"""Read the AI work carried in a serialized NOET block.

Every block's proof holds the prompt token IDs and the proven 256-token
window the model produced. This decodes a raw block (getblock <hash> 0) and
turns those IDs back into text with the pinned model's vocabulary. The prompt
is chosen by the miner: treat it as untrusted text.
"""
import gzip
import json
from pathlib import Path
import struct

VOCAB_PATH = Path(__file__).resolve().parent / 'vocab/qwen3-0.6b-vocab.json.gz'
HEADER_SIZE = 117  # version, prev, merkle, time, adjbits, nonce, bits, hashPoW, flags
MAX_ITEMS = 1 << 22  # bound every length field read from untrusted bytes


class Reader:
    def __init__(self, data):
        self.data, self.at = memoryview(data), 0

    def take(self, n):
        if n < 0 or self.at + n > len(self.data):
            raise ValueError('Truncated block')
        chunk = self.data[self.at:self.at + n]
        self.at += n
        return bytes(chunk)

    def unpack(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def compact(self):
        first = self.unpack('<B')
        value = first if first < 0xfd else self.unpack({0xfd: '<H', 0xfe: '<I', 0xff: '<Q'}[first])
        if value > MAX_ITEMS:
            raise ValueError('Length out of range')
        return value

    def blob(self):
        return self.take(self.compact())

    def array(self, fmt):
        count = self.compact()
        size = struct.calcsize(fmt)
        return list(struct.unpack(f'<{count}{fmt}', self.take(count * size))) if count else []

    def nested(self, fmt):
        return [self.array(fmt) for _ in range(self.compact())]


def skip_transaction(r):
    r.take(4)  # version
    flags = 0
    if r.data[r.at] == 0:  # empty input vector: extended format follows
        r.take(1)
        flags = r.unpack('<B')
    inputs = r.compact()
    for _ in range(inputs):
        r.take(36)
        r.blob()
        r.take(4)
    for _ in range(r.compact()):
        r.take(8)
        r.blob()
        if flags & 0x02:
            r.blob()  # per-output extension
    if flags & 0x01:
        for _ in range(inputs):
            for _ in range(r.compact()):
                r.blob()
    r.take(4)  # lock time


def parse_proof(r):
    p = {'version': r.unpack('<B'), 'tick': r.unpack('<Q'), 'timestamp': r.unpack('<Q')}
    for name in ('target', 'vdf', 'hash', 'blockHash', 'headerPrefix'):
        p[name] = r.blob()
    p['isSolution'] = bool(r.unpack('<B'))
    for name in ('model', 'precision', 'ipfsCid', 'extraFlags'):
        p[name] = r.blob().decode('utf-8', 'replace')
    p['temperature'], p['topP'] = r.unpack('<f'), r.unpack('<f')
    p['topK'], p['repetitionPenalty'] = r.unpack('<I'), r.unpack('<f')
    p['chosenTokens'] = r.array('I')
    p['chosenProbs'] = r.array('f')
    r.array('f')  # sampling_u
    r.array('f')  # softmax_normalizers
    p['promptTokens'] = r.array('I')
    p['padMask'] = list(r.blob())
    r.nested('f')  # topk_logits
    r.nested('I')  # topk_indices
    r.nested('f')  # logsumexp_stats
    return p


def parse_block(raw):
    """Header fields, transaction count and the decoded proof of one raw block."""
    r = Reader(raw)
    header = r.take(HEADER_SIZE)
    count = r.compact()
    for _ in range(count):
        skip_transaction(r)
    proof = parse_proof(r)
    r.unpack('<Q')  # cumulative_tick
    if r.at != len(r.data):
        raise ValueError('Unexpected bytes after the block')
    return {'version': struct.unpack('<i', header[:4])[0], 'time': struct.unpack('<I', header[68:72])[0],
            'transactions': count, 'proof': proof}


def _byte_decoder():
    """Inverse of the GPT-2 byte-to-unicode table used by Qwen's byte-level BPE."""
    printable = list(range(ord('!'), ord('~') + 1)) + list(range(ord('¡'), ord('¬') + 1)) + \
        list(range(ord('®'), ord('ÿ') + 1))
    chars, extra = printable[:], 0
    for b in range(256):
        if b not in printable:
            printable.append(b)
            chars.append(256 + extra)
            extra += 1
    return {chr(c): b for b, c in zip(printable, chars)}


class Detokenizer:
    def __init__(self, path=VOCAB_PATH):
        self.tokens = json.loads(gzip.decompress(Path(path).read_bytes()))['tokens']
        self.bytes_of = _byte_decoder()

    def __call__(self, ids, pad_mask=None):
        out = bytearray()
        for i, token in enumerate(ids):
            if pad_mask and i < len(pad_mask) and pad_mask[i]:
                continue  # 1 marks padding, which is not part of the prompt
            piece = self.tokens[token] if 0 <= token < len(self.tokens) else ''
            if piece.startswith('<|') and piece.endswith('|>'):
                out += piece.encode()  # control tokens are shown literally
            else:
                out += bytes(self.bytes_of.get(ch, ord('?')) for ch in piece)
        return out.decode('utf-8', 'replace')


def block_work(raw, detokenize=None):
    """The public summary of a block's AI work: model, sampler, prompt and proven output."""
    detokenize = detokenize or Detokenizer()
    proof = parse_block(raw)['proof']
    return {'model': proof['model'], 'precision': proof['precision'],
            'sampler': {'temperature': round(proof['temperature'], 4), 'topK': proof['topK'],
                        'topP': round(proof['topP'], 4), 'repetitionPenalty': round(proof['repetitionPenalty'], 4)},
            'promptTokens': len(proof['promptTokens']), 'outputTokens': len(proof['chosenTokens']),
            'prompt': detokenize(proof['promptTokens'], proof['padMask']),
            'output': detokenize(proof['chosenTokens']), 'tick': proof['tick']}
