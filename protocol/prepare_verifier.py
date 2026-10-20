#!/usr/bin/env python3
"""Prepare a guarded verifier image context without downloading models or renting GPUs."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
from source_bundle import pack

ROOT = Path(__file__).resolve().parent
FILES = {
    'services/verification-api/src/detector_gate.py': 'src/detector_gate.py',
    'services/verification-api/src/zmq_send_broker.py': 'src/zmq_send_broker.py',
    'services/verification-api/src/main.py': 'src/main.py',
    'services/verification-api/src/proof_verifier.py': 'src/proof_verifier.py',
    'services/verification-api/src/model_verifier.py': 'src/model_verifier.py',
    'services/verification-api/src/utils/shared_utils.py': 'src/utils/shared_utils.py',
}


def prepare(base_image, output):
    if not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}', base_image):
        raise ValueError('An independently reviewed parent image pinned by digest is required')
    source = ROOT / '.upstream'
    lock = json.loads((ROOT / 'upstream.lock.json').read_text())
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), *args])
    if git('rev-parse', 'HEAD').decode().strip() != lock['commit']:
        raise ValueError('Source differs from the locked commit')
    patch_path = ROOT / 'patches/noet-miner.patch'
    if git('diff', '--no-ext-diff', '--binary', '--ignore-submodules=all', 'HEAD').strip() != patch_path.read_bytes().strip():
        raise ValueError('Source differs from the reviewed runtime patch')
    with tempfile.TemporaryDirectory(prefix='noet-verifier-context-') as folder:
        context = Path(folder)
        for original, target in FILES.items():
            destination = context / original
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(git('show', lock['commit'] + ':' + original))
            marker = ('diff --git a/' + original + ' b/' + original).encode()
            if marker in patch_path.read_bytes():
                subprocess.run(['git', '-C', str(context), 'apply', '--include=' + original, str(patch_path)], check=True, capture_output=True)
            output_path = context / target
            output_path.parent.mkdir(parents=True, exist_ok=True)
            destination.rename(output_path)
        (context / 'noet').mkdir()
        for name in ('__init__.py', 'model_runtime.py', 'model_artifacts.py', 'verifier_entry.py', 'vdf.py', 'verifier_transport.py', 'admission.py'):
            (context / 'noet' / name).write_bytes((ROOT / 'noet' / name).read_bytes())
        (context / 'Dockerfile').write_text('FROM ' + base_image + '''
COPY src/ /app/src/
COPY noet/ /opt/noet/noet/
WORKDIR /app/src
ENV PYTHONPATH=/opt/noet:/app/src:/app HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
USER 1000:1000
ENTRYPOINT ["python3", "-m", "noet.verifier_entry"]
CMD []
''')
        (context / 'NOET-BUILD-INPUTS.json').write_text(json.dumps({
            'parentImage': base_image, 'sourceCommit': lock['commit'],
            'productionReady': False,
            'purpose': 'single approved model development verifier; GPU acceptance required',
        }, indent=2) + '\n')
        return pack(context, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-image', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.base_image, args.out))
