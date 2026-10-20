"""Create and verify exact model-file inventories. Never downloads or executes models."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

MAX_FILES = 4096
MAX_BYTES = 1024 ** 4
MAX_MANIFEST = 2 * 1024 ** 2


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate manifest field')
        result[key] = value
    return result


def relative_name(value):
    if not isinstance(value, str) or len(value) > 512 or '\\' in value:
        raise ValueError('Invalid model file path')
    parts = value.split('/')
    if len(parts) > 16 or any(p in ('', '.', '..') for p in parts) or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid model file path')
    if str(PurePosixPath(value)) != value:
        raise ValueError('Noncanonical model file path')
    return value


def digest_file(root_fd, name):
    """Walk relative to a pinned directory descriptor, without following symlinks."""
    parts = relative_name(name).split('/')
    parent = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = next_fd
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
                raise ValueError('Model file is not a bounded regular file')
            digest, size = hashlib.sha256(), 0
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('Model file exceeds size limit')
                digest.update(chunk)
            after = os.fstat(stream.fileno())
            if size != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('Model file changed during verification')
            return {'path': name, 'bytes': size, 'sha256': digest.hexdigest()}
    finally:
        os.close(parent)


def inventory(root):
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    files = []
    total = 0
    entries = 0
    try:
        def walk(fd, prefix='', depth=0):
            nonlocal total, entries
            if depth > 16:
                raise ValueError('Model directory nesting exceeds limit')
            for name in sorted(os.listdir(fd)):
                entries += 1
                if entries > MAX_FILES * 2:
                    raise ValueError("Too many model directory entries")
                relative = prefix + name
                relative_name(relative)
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        walk(child, relative + '/', depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    if len(files) >= MAX_FILES:
                        raise ValueError('Too many model files')
                    item = digest_file(root_fd, relative)
                    total += item['bytes']
                    if total > MAX_BYTES:
                        raise ValueError('Model inventory exceeds size limit')
                    files.append(item)
                else:
                    raise ValueError('Symlinks and special model files are forbidden')
        walk(root_fd)
    finally:
        os.close(root_fd)
    if not files:
        raise ValueError('Model directory is empty')
    return sorted(files, key=lambda row: row['path'])


def validate(manifest):
    if not isinstance(manifest, dict) or set(manifest) != {'version', 'model', 'revision', 'license', 'files'} or type(manifest['version']) is not int or manifest['version'] != 1:
        raise ValueError('Unsupported model manifest')
    if not isinstance(manifest['model'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', manifest['model']):
        raise ValueError('Use an explicit organization/model identifier')
    if not isinstance(manifest['revision'], str) or not re.fullmatch(r'[a-f0-9]{40}', manifest['revision']):
        raise ValueError('Model revision must be a full immutable commit')
    if not isinstance(manifest['license'], str) or not 1 <= len(manifest['license'].strip()) <= 256:
        raise ValueError('Record the model license for review')
    files = manifest['files']
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('Invalid model file count')
    names, total = set(), 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {'path', 'bytes', 'sha256'}:
            raise ValueError('Invalid model file entry')
        name = relative_name(item['path'])
        if name in names:
            raise ValueError('Duplicate model file')
        names.add(name)
        if type(item['bytes']) is not int or not 0 <= item['bytes'] <= MAX_BYTES:
            raise ValueError('Invalid model file size')
        if not isinstance(item['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256']):
            raise ValueError('Invalid model file digest')
        total += item['bytes']
    if total > MAX_BYTES:
        raise ValueError('Model inventory exceeds size limit')
    return manifest


def create(root, model, revision, license_name):
    return validate({'version': 1, 'model': model, 'revision': revision, 'license': license_name, 'files': inventory(root)})


def verify(root, raw, expected_digest):
    if len(raw) > MAX_MANIFEST or not re.fullmatch(r'[a-f0-9]{64}', expected_digest):
        raise ValueError('Invalid manifest size or trusted digest')
    if hashlib.sha256(raw).hexdigest() != expected_digest:
        raise ValueError('Manifest does not match the trusted digest')
    manifest = validate(json.loads(raw, object_pairs_hook=unique_object))
    if inventory(root) != sorted(manifest['files'], key=lambda row: row['path']):
        raise ValueError('Model files differ from the approved inventory')
    return {'model': manifest['model'], 'revision': manifest['revision'], 'files': len(manifest['files']), 'bytes': sum(f['bytes'] for f in manifest['files']), 'manifestSha256': expected_digest, 'filesVerified': True, 'proofValidation': 'not performed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    make = commands.add_parser('create')
    make.add_argument('root', type=Path)
    make.add_argument('--model', required=True)
    make.add_argument('--revision', required=True)
    make.add_argument('--license', required=True)
    make.add_argument('--out', required=True, type=Path)
    check = commands.add_parser('verify')
    check.add_argument('root', type=Path)
    check.add_argument('manifest', type=Path)
    check.add_argument('--sha256', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'create':
            if args.out.resolve().is_relative_to(args.root.resolve()):
                raise ValueError('Store the manifest outside the model directory')
            data = create(args.root, args.model, args.revision, args.license)
            raw = (json.dumps(data, indent=2, sort_keys=True) + '\n').encode()
            if len(raw) > MAX_MANIFEST:
                raise ValueError('Manifest exceeds size limit')
            with args.out.open('xb') as stream:
                stream.write(raw)
            print(json.dumps({'manifestSha256': hashlib.sha256(raw).hexdigest(), 'status': 'candidate; independent approval required'}))
        else:
            fd = os.open(args.manifest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError('Manifest must be a regular file')
                raw = stream.read(MAX_MANIFEST + 1)
            print(json.dumps(verify(args.root, raw, args.sha256)))
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, 'Model artifact check failed: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
