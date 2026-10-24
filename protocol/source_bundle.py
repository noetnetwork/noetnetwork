#!/usr/bin/env python3
"""Export pinned, reviewed source inputs without copying checkout build debris.

Produces an unsigned development Docker context, not a reproducible binary claim.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parent
COMPONENTS=('services/core-node/bcore','shared-utils/liboqs','shared-utils/secp256k1-zkp')


def git(source,*args):
    return subprocess.check_output(['git','-C',str(source),*args])


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1<<20),b''):
            value.update(block)
    return value.hexdigest()


def export(source,destination,commit,patch=None):
    """Validate live tracked changes, then reconstruct from Git objects + patch."""
    if git(source,'rev-parse','HEAD').decode().strip()!=commit:
        raise ValueError('Source commit differs from release lock')
    actual=git(source,'diff','--no-ext-diff','--binary','--ignore-submodules=all','HEAD').strip()
    expected=patch.read_bytes().strip() if patch else b''
    if actual!=expected:
        raise ValueError('Tracked source differs from the reviewed patch')
    destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryFile() as archive:
        subprocess.run(['git','-C',str(source),'archive','--format=tar',commit],stdout=archive,check=True)
        archive.seek(0)
        with tarfile.open(fileobj=archive,mode='r:') as source_tar:
            # Reject traversal, devices, and links outside the exported source.
            source_tar.extractall(destination,filter='data')
    if patch:
        subprocess.run(['git','-C',str(destination),'apply','--check',str(patch.resolve())],check=True,capture_output=True)
        subprocess.run(['git','-C',str(destination),'apply',str(patch.resolve())],check=True,capture_output=True)
    return {'commit':commit,'patchSha256':digest(patch) if patch else None}


def inventory(root):
    entries=[]
    for path in sorted(root.rglob('*')):
        name=path.relative_to(root).as_posix()
        mode=path.lstat().st_mode
        if stat.S_ISLNK(mode):
            target=os.readlink(path)
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError('Source link leaves the build context')
            entries.append({'path':name,'type':'symlink','target':target})
        elif stat.S_ISREG(mode):
            entries.append({'path':name,'type':'file','size':path.stat().st_size,'sha256':digest(path),'executable':bool(mode&0o111)})
        elif not stat.S_ISDIR(mode):
            raise ValueError('Special file in source export')
    return entries


def pack(root,output):
    # Exclusive creation protects existing release artifacts. Timestamp/ownership
    # are normalized; source permissions preserve only executable vs regular.
    with Path(output).open('xb') as stream:
        with tarfile.open(fileobj=stream,mode='w',format=tarfile.PAX_FORMAT) as archive:
            for path in sorted(root.rglob('*')):
                info=archive.gettarinfo(str(path),path.relative_to(root).as_posix())
                info.uid=info.gid=info.mtime=0
                info.uname=info.gname=''
                info.pax_headers={}
                info.mode=0o755 if info.isdir() or info.mode&0o111 else 0o644
                if info.isfile():
                    with path.open('rb') as source:archive.addfile(info,source)
                else:archive.addfile(info)
    return digest(Path(output))


def build(source,output,lock=None):
    lock=lock or json.loads((ROOT/'upstream.lock.json').read_text())
    with tempfile.TemporaryDirectory(prefix='noet-source-') as folder:
        context=Path(folder)/'context'
        inputs={'root':export(source,context,lock['commit'],ROOT/'patches/noet-miner.patch')}
        for relative in COMPONENTS:
            inputs[relative]=export(source/relative,context/relative,lock['components'][relative],ROOT/'patches/noet-lab.patch' if relative.endswith('/bcore') else None)
        (context/'Dockerfile').write_bytes((ROOT/'docker/Node.Dockerfile').read_bytes())
        record={'format':'noet-source-context-v1','purpose':'isolated laboratory development build','productionReady':False,'inputs':inputs,'files':inventory(context)}
        (context/'NOET-SOURCE-PROVENANCE.json').write_text(json.dumps(record,sort_keys=True,indent=2)+'\n')
        return pack(context,output)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'.upstream')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    print(build(args.source.resolve(),args.out))


if __name__=='__main__':main()
