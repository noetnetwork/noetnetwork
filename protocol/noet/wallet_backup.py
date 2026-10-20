"""Native-only encrypted wallet backup. No destination path is accepted from a browser."""
import os
from pathlib import Path
import stat
import tempfile
from .rpc import verify_network


def choose_destination(network):
    import tkinter as tk
    from tkinter import filedialog
    root=tk.Tk();root.withdraw()
    try:
        value=filedialog.asksaveasfilename(parent=root,title='Save encrypted '+network+' backup',
            initialfile='noet-encrypted-wallet.dat',defaultextension='.dat',filetypes=[('Encrypted wallet backup','*.dat')])
        return Path(value) if value else None
    finally:root.destroy()


def backup_wallet(rpc,genesis,network,choose=choose_destination):
    target=choose(network)
    if target is None:return {'cancelled':True,'saved':False}
    target=Path(target)
    if not target.is_absolute() or not target.name or target.is_symlink() or target.exists():
        raise ValueError('Choose a new absolute backup filename')
    parent=target.parent.resolve(strict=True)
    if not parent.is_dir():raise ValueError('Choose an existing backup directory')
    target=parent/target.name
    verify_network(rpc,genesis)
    info=rpc('getwalletinfo')
    if info.get('private_keys_enabled') is not True or info.get('unlocked_until') != 0:
        raise ValueError('Lock an encrypted signing wallet before backing it up')
    # The node writes inside a private sibling directory. Publish by hard link
    # only after checking permissions; never overwrite an existing backup.
    with tempfile.TemporaryDirectory(prefix='.noet-wallet-backup-',dir=parent) as directory:
        candidate=Path(directory)/'wallet.dat'
        rpc('backupwallet',str(candidate))
        fd=os.open(candidate,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            record=os.fstat(stream.fileno())
            if not stat.S_ISREG(record.st_mode) or record.st_uid!=os.getuid() or record.st_size<=0:
                raise ValueError('Node did not create a regular private backup')
            os.fchmod(stream.fileno(),0o600)
            os.fsync(stream.fileno())
            verify_network(rpc,genesis)
            current=candidate.lstat()
            if (current.st_dev,current.st_ino)!=(record.st_dev,record.st_ino):raise ValueError('Backup file changed')
            os.link(candidate,target,follow_symlinks=False)
    return {'saved':True,'encrypted':True,'testCoinsOnly':True,'genesis':genesis}
