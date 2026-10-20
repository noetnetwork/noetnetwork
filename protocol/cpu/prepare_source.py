#!/usr/bin/env python3
"""Assemble pinned CPU inference-proof sources. Does not build or run a miner."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from source_bundle import export, git, inventory, pack


def prepare(output):
    source = ROOT / '.upstream'
    lock = json.loads((ROOT / 'upstream.lock.json').read_text())
    if git(source, 'rev-parse', 'HEAD').decode().strip() != lock['commit']:
        raise ValueError('Source differs from the locked commit')
    if git(source, 'diff', '--no-ext-diff', '--binary', '--ignore-submodules=all', 'HEAD').strip() != (ROOT / 'patches/noet-miner.patch').read_bytes().strip():
        raise ValueError('Source differs from the reviewed runtime patch')
    with tempfile.TemporaryDirectory(prefix='noet-cpu-source-') as folder:
        temporary = Path(folder)
        context = temporary / 'context'
        engine = context / 'llama'
        record = export(source / 'services/miner-api/llama.cpp', engine,
                        lock['components']['services/miner-api/llama.cpp'],
                        ROOT / 'patches/noet-engine.patch')
        with tempfile.TemporaryFile() as stream:
            subprocess.run(['git', '-C', str(source), 'archive', lock['commit'],
                            'shared-utils/pow-utils', 'shared-utils/fb-schemas'], stdout=stream, check=True)
            stream.seek(0)
            with tarfile.open(fileobj=stream) as archive:
                archive.extractall(temporary / 'shared', filter='data')
        helpers = temporary / 'shared/shared-utils/pow-utils'
        target = engine / 'tools/server'
        for path in sorted(helpers.iterdir()):
            if path.suffix in ('.h', '.cpp') and path.is_file() and not path.is_symlink():
                shutil.copyfile(path, target / path.name)
        for name in ('bcore_compat', 'coremath'):
            shutil.copytree(helpers / name, target / name)
        schemas = temporary / 'shared/shared-utils/fb-schemas'
        for name in ('proof.fbs', 'blockheader.fbs', 'validation.fbs'):
            shutil.copyfile(schemas / name, target / name)
        shutil.copytree(schemas / 'proof', context / 'proof')
        record = {'format':'noet-cpu-source-v1', 'engine':record, 'helpersCommit':lock['commit'],
                  'productionReady':False, 'purpose':'CPU inference-evidence experiment; not consensus acceptance',
                  'files':inventory(context)}
        (context / 'NOET-CPU-SOURCE.json').write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
        return pack(context, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    print(prepare(parser.parse_args().out))
