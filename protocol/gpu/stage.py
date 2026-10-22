#!/usr/bin/env python3
"""Assemble the credential-free input set for one GPU research host.

Every archive comes from the reviewed exporters, so the host builds exactly the
patched sources in this checkout. Large model files are verified against their
pinned digests and staged separately. No keys, wallets, .env files or account
credentials are included.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
GGUF_SHA256 = '8fa11f4d815167c4f6e3c91b25aebde25d8346a8f11228e744016592532b0700'
MANIFEST_SHA256 = 'c18e242c1f4c2273d89a27a226856ad1a6623a7a0c22839495b45371eefa3ae4'
CHECKS = {'cpu/fix_object_bindings.py': 'fix_object_bindings.py', 'cpu/exercise.py': 'exercise.py',
          'cpu/verify_current.py': 'verify_current.py', 'realwork/generate.py': 'generate.py',
          'realwork/verify.py': 'verify.py', 'miner/noet_miner.py': 'noet_miner.py'}
GPU_CHECKS = ('audit.py', 'compare_logits.py', 'diagnose_candidates.py', 'native.py', 'peer.py',
              'replay.py', 'summarize.py', 'test_response.py', 'response_test.cpp', 'wallet_acceptance.py',
              'miner.py', 'live.py')


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_profile(name):
    profiles = json.loads((ROOT / 'models/profiles.json').read_text())
    if name not in profiles:
        raise ValueError('Unknown model profile: ' + name)
    profile = dict(profiles[name])
    raw = (ROOT / profile['manifest']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != profile['manifestSha256']:
        raise ValueError('Candidate manifest differs from its profile')
    return profile, raw


def stage(out, builder, model=None, gguf=None, profile_name='qwen3-0.6b', switch_profile=None, switch_height=None):
    """switch_profile/switch_height: rehearse a model switch (design/MODEL-SWITCH.md)
    on the private chain; both models are then fetched and converted on the host."""
    from cpu.prepare_source import prepare as engine_source
    from prepare_verifier_cpu import prepare as verifier_source
    from realwork.prepare import prepare as private_work
    from source_bundle import build as native_source
    from noet.model_artifacts import verify
    profile, candidate = load_profile(profile_name)
    switch = None
    if switch_profile:
        switch_spec, switch_candidate = load_profile(switch_profile)
        if switch_profile == profile_name or type(switch_height) is not int or not 2 <= switch_height < 100000:
            raise ValueError('A switch rehearsal needs another model and a height from 2')
        switch = {'model': switch_spec['model'], 'revision': switch_spec['revision'], 'height': switch_height}
    mainnet_model = profile_name == 'qwen3-0.6b' and not switch
    if not mainnet_model and (model or gguf):
        raise ValueError('A candidate model is fetched and converted on the host (model_session.sh)')
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    if mainnet_model:
        # The pinned model is uploaded after a local check, as before.
        if digest(gguf) != GGUF_SHA256:
            raise ValueError('Converted model differs from the pinned research artifact')
        verify(model, candidate, MANIFEST_SHA256)
    engine_source(out / 'engine.tar')
    verifier_source(builder, out / 'verifier.tar')
    native_source(ROOT / '.upstream', out / 'native.tar')
    private_work(builder if '@' in builder else 'noet-native-builder@' + builder, out / 'private-work.tar',
                 profile['model'], profile['revision'], switch)
    for name in ('build.sh', 'build_native.sh', 'run.sh', 'session.sh', 'model_session.sh', 'prepare_live.sh'):
        shutil.copyfile(ROOT / 'gpu' / name, out / name)
    checks = out / 'checks'
    checks.mkdir()
    for source, name in CHECKS.items():
        shutil.copyfile(ROOT / source, checks / name)
    for name in GPU_CHECKS:
        if (ROOT / 'gpu' / name).exists():
            shutil.copyfile(ROOT / 'gpu' / name, checks / name)
    shutil.copyfile(ROOT / 'noet/rpc.py', checks / 'noet_rpc.py')
    # Harnesses (indexer, wallet, peer) import the full host package and the
    # laboratory profile next to it; session.sh installs these after the build.
    package = out / 'pkg/noet'
    package.mkdir(parents=True)
    for module in sorted((ROOT / 'noet').glob('*.py')):
        shutil.copyfile(module, package / module.name)
    shutil.copyfile(ROOT / 'lab.network.json', out / 'pkg/lab.network.json')
    relay = out / 'relay/noet'
    relay.mkdir(parents=True)
    for name in ('__init__.py', 'secure_transport.py', 'verifier_transport.py'):
        shutil.copyfile(ROOT / 'noet' / name, relay / name)
    manifest_file = Path(profile['manifest']).name
    (out / manifest_file).write_bytes(candidate)
    if not mainnet_model:
        # Host-side fetch and conversion use the miner's own converter.
        shutil.copyfile(ROOT / 'miner/convert.sh', out / 'convert.sh')
        shutil.copyfile(ROOT / 'gpu/plan_host.py', out / 'plan_host.py')
        app = out / 'app/noet'
        app.mkdir(parents=True)
        shutil.copyfile(ROOT / 'cpu/fetch_model.py', out / 'app/fetch_model.py')
        for name in ('__init__.py', 'model_artifacts.py'):
            shutil.copyfile(ROOT / 'noet' / name, app / name)
        record = {'name': profile_name, 'id': profile['model'] + '@' + profile['revision'], 'gguf': profile['gguf'],
                  'ggufSha256': profile['ggufSha256'], 'privateGenesis': None, 'manifestFile': manifest_file,
                  'manifestSha256': profile['manifestSha256']}
        if switch:
            switch_manifest = Path(switch_spec['manifest']).name
            (out / switch_manifest).write_bytes(switch_candidate)
            record['switch'] = {'name': switch_profile, 'height': switch_height,
                                'id': switch_spec['model'] + '@' + switch_spec['revision'],
                                'gguf': switch_spec['gguf'], 'ggufSha256': switch_spec['ggufSha256'],
                                'manifestFile': switch_manifest, 'manifestSha256': switch_spec['manifestSha256']}
        (out / 'model.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    groups = {'inputs.sha256': ('engine.tar', 'verifier.tar', 'build.sh'),
              'native-inputs.sha256': ('native.tar', 'private-work.tar', 'build_native.sh')}
    for record, names in groups.items():
        (out / record).write_text(''.join(f'{digest(out / n)}  {n}\n' for n in names))
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file())
    manifest = {'format': 'noet-gpu-stage-v1', 'productionReady': False, 'builder': builder,
                'modelProfile': profile_name, 'ggufSha256': profile['ggufSha256'],
                **({'switch': {'profile': switch_profile, 'height': switch_height}} if switch else {}),
                'modelManifestSha256': profile['manifestSha256'],
                'files': {name: digest(out / name) for name in files},
                'large': {'model': str(model), 'gguf': str(gguf)} if mainnet_model else 'fetched and converted on the host'}
    (out / 'stage.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--builder', required=True, help='Reviewed native builder image digest (recorded)')
    parser.add_argument('--profile', default='qwen3-0.6b', help='protocol/models/profiles.json entry')
    parser.add_argument('--model', type=Path, help='Verified snapshot directory (mainnet model only)')
    parser.add_argument('--gguf', type=Path, help='Pinned conversion (mainnet model only)')
    parser.add_argument('--switch-profile', help='Rehearse a switch to this profile at --switch-height')
    parser.add_argument('--switch-height', type=int)
    args = parser.parse_args()
    if args.profile == 'qwen3-0.6b' and not args.switch_profile and not (args.model and args.gguf):
        parser.error('--model and --gguf are required for the mainnet model')
    result = stage(args.out, args.builder, args.model and args.model.resolve(), args.gguf and args.gguf.resolve(),
                   args.profile, args.switch_profile, args.switch_height)
    print(json.dumps({'files': len(result['files'])}, indent=2))
