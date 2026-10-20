#!/usr/bin/env python3
"""Assemble the self-contained NOET miner build context.

The context holds only reviewed sources: the patched inference engine (from
cpu/prepare_source.py), the pinned chiavdf sources for the VDF binding, the
proof generator and the gateway client. No keys, wallets or credentials. Both
the Dockerfile and setup.sh build from exactly this directory.

  python3 protocol/miner/prepare.py --out /tmp/noet-miner
  docker build -t noet-miner /tmp/noet-miner
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
FILES = {
    'miner/noet_miner.py': 'app/noet_miner.py',
    'realwork/generate.py': 'app/generate.py',
    'cpu/exercise.py': 'app/exercise.py',
    'noet/__init__.py': 'app/noet/__init__.py',
    'noet/vdf.py': 'app/noet/vdf.py',
    'noet/model_artifacts.py': 'app/noet/model_artifacts.py',
    'cpu/fetch_model.py': 'app/fetch_model.py',
    'cpu/qwen3-06b.candidate.json': 'app/qwen3-06b.candidate.json',
    'miner/Dockerfile': 'Dockerfile',
    'miner/setup.sh': 'setup.sh',
    'miner/convert.sh': 'convert.sh',
    'miner/README.md': 'README.md',
    'miner/LICENSE-NOTICE.md': 'LICENSE-NOTICE.md',
}

# Licence texts for everything compiled or converted into the image (from protocol/.upstream).
LICENSES = {
    'LICENSE': 'licenses/Apache-2.0.txt',  # Qwen3-0.6B weights and chiavdf share this text
    'services/miner-api/llama.cpp/LICENSE': 'licenses/llama.cpp-MIT.txt',
    'services/miner-api/llama.cpp/vendor/cpp-httplib/LICENSE': 'licenses/cpp-httplib-MIT.txt',
    'services/miner-api/llama.cpp/licenses/LICENSE-jsonhpp': 'licenses/nlohmann-json-MIT.txt',
    'shared-utils/chiavdf/LICENSE': 'licenses/chiavdf-Apache-2.0.txt',
    'shared-utils/chiavdf/src/uint128_t/LICENSE': 'licenses/uint128_t-MIT.txt',
}


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def chiavdf(out):
    lock = json.loads((ROOT / 'upstream.lock.json').read_text())
    with tempfile.TemporaryFile() as stream:
        subprocess.run(['git', '-C', str(ROOT / '.upstream'), 'archive', lock['commit'], 'shared-utils/chiavdf/src'],
                       stdout=stream, check=True)
        stream.seek(0)
        with tarfile.open(fileobj=stream) as archive:
            archive.extractall(out, filter='data')
    shutil.move(out / 'shared-utils/chiavdf', out / 'chiavdf')
    (out / 'shared-utils').rmdir()


def prepare(out):
    from cpu.prepare_source import prepare as engine_source
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    engine_source(out / 'engine.tar')
    chiavdf(out)
    for source, target in FILES.items():
        (out / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, out / target)
    for source, target in LICENSES.items():
        (out / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / '.upstream' / source, out / target)
    # generate.py looks for the exercise module next to itself, never in /cpu.
    generate = out / 'app/generate.py'
    generate.write_text(generate.read_text().replace("sys.path.insert(0, '/cpu')\n", ''))
    (out / 'setup.sh').chmod(0o755)
    (out / 'convert.sh').chmod(0o755)
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file())
    manifest = {'format': 'noet-miner-context-v1', 'files': {name: digest(out / name) for name in files}}
    (out / 'context.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, required=True)
    result = prepare(parser.parse_args().out)
    print(json.dumps({'files': len(result['files'])}))
