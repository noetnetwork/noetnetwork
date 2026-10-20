import os
from pathlib import Path
import stat
import tempfile
import unittest
from noet.wallet_backup import backup_wallet
from noet.wallet_network import PROFILES

class WalletBackupTests(unittest.TestCase):
    def setUp(self):
        self.calls=[];self.genesis=PROFILES['lab']['genesis'];self.locked=0;self.unsafe=False
    def rpc(self,method,*args):
        self.calls.append((method,args))
        if method=='getblockhash':return self.genesis
        if method=='getblockchaininfo':return {'chain':'tensor-reg'}
        if method=='getwalletinfo':return {'private_keys_enabled':True,'unlocked_until':self.locked}
        if method=='backupwallet':
            path=Path(args[0])
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode),0o700)
            if self.unsafe:path.symlink_to('/dev/null')
            else:path.write_bytes(b'synthetic encrypted backup fixture');path.chmod(0o644)
            return None
        raise AssertionError(method)
    def test_cancel_never_calls_node(self):
        self.assertEqual(backup_wallet(self.rpc,self.genesis,'NOET laboratory',lambda _:None),{'cancelled':True,'saved':False})
        self.assertEqual(self.calls,[])
    def test_publishes_private_file_without_path_or_contents_in_response(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'wallet.dat'
            result=backup_wallet(self.rpc,self.genesis,'NOET laboratory',lambda _:target)
            self.assertTrue(result['saved']);self.assertTrue(result['encrypted'])
            self.assertEqual(target.read_bytes(),b'synthetic encrypted backup fixture')
            self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o600)
            self.assertNotIn(folder,str(result));self.assertEqual(list(Path(folder).iterdir()),[target])
    def test_existing_file_or_symlink_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'wallet.dat';target.write_text('retain')
            with self.assertRaises(ValueError):backup_wallet(self.rpc,self.genesis,'NOET laboratory',lambda _:target)
            self.assertEqual(target.read_text(),'retain');self.assertEqual(self.calls,[])
            linked=Path(folder)/'link';linked.symlink_to(target)
            with self.assertRaises(ValueError):backup_wallet(self.rpc,self.genesis,'NOET laboratory',lambda _:linked)
    def test_unlocked_wallet_or_node_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'wallet.dat';self.locked=1
            with self.assertRaises(ValueError):backup_wallet(self.rpc,self.genesis,'NOET laboratory',lambda _:target)
            self.assertFalse(target.exists())
            self.locked=0;self.unsafe=True
            with self.assertRaises(OSError):backup_wallet(self.rpc,self.genesis,'NOET laboratory',lambda _:target)
            self.assertEqual(list(Path(folder).iterdir()),[])
    def test_racing_existing_destination_is_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'wallet.dat'
            def rpc(method,*args):
                result=self.rpc(method,*args)
                if method=='backupwallet':target.write_text('concurrent file')
                return result
            with self.assertRaises(FileExistsError):backup_wallet(rpc,self.genesis,'NOET laboratory',lambda _:target)
            self.assertEqual(target.read_text(),'concurrent file')
