"""Create a real VDF for a synthetic header, inside the CPU verifier container."""
import argparse
import json
from pathlib import Path


def create(output, priced=False):
    import chiavdf
    from noet.vdf import verify
    parent = bytes.fromhex('ab' * 32)
    iterations = 10_000
    proof = chiavdf.prove_from_hash(parent, 1024, iterations)
    if not verify(parent.hex(), proof.hex(), iterations, chiavdf.verify_from_hash):
        raise ValueError('Generated VDF failed independent verification')
    bits = 0x207fffff if priced else 0
    header = (4).to_bytes(4, 'little') + parent + bytes(32) + (1).to_bytes(4, 'little') + bits.to_bytes(4, 'little')
    data = {'block_hash': parent.hex(), 'header_prefix': header.hex(), 'vdf': proof.hex(), 'tick': iterations}
    with output.open('x') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')
    print('Real VDF verified for a synthetic header. No valid block or network claim.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--priced', action='store_true', help='Synthetic v4 research header with compact target 0x207fffff')
    args = parser.parse_args()
    create(args.out, args.priced)
