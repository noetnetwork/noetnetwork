#!/usr/bin/env python3
"""Fetch only the pinned public CPU experiment snapshot. No account or GPU used."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from noet.model_artifacts import relative_name, validate

MANIFEST_SHA256 = 'c18e242c1f4c2273d89a27a226856ad1a6623a7a0c22839495b45371eefa3ae4'
ORIGIN = 'https://huggingface.co/Qwen/Qwen3-0.6B/resolve/c1899de289a04d12100db370d81485cdf75e47ca/'


class PublicModelRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        target = urllib.parse.urlsplit(url)
        host = target.hostname or ''
        if target.scheme != 'https' or target.username or target.password or target.port not in (None, 443) or not (host == 'huggingface.co' or host.endswith('.huggingface.co') or host.endswith('.hf.co')):
            raise ValueError('Unapproved model download redirect')
        return super().redirect_request(request, fp, code, message, headers, url)


def download_record(opener, directory, row, origin=ORIGIN):
    name = relative_name(row['path'])
    if '/' in name:
        raise ValueError('This experiment snapshot contains only top-level files')
    target = directory / name
    partial = directory / (name + '.partial')
    if target.exists() or target.is_symlink():
        raise FileExistsError('Refusing to replace a snapshot file')
    digest = hashlib.sha256()
    size = 0
    try:
        with opener.open(origin + urllib.parse.quote(name, safe=''), timeout=60) as response, partial.open('xb') as output:
            for chunk in iter(lambda: response.read(1024 * 1024), b''):
                size += len(chunk)
                if size > row['bytes']:
                    raise ValueError('Model file exceeds its pinned size')
                digest.update(chunk)
                output.write(chunk)
        if size != row['bytes'] or digest.hexdigest() != row['sha256']:
            raise ValueError('Model file differs from its pinned content')
        # Exclusive final creation also refuses a file appearing during download.
        os.link(partial, target, follow_symlinks=False)
        partial.unlink()
    except Exception:
        # Leave incomplete files for inspection. A partial directory will fail
        # the complete-snapshot verifier and is never marked ready.
        raise ValueError('Candidate download failed for ' + name) from None


def fetch(output, manifest_path=None, expected=MANIFEST_SHA256, budget=2_000_000_000):
    # Another candidate (protocol/models/profiles.json) needs its own manifest
    # and pinned digest; the files then come from that model's own revision.
    raw = (manifest_path or Path(__file__).parent / 'qwen3-06b.candidate.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Candidate manifest differs from the experiment pin')
    manifest = validate(json.loads(raw))
    if sum(row['bytes'] for row in manifest['files']) > budget:
        raise ValueError('Candidate exceeds the experiment download budget')
    origin = ORIGIN if manifest_path is None else (
        f"https://huggingface.co/{manifest['model']}/resolve/{manifest['revision']}/")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), PublicModelRedirect())
    for row in manifest['files']:
        download_record(opener, output, row, origin)
        print('Verified ' + row['path'], flush=True)
    print('Candidate snapshot downloaded. No deployment approval or inference acceptance is implied.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, help='another candidate manifest (needs --sha256)')
    parser.add_argument('--sha256', help='pinned digest of --manifest')
    parser.add_argument('--max-gb', type=float, default=2.0)
    args = parser.parse_args()
    if (args.manifest is None) != (args.sha256 is None):
        parser.error('--manifest and --sha256 go together')
    fetch(args.out.resolve(), args.manifest, args.sha256 or MANIFEST_SHA256, int(args.max_gb * 1e9))
