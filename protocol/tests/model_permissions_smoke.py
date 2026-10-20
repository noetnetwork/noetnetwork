"""Actual Linux ownership/UID/permission acceptance in a disposable test container."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def check(root):
    from noet.model_runtime import require_snapshot_protection
    require_snapshot_protection(root,[{'path':'weights'}])
    for action in (lambda:(root/'weights').open('ab'),
                   lambda:(root/'weights').chmod(0o666),
                   lambda:root.rename(root.with_name(root.name+'-moved'))):
        try: action()
        except PermissionError: pass
        else: raise AssertionError('Unprivileged loader could mutate protected files')


def run():
    if os.getuid()!=0:raise ValueError('Fixture setup requires root in a disposable Linux host')
    root=Path(tempfile.mkdtemp(prefix='noet-permissions-',dir='/opt'))
    root.chmod(0o755);file=root/'weights';file.write_bytes(b'fixture');file.chmod(0o444)
    env={**os.environ,'NOET_MODEL_PROTECTION':'root-owned'}
    base=['setpriv','--reuid=10001','--regid=10001','--clear-groups','--bounding-set=-all','--inh-caps=-all','--ambient-caps=-all']
    def invoke(protected=True):
        return subprocess.run(base+(['--no-new-privs'] if protected else [])+
            [sys.executable,str(Path(__file__).resolve()),str(root)],env=env,capture_output=True,text=True)
    cases=[]
    try:
        result=invoke();assert result.returncode==0,result.stderr;cases.append('protected-loader-cannot-write-chmod-or-rename')
        file.chmod(0o666);assert invoke().returncode!=0;cases.append('writable-file-refused');file.chmod(0o444)
        os.chown(file,10001,10001);assert invoke().returncode!=0;cases.append('worker-owned-file-refused');os.chown(file,0,0)
        root.chmod(0o777);assert invoke().returncode!=0;cases.append('writable-ancestor-refused');root.chmod(0o755)
        target=root/'target';file.rename(target);file.symlink_to('target')
        assert invoke().returncode!=0;cases.append('symlink-refused');file.unlink();target.rename(file)
        assert invoke(False).returncode!=0;cases.append('privilege-regain-not-disabled-refused')
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),str(root)],env=env,capture_output=True,text=True)
        assert result.returncode!=0;cases.append('root-loader-refused')
        print(json.dumps({'status':'passed','cases':cases,'productionReady':False},indent=2))
    finally:shutil.rmtree(root)


if __name__=='__main__':
    if len(sys.argv)==2:check(Path(sys.argv[1]))
    else:run()
