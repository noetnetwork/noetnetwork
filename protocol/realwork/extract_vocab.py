#!/usr/bin/env python3
"""Extract the pinned model's token vocabulary from its GGUF file.

The explorer turns the token IDs in each block's proof back into text. It
needs only the vocabulary (the Qwen3 tokenizer, Apache-2.0), not the weights,
so this reads the GGUF metadata and writes a small gzip'd JSON list.

  python3 protocol/realwork/extract_vocab.py --gguf qwen3-0.6b-bf16.gguf
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import struct

PINNED_GGUF_SHA256 = '8fa11f4d815167c4f6e3c91b25aebde25d8346a8f11228e744016592532b0700'
OUT = Path(__file__).resolve().parents[1] / 'noet/vocab/qwen3-0.6b-vocab.json.gz'
SCALARS = {0: '<B', 1: '<b', 2: '<H', 3: '<h', 4: '<I', 5: '<i', 6: '<f', 7: '<?', 10: '<Q', 11: '<q', 12: '<d'}


def read_metadata(stream, wanted):
    def unpack(fmt):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, stream.read(size))[0]

    def string():
        return stream.read(unpack('<Q')).decode('utf-8')

    def value(kind):
        if kind in SCALARS:
            return unpack(SCALARS[kind])
        if kind == 8:
            return string()
        if kind == 9:
            inner, count = unpack('<I'), unpack('<Q')
            return [value(inner) for _ in range(count)]
        raise ValueError(f'Unknown GGUF value type {kind}')

    if stream.read(4) != b'GGUF':
        raise ValueError('Not a GGUF file')
    version, _tensors, pairs = unpack('<I'), unpack('<Q'), unpack('<Q')
    if version not in (2, 3):
        raise ValueError(f'Unsupported GGUF version {version}')
    found = {}
    for _ in range(pairs):
        key, kind = string(), unpack('<I')
        item = value(kind)
        if key in wanted:
            found[key] = item
    return found


def extract(gguf, out=OUT):
    digest = hashlib.sha256()
    with open(gguf, 'rb') as stream:
        while chunk := stream.read(1 << 22):
            digest.update(chunk)
    if digest.hexdigest() != PINNED_GGUF_SHA256:
        raise ValueError('The GGUF file differs from the pinned NOET model')
    with open(gguf, 'rb') as stream:
        meta = read_metadata(stream, {'tokenizer.ggml.tokens', 'tokenizer.ggml.model', 'tokenizer.ggml.pre'})
    document = {'model': 'Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca',
                'source': 'tokenizer.ggml.tokens from the pinned GGUF (sha256 ' + PINNED_GGUF_SHA256 + ')',
                'licence': 'Apache-2.0 (Qwen3 tokenizer, Copyright Alibaba Cloud)',
                'tokenizer': meta.get('tokenizer.ggml.model'), 'pre': meta.get('tokenizer.ggml.pre'),
                'tokens': meta['tokenizer.ggml.tokens']}
    out.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 keeps the file byte-for-byte reproducible.
    with gzip.GzipFile(out, 'wb', mtime=0) as stream:
        stream.write(json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode())
    return len(document['tokens'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--gguf', type=Path, required=True)
    print(json.dumps({'tokens': extract(parser.parse_args().gguf), 'out': str(OUT)}))
