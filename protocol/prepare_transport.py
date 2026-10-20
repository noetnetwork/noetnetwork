#!/usr/bin/env python3
"""Package the CPU-only TLS relay without certificates, keys or deployment config."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
from noet.secure_transport import regular_file
from source_bundle import pack

ROOT = Path(__file__).resolve().parent


def prepare(base_image, output):
    if not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}', base_image):
        raise ValueError('A reviewed Python 3.12+ parent image pinned by digest is required')
    with tempfile.TemporaryDirectory(prefix='noet-transport-build-') as folder:
        context = Path(folder)
        (context/'noet').mkdir()
        inventory = {}
        for name in ('__init__.py', 'secure_transport.py', 'verifier_transport.py'):
            data = regular_file(ROOT/'noet'/name)
            target = context/'noet'/name
            target.write_bytes(data)
            inventory['noet/'+name] = hashlib.sha256(data).hexdigest()
        (context/'Dockerfile').write_text('FROM '+base_image+'''
WORKDIR /opt/noet
COPY noet /opt/noet/noet
ENV PYTHONPATH=/opt/noet PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
ENTRYPOINT ["python3", "-m", "noet.secure_transport"]
CMD []
''')
        (context/'NOET-BUILD-INPUTS.json').write_text(json.dumps({
            'parentImage':base_image, 'files':inventory, 'productionReady':False,
            'purpose':'Private verifier mutual-TLS relay; multi-host acceptance required',
            'containsCredentials':False,
        }, indent=2)+'\n')
        return pack(context, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-image', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.base_image, args.out))
