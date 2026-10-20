#!/usr/bin/env python3
"""Build pinned native VDF code and test real proofs, without GPUs or network access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(out, include_dir=None, library_dir=None):
    if out.exists() or out.is_symlink():
        raise FileExistsError(out)
    lock = json.loads((ROOT/'upstream.lock.json').read_text())
    source = ROOT/'.upstream'
    def git(*args):
        return subprocess.check_output(['git','-C',str(source),*args])
    if git('rev-parse','HEAD').decode().strip() != lock['commit']:
        raise ValueError('Source differs from locked commit')
    # Compile the immutable Git objects, never local headers or build debris.
    prefix = 'shared-utils/chiavdf'
    with tempfile.TemporaryDirectory(prefix='noet-vdf-check-') as folder:
        work = Path(folder)
        with tempfile.TemporaryFile() as stream:
            subprocess.run(['git','-C',str(source),'archive',lock['commit'],prefix],stdout=stream,check=True)
            stream.seek(0)
            with tarfile.open(fileobj=stream) as archive:
                archive.extractall(work,filter='data')
        native = work/prefix/'src'
        flags = ['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer']
        obj = work/'lzcnt.o'; binary = work/'acceptance'
        subprocess.run(['clang',*flags,'-c',str(native/'refcode/lzcnt.c'),'-o',str(obj)],check=True,timeout=60,capture_output=True)
        command = ['clang++','-std=c++17',*flags,'-I'+str(native)]
        if include_dir: command.append('-I'+str(include_dir))
        command += [str(ROOT/'tests/native/vdf_acceptance.cpp'),str(obj)]
        if library_dir: command.append('-L'+str(library_dir))
        command += ['-lgmpxx','-lgmp','-pthread','-o',str(binary)]
        subprocess.run(command,check=True,timeout=60,capture_output=True)
        started = time.monotonic()
        result = subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=60,
            env={**os.environ,'ASAN_OPTIONS':'halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=1'})
        report = json.loads(result.stdout)
        if report.get('validProofAccepted') is not True or report.get('invalidCasesRejected') != 204 or report.get('proofBytes') != 200:
            raise ValueError('Native acceptance report is incomplete')
        report.update(sourceCommit=lock['commit'],platform=platform.system(),architecture=platform.machine(),
            compiler=subprocess.check_output(['clang++','--version'],text=True).splitlines()[0],
            binarySha256=digest(binary),testSourceSha256=digest(ROOT/'tests/native/vdf_acceptance.cpp'),
            sanitizers=['address','undefined'],elapsedSeconds=round(time.monotonic()-started,3),
            scope='native VDF generation and verification; not AI inference proofs or production mining')
        out.parent.mkdir(parents=True,exist_ok=True)
        with out.open('x') as target: json.dump(report,target,indent=2);target.write('\n')
        print(json.dumps({key:value for key,value in report.items() if key!='proofHex'},indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--include-dir',type=Path)
    parser.add_argument('--library-dir',type=Path)
    args = parser.parse_args()
    run(args.out,args.include_dir,args.library_dir)
