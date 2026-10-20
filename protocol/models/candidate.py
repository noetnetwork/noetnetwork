#!/usr/bin/env python3
"""Write a candidate manifest for a public model snapshot without downloading weights.

Small files are downloaded and checked against their Git blob IDs. Weight files
are stored with Git LFS, whose object ID is the SHA-256 of the content, so their
digests come from the Hugging Face API; every worker still verifies the full
download against this manifest before use. A candidate is not an approval.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from noet.model_artifacts import MAX_MANIFEST, relative_name, validate

API = 'https://huggingface.co/api/models/{model}/revision/{revision}?blobs=true'
FILE = 'https://huggingface.co/{model}/resolve/{revision}/{name}'
SMALL_FILE_LIMIT = 64 * 1024 * 1024


class HubOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        target = urllib.parse.urlsplit(url)
        host = target.hostname or ''
        if target.scheme != 'https' or target.username or target.password or target.port not in (None, 443) or not (
                host == 'huggingface.co' or host.endswith('.huggingface.co') or host.endswith('.hf.co')):
            raise ValueError('Unapproved redirect: ' + host)
        return super().redirect_request(request, fp, code, message, headers, url)


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), HubOnly())


def fetch(url, limit):
    with OPENER.open(url, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Response exceeds its size limit')
    return data


def git_blob_id(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def entry(model, revision, sibling):
    name = relative_name(sibling['rfilename'])
    lfs = sibling.get('lfs')
    if lfs:
        if not re.fullmatch(r'[a-f0-9]{64}', str(lfs.get('sha256', ''))) or lfs.get('size') != sibling.get('size'):
            raise ValueError('Incomplete LFS record for ' + name)
        return {'path': name, 'bytes': lfs['size'], 'sha256': lfs['sha256']}
    url = FILE.format(model=model, revision=revision, name=urllib.parse.quote(name))
    data = fetch(url, SMALL_FILE_LIMIT)
    if len(data) != sibling.get('size') or git_blob_id(data) != sibling.get('blobId'):
        raise ValueError('Downloaded file differs from its Git blob: ' + name)
    return {'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def candidate(model, revision, license_name):
    info = json.loads(fetch(API.format(model=model, revision=revision), 4 * 1024 * 1024))
    if info.get('sha') != revision or info.get('id') != model:
        raise ValueError('Hub returned a different model or revision')
    files = sorted((entry(model, revision, s) for s in info['siblings']), key=lambda row: row['path'])
    return validate({'version': 1, 'model': model, 'revision': revision, 'license': license_name, 'files': files})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, help='organization/name')
    parser.add_argument('--revision', required=True, help='full 40-character commit')
    parser.add_argument('--license', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    raw = (json.dumps(candidate(args.model, args.revision, args.license), indent=2, sort_keys=True) + '\n').encode()
    if len(raw) > MAX_MANIFEST:
        raise ValueError('Manifest exceeds size limit')
    with args.out.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'manifestSha256': hashlib.sha256(raw).hexdigest(), 'status': 'candidate; independent approval required'}))


if __name__ == '__main__':
    main()
