#!/usr/bin/env python3
"""Prepare a guarded mining-backend Docker context. Does not build or rent GPUs."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
from source_bundle import pack

ROOT=Path(__file__).resolve().parent
SUPERVISOR='services/miner-api/vllm_supervisor.py'


def prepare(base_image,output):
    if not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}',base_image):
        raise ValueError('An independently reviewed parent image pinned by digest is required')
    source=ROOT/'.upstream';lock=json.loads((ROOT/'upstream.lock.json').read_text())
    def git(*args):return subprocess.check_output(['git','-C',str(source),*args])
    if git('rev-parse','HEAD').decode().strip()!=lock['commit']:raise ValueError('Source differs from the locked commit')
    if git('diff','--no-ext-diff','--binary','--ignore-submodules=all','HEAD').strip()!=(ROOT/'patches/noet-miner.patch').read_bytes().strip():
        raise ValueError('Source differs from the reviewed miner patch')
    with tempfile.TemporaryDirectory(prefix='noet-miner-context-') as folder:
        context=Path(folder)
        destination=context/SUPERVISOR;destination.parent.mkdir(parents=True)
        destination.write_bytes(git('show',lock['commit']+':'+SUPERVISOR))
        subprocess.run(['git','-C',str(context),'apply','--include='+SUPERVISOR,str(ROOT/'patches/noet-miner.patch')],check=True,capture_output=True)
        destination.rename(context/'vllm_supervisor.py')
        (context/'noet').mkdir()
        for name in ('__init__.py','model_runtime.py','model_artifacts.py','rpc.py'):
            (context/'noet'/name).write_bytes((ROOT/'noet'/name).read_bytes())
        (context/'Dockerfile').write_text('FROM '+base_image+'''\nWORKDIR /opt/noet
COPY noet /opt/noet/noet
COPY vllm_supervisor.py /opt/noet/vllm_supervisor.py
ENV PYTHONPATH=/opt/noet:/app VLLM_ENABLE_POW=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
USER 1000:1000
ENTRYPOINT ["python3", "/opt/noet/vllm_supervisor.py"]
CMD []
''')
        (context/'NOET-BUILD-INPUTS.json').write_text(json.dumps({'parentImage':base_image,'sourceCommit':lock['commit'],'productionReady':False,'purpose':'guarded development mining backend; GPU acceptance required'},indent=2)+'\n')
        return pack(context,output)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-image',required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    print(prepare(args.base_image,args.out))
