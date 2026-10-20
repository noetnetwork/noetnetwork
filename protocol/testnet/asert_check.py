#!/usr/bin/env python3
"""Check NOET's ASERT difficulty on a real test-variant node.

  python3 protocol/testnet/asert_check.py --image IMAGE [--docker-context CTX]

Starts a disposable node (no network) with ASERT from height 20, mines fast
blocks (60 s apart) and then slow ones (3,000 s apart) on a mocked clock, and
compares every block's nBits with an independent Python port of upstream's
aserti3-2d (pow.cpp) and NOET's parameters (testnet/prepare.py). Prints a JSON
report; exits 1 on any mismatch. The container is removed afterwards.
"""
import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare import ASERT_ANCHOR_BITS, ASERT_HALF_LIFE, ASERT_PHASE_B_OFFSET, GENESIS_TIME  # noqa: E402

POW_LIMIT = (1 << 255) - 1
SPACING = 600
START = 20  # -daav2height for this check; the networks pin their own heights


def set_compact(bits):
    size, word = bits >> 24, bits & 0x7fffff
    return word >> 8 * (3 - size) if size <= 3 else word << 8 * (size - 3)


def get_compact(target):
    size = (target.bit_length() + 7) // 8
    compact = target << 8 * (3 - size) if size <= 3 else target >> 8 * (size - 3)
    if compact & 0x800000:
        compact >>= 8
        size += 1
    return compact | size << 24


def asert(anchor_target, time_diff, height_diff, half_life=ASERT_HALF_LIFE, limit=POW_LIMIT):
    """CalculateASERT: anchor * 2^((time_diff - spacing*(height_diff+1)) / half_life)."""
    numerator = (time_diff - SPACING * (height_diff + 1)) * 65536
    exponent = abs(numerator) // half_life * (1 if numerator >= 0 else -1)  # C++ truncates toward zero
    shifts, frac = exponent >> 16, exponent & 0xffff
    factor = 65536 + ((195766423245049 * frac + 971821376 * frac * frac + 5127 * frac ** 3 + (1 << 47)) >> 48)
    target = anchor_target * factor
    shifts -= 16
    target = target >> -shifts if shifts < 0 else target << shifts
    return 1 if target == 0 else min(target, limit)


def expected_bits(height, times, bits, start=START):
    """nBits for `height` given the chain's earlier timestamps and bits."""
    phase_b = start + ASERT_PHASE_B_OFFSET
    if height < start:
        return get_compact(POW_LIMIT)  # no retargeting before the fork
    if height == start:
        return ASERT_ANCHOR_BITS
    if height < phase_b:
        anchor, anchor_height, parent_time = set_compact(ASERT_ANCHOR_BITS), start, times[start - 1]
    else:
        anchor, anchor_height, parent_time = set_compact(bits[phase_b - 1]), phase_b - 1, times[phase_b - 2]
    return get_compact(asert(anchor, times[height - 1] - parent_time, height - 1 - anchor_height))


def run(args):
    docker = ['docker', '--context', args.docker_context]
    name = 'noet-asert-' + uuid.uuid4().hex[:8]
    subprocess.run(docker + ['run', '-d', '--rm', '--name', name, '--network', 'none', '--entrypoint', 'bitcoind',
                             args.image, '-chain=tensor-reg', '-server=1', '-validationapi=mock', '-useextapi=0',
                             '-listen=0', '-rpcport=29443', '-printtoconsole=0', f'-daav2height={START}',
                             f'-daav2phasebheight={START + ASERT_PHASE_B_OFFSET}'],
                   check=True, capture_output=True)

    def cli(*parts):
        out = subprocess.run(docker + ['exec', name, 'bitcoin-cli', '-chain=tensor-reg', '-rpcport=29443',
                                       '-rpcwait', *map(str, parts)], check=True, capture_output=True, text=True)
        text = out.stdout.strip()
        return json.loads(text) if text[:1] in ('[', '{', '"') else text
    try:
        cli('createwallet', 'asert')
        address = cli('getnewaddress', '', 'bech32')
        now, times, bits, rows, mismatches = GENESIS_TIME + 600, {0: GENESIS_TIME}, {}, [], []
        bits[0] = int(cli('getblockheader', cli('getblockhash', 0))['bits'], 16)
        schedule = [60] * (START + 140) + [3000] * 120
        for height, gap in enumerate(schedule, start=1):
            now += gap
            cli('setmocktime', now)
            block = cli('generatetoaddress', 1, address)[0]
            header = cli('getblockheader', block)
            times[height], bits[height] = header['time'], int(header['bits'], 16)
            want = expected_bits(height, times, bits)
            rows.append({'height': height, 'time': header['time'], 'bits': header['bits'],
                         'difficulty': header['difficulty']})
            if bits[height] != want:
                mismatches.append({'height': height, 'node': header['bits'], 'expected': f'{want:08x}'})
        top = max(rows, key=lambda r: r['difficulty'])
        report = {'image': args.image, 'asertHeight': START, 'phaseBHeight': START + ASERT_PHASE_B_OFFSET,
                  'blocks': len(rows), 'mismatches': mismatches,
                  'beforeFork': rows[START - 2]['bits'], 'atFork': rows[START - 1]['bits'],
                  'hardest': {'height': top['height'], 'bits': top['bits'], 'difficulty': top['difficulty']},
                  'final': rows[-1]['bits'], 'passed': not mismatches}
        if args.out:
            Path(args.out).write_text(json.dumps({**report, 'rows': rows}, indent=2) + '\n')
        print(json.dumps(report, indent=2))
        return 0 if report['passed'] else 1
    finally:
        subprocess.run(docker + ['rm', '-f', name], capture_output=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--image', required=True)
    parser.add_argument('--docker-context', default='colima-noet-build')
    parser.add_argument('--out', help='also write every block to this JSON file')
    sys.exit(run(parser.parse_args()))
