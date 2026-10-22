#!/usr/bin/env python3
"""Assemble the Fly.io build context for one hosted node (private-work, testnet or mainnet).

Sources come from the reviewed exporters: the laboratory-patched native node,
the private-work overlay (separate genesis, `nwrk` addresses, real external
verification only) and the explorer/relay modules. Credentials are never part
of the context; they are supplied as platform secrets at runtime.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from realwork.prepare import overlay, PROFILE, MODEL, REVISION
from testnet import prepare as testnet
import payout_proof
from source_bundle import export, git, inventory

NATIVE = ('services/core-node/bcore', 'shared-utils/liboqs', 'shared-utils/secp256k1-zkp')
SHARED = ('shared-utils/chiavdf', 'shared-utils/fb-schemas')
RUNTIME = ('__init__.py', 'indexer.py', 'rpc.py', 'serve.py', 'faucet.py', 'gateway.py', 'tasks.py',
           'open_dataset.py', 'secure_transport.py', 'verifier_transport.py', 'blockwork.py', 'vocab/qwen3-0.6b-vocab.json.gz')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prepare(out, profile_name='private-work', test=False):
    """test: the overlay's test variant (mock validation) of the testnet or mainnet
    consensus, for local checks such as testnet/switch_check.py. Never deployed."""
    if profile_name not in ('private-work', 'testnet', 'mainnet'):
        raise ValueError('Unknown network profile')
    if test and profile_name == 'private-work':
        raise ValueError('The test variant exists for the testnet and mainnet overlays only')
    net, identity = testnet.TESTNET, {'profile': profile_name}
    if profile_name == 'mainnet':
        # Refuses until genesis, real signers and the pinned genesis hash exist (mainnet/README.md).
        net = testnet.mainnet()
        spec = json.loads((ROOT / 'mainnet/network.json').read_text())
        if not isinstance(spec.get('genesisHash'), str) or len(spec['genesisHash']) != 64:
            raise ValueError('Pin genesisHash in mainnet/network.json from a test build first')
        identity = {'profile': 'mainnet', 'genesis': spec['genesisHash'], 'hrp': net['hrp'],
                    'name': net['profile'], 'p2pPort': net['port'], 'seeds': net['seeds']}
    out = Path(out)
    if out.exists():
        raise ValueError('Use a new output directory')
    lock = json.loads((ROOT / 'upstream.lock.json').read_text())
    source = ROOT / '.upstream'
    src = out / 'src'
    inputs = {}
    with tempfile.TemporaryDirectory(prefix='noet-fly-root-') as folder:
        umbrella = Path(folder) / 'root'
        inputs['root'] = export(source, umbrella, lock['commit'], ROOT / 'patches/noet-miner.patch')
        for relative in SHARED:
            shutil.copytree(umbrella / relative, src / relative, symlinks=True)
    for relative in NATIVE:
        patch = ROOT / 'patches/noet-lab.patch' if relative.endswith('/bcore') else None
        inputs[relative] = export(source / relative, src / relative, lock['components'][relative], patch)
    core = src / 'services/core-node/bcore'
    files = {}
    if profile_name in ('testnet', 'mainnet'):
        # Release variant: real external verification only, proposed economics.
        sources = {p: (core / p).read_text() for p in testnet.FILES}
        result = testnet.overlay(sources, test=test, net=net)
        for path, updated in result.items():
            (core / path).write_text(updated)
        overlay_profile = testnet.profile(sources, result, test=test, net=net)
        files = overlay_profile['files']
        profile = {'format': net['profile'], 'model': testnet.MODEL + '@' + testnet.REVISION,
                   **({'modelSwitch': overlay_profile['modelSwitch']} if 'modelSwitch' in overlay_profile else {}),
                   **({'variant': 'test'} if test else {}),
                   'sourceCommit': lock['components'][NATIVE[0]],
                   'labPatchSha256': digest((ROOT / 'patches/noet-lab.patch').read_bytes()), 'files': files,
                   'productionReady': False, 'registeredDifficulty': 1000000,
                   'purpose': ('Local consensus test node: mock validation, for no network' if test else
                               'Hosted public testnet node; proposed economics; test coins have no value'
                               if profile_name == 'testnet' else 'Hosted mainnet node')}
    else:
        paths = ('src/kernel/chainparams.cpp', 'src/init.cpp')
        before = [(core / p).read_text() for p in paths]
        after = overlay(*before)
        for path, original, updated in zip(paths, before, after):
            (core / path).write_text(updated)
            files[path] = {'beforeSha256': digest(original.encode()), 'afterSha256': digest(updated.encode())}
        profile = {'format': PROFILE, 'model': MODEL + '@' + REVISION, 'sourceCommit': lock['components'][NATIVE[0]],
                   'labPatchSha256': digest((ROOT / 'patches/noet-lab.patch').read_bytes()), 'files': files,
                   'productionReady': False, 'registeredDifficulty': 1000000,
                   'purpose': 'Hosted private-work development network; inherited test economics; unapproved research model'}
    # Both networks: work units carry their coinbase and merkle branch (RPC output only).
    original = (core / payout_proof.FILE).read_text()
    updated = payout_proof.overlay(original)
    (core / payout_proof.FILE).write_text(updated)
    files[payout_proof.FILE] = {'beforeSha256': digest(original.encode()), 'afterSha256': digest(updated.encode())}
    (out / 'profile.json').write_text(json.dumps(profile, indent=2) + '\n')
    runtime = out / 'runtime'
    (runtime / 'noet').mkdir(parents=True)
    for name in RUNTIME:
        (runtime / 'noet' / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / 'noet' / name, runtime / 'noet' / name)
    shutil.copytree(Path(__file__).parent / 'noet_node', runtime / 'noet_node',
                    ignore=shutil.ignore_patterns('__pycache__'))
    (runtime / 'network.json').write_text(json.dumps(identity) + '\n')
    shutil.copyfile(Path(__file__).parent / 'Dockerfile', out / 'Dockerfile')
    toml = {'testnet': 'fly.testnet.toml', 'mainnet': 'fly.mainnet.toml'}.get(profile_name, 'fly.toml')
    shutil.copyfile(Path(__file__).parent / toml, out / 'fly.toml')
    (out / '.dockerignore').write_text('**/.git\n**/__pycache__\n')
    record = {'format': 'noet-fly-node-context-v1', 'productionReady': False, 'inputs': inputs,
              'profile': profile, 'files': inventory(out)}
    (out / 'NOET-SOURCE-PROVENANCE.json').write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
    return {'files': len(record['files']), 'overlay': {k: v['afterSha256'] for k, v in files.items()}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--profile', choices=('private-work', 'testnet', 'mainnet'), default='private-work')
    parser.add_argument('--test-variant', action='store_true', help='mock validation, for local consensus checks only')
    args = parser.parse_args()
    print(json.dumps(prepare(args.out, args.profile, args.test_variant), indent=2))
