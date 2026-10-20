#!/usr/bin/env python3
"""Assemble a complete CPU verifier research image from pinned sources.

No GPU, model download, service exposure or production-readiness claim.
"""
import argparse
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
from source_bundle import pack

ROOT = Path(__file__).resolve().parent
HOST_MODULES = ('__init__.py', 'model_runtime.py', 'model_artifacts.py', 'verifier_entry.py',
                'vdf.py', 'verifier_transport.py', 'admission.py')


def prepare(builder, output):
    if not re.fullmatch(r'(?:[a-z0-9][a-z0-9./:_-]*@)?sha256:[a-f0-9]{64}', builder):
        raise ValueError('Use an immutable, reviewed native builder image')
    source = ROOT / '.upstream'
    lock = json.loads((ROOT / 'upstream.lock.json').read_text())
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), *args])
    if git('rev-parse', 'HEAD').decode().strip() != lock['commit']:
        raise ValueError('Source revision differs from lock')
    patch = ROOT / 'patches/noet-miner.patch'
    if git('diff', '--no-ext-diff', '--binary', '--ignore-submodules=all', 'HEAD').strip() != patch.read_bytes().strip():
        raise ValueError('Source differs from reviewed patch')
    paths = ['services/verification-api', 'shared-utils/pow-utils', 'shared-utils/fb-schemas',
             'shared-utils/chiavdf', 'shared-utils/config', 'LICENSE', 'NOTICE', 'LICENSING.md']
    with tempfile.TemporaryDirectory(prefix='noet-cpu-verifier-') as folder:
        context = Path(folder)
        with tarfile.open(fileobj=io.BytesIO(git('archive', lock['commit'], *paths))) as archive:
            archive.extractall(context, filter='data')
        subprocess.run(['git', '-C', str(context), 'apply', '--include=services/verification-api/*', str(patch)],
                       check=True, capture_output=True)
        (context / 'noet').mkdir()
        for name in HOST_MODULES:
            shutil.copyfile(ROOT / 'noet' / name, context / 'noet' / name)
        shutil.copyfile(ROOT / 'docker/VerifierCPU.Dockerfile', context / 'Dockerfile')
        (context / 'BUILDER').write_text(builder+'\n')
        (context / 'NOET-BUILD-INPUTS.json').write_text(json.dumps({'sourceCommit': lock['commit'],
            'nativeBuilder': builder, 'productionReady': False, 'gpuAccepted': False,
            'purpose': 'CPU integration research; no model or service credentials'}, indent=2)+'\n')
        return pack(context, output)


def build(output, expected, reference, tag, docker_context=None):
    """Resolve local Docker tags to the recorded ID before and after the build."""
    for value in (reference, tag):
        if not re.fullmatch(r'[a-z0-9][a-z0-9./:@_-]{0,255}', value):
            raise ValueError('Invalid Docker image reference')
    if reference == tag:
        raise ValueError('Output must not replace the native builder tag')
    docker = ['docker'] + (['--context', docker_context] if docker_context else [])
    def inspect(field):
        return subprocess.check_output(docker + ['image', 'inspect', '--format', '{{.'+field+'}}', reference], text=True).strip()
    # Accept a local image ID for research. Registry digests remain suitable for
    # source preparation but require a separately matched local image ID here.
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', expected):
        raise ValueError('Building requires the recorded local image ID')
    if inspect('Id') != expected or inspect('Architecture') != 'arm64':
        raise ValueError('Native builder differs from the recorded ARM64 image')
    with tarfile.open(output) as archive:
        member = archive.getmember('NOET-BUILD-INPUTS.json')
        if not member.isfile() or member.size > 16384:
            raise ValueError('Invalid build provenance record')
        info = json.load(archive.extractfile(member))
    if info['nativeBuilder'] != expected:
        raise ValueError('Context builder identity differs from selected image')
    with output.open('rb') as stream:
        subprocess.run(docker + ['build', '--build-arg', 'NOET_NATIVE_BUILDER='+reference, '-t', tag, '-'],
                       stdin=stream, check=True)
    if inspect('Id') != expected:
        raise ValueError('Native builder tag changed during build; do not use output')
    return subprocess.check_output(docker + ['image', 'inspect', '--format', '{{.Id}}', tag], text=True).strip()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--builder', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--build-image', help='Optional output image tag; only local ARM64 research builds are accepted')
    parser.add_argument('--builder-tag', help='Local Docker reference whose ID must match --builder')
    parser.add_argument('--docker-context')
    args = parser.parse_args()
    if bool(args.build_image) != bool(args.builder_tag):
        parser.error('--build-image and --builder-tag must be supplied together')
    print(prepare(args.builder, args.out))
    if args.build_image:
        print(build(args.out, args.builder, args.builder_tag, args.build_image, args.docker_context))
