#!/usr/bin/env python3
"""Fix the mainnet genesis time, find its nonce and compute its hash.

  python3 protocol/mainnet/genesis.py --time 1798761600

Writes genesisTime, genesisNonce and genesisHash into network.json. The nonce is
the first whose 80-byte short header meets the genesis target 0x207fffff, as for
the testnet. Choose the time shortly before launch: it is part of the chain's
identity and cannot change afterwards. `--pin` fills in genesisHash for a genesis
already fixed. The hash is the node's `getblockhash 0`; the tests check this
calculation against the lab and testnet chains, and the launch build confirms it.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

NETWORK = Path(__file__).resolve().parent / 'network.json'
# Merkle root of the genesis coinbase shared by every NOET chain.
MERKLE = bytes.fromhex('4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b')[::-1]
BITS, TARGET = 0x207fffff, 0x7fffff << (8 * 29)
MODEL = 'Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca'
# The genesis proof every NOET chain carries (chainparams.cpp): tick 10 and this VDF.
TICK = 10
VDF = bytes.fromhex(
    '0000e173ab04019e2b3be0a758fc11852ae390f6ba7c51d044a8ec2609f25a880b453cdd52563519'
    '4557f71910bd4039eed4bb563caf9fcf91631516822d3a44a059f5bec7ae8a7ec961b80073655f2f'
    '0cb4a42f179c3c5d7336a10578ee1c7ded3d01000400000000000000000000000000000000000000'
    + '00' * 80)


def sha256d(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def short_header(time, nonce):
    return struct.pack('<I', 1) + bytes(32) + MERKLE + struct.pack('<III', time, BITS, nonce)


def nonce_for(time):
    for nonce in range(2 ** 32):
        if int.from_bytes(sha256d(short_header(time, nonce)), 'little') < TARGET:
            return nonce
    raise ValueError('No nonce found')


def proof_commitment(model=MODEL):
    """CProofBlob::GetMerkleRoot of the genesis proof (primitives/proofblob.cpp)."""
    def leaf(tag, data):
        return sha256d(b'\xffPOW\0' + bytes([tag]) + struct.pack('<I', len(data)) + data)
    name = model.encode()
    # The serialized blob: version, tick, timestamp, target, vdf, hash, block_hash,
    # header_prefix, is_solution, model, precision, ipfs_cid, extra_flags,
    # temperature, top_p, top_k, repetition_penalty, then nine empty vectors.
    blob = (b'\0' + struct.pack('<QQ', TICK, 0) + b'\0' + bytes([len(VDF)]) + VDF
            + b'\0' * 4 + bytes([len(name)]) + name + b'\0' * 3 + struct.pack('<ffIf', 0, 0, 0, 0) + b'\0' * 9)
    leaves = [leaf(1, struct.pack('<Q', TICK)), leaf(2, VDF),
              leaf(3, b'\0' + hashlib.sha256(name).digest()), leaf(4, blob)]
    return sha256d(sha256d(leaves[0] + leaves[1]) + sha256d(leaves[2] + leaves[3]))


def genesis_hash(time, nonce, model=MODEL):
    """CBlockHeader::GetHash: the short header, then nBits and the proof commitment."""
    header = short_header(time, nonce) + struct.pack('<I', BITS) + proof_commitment(model)
    return sha256d(header)[::-1].hex()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--time', type=int, help='genesis time, Unix seconds (UTC)')
    parser.add_argument('--pin', action='store_true', help='compute genesisHash for the fixed genesis')
    args = parser.parse_args()
    spec = json.loads(NETWORK.read_text())
    if args.pin:
        if not isinstance(spec.get('genesisTime'), int) or not isinstance(spec.get('genesisNonce'), int):
            parser.error('Fix the genesis first with --time')
    else:
        if args.time is None:
            parser.error('Give --time, or --pin for a genesis already fixed')
        if not 1_790_000_000 <= args.time < 2 ** 32:
            parser.error('Use a Unix time in seconds after October 2026')
        if spec.get('genesisTime') is not None:
            parser.error('The mainnet genesis is already fixed; edit network.json by hand only before launch')
        spec['genesisTime'], spec['genesisNonce'] = args.time, nonce_for(args.time)
    spec['genesisHash'] = genesis_hash(spec['genesisTime'], spec['genesisNonce'])
    NETWORK.write_text(json.dumps(spec, indent=2) + '\n')
    print(json.dumps({k: spec[k] for k in ('genesisTime', 'genesisNonce', 'genesisHash')}))


if __name__ == '__main__':
    main()
