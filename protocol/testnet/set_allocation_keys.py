#!/usr/bin/env python3
"""Set the allocation signers before launch, then refresh the wallet's list.

Each signer opens the NOET wallet, goes to Shared vaults and copies "Your
cosigner key". Collect three keys per allocation class:

  python3 protocol/testnet/set_allocation_keys.py \\
      --treasury KEY,KEY,KEY --team KEY,KEY,KEY --ecosystem KEY,KEY,KEY

(one option per allocation class in protocol/economics/tokenomics.json).
Add --network mainnet to set the mainnet signers instead; the mainnet node
build refuses to start until they exist.

Only public keys are written. Block 1 changes with the keys, so do this before
the network starts; the genesis block does not change.
"""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from allocations import MANIFEST, manifest, outputs  # noqa: E402
from tokenomics import ALLOCATIONS  # noqa: E402

KEYS = HERE / 'allocation-keys.json'
# Where each network keeps its signer keys and the wallet's allocation list.
NETWORKS = {
    'testnet': ('noet-testnet-1', KEYS, MANIFEST),
    'mainnet': ('noet-mainnet-1', HERE.parent / 'mainnet/allocation-keys.json',
                MANIFEST.parent / 'noet-mainnet-1.json'),
}
P = 2**256 - 2**32 - 977  # secp256k1 field prime
CLASSES = tuple(a['name'] for a in ALLOCATIONS)


def checked_key(text):
    """A compressed secp256k1 public key whose x coordinate lies on the curve."""
    key = text.strip().lower()
    try:
        raw = bytes.fromhex(key)
    except ValueError:
        raise ValueError(f'Not hex: {text!r}') from None
    if len(raw) != 33 or raw[0] not in (2, 3):
        raise ValueError(f'Use compressed public keys (66 hex characters): {text!r}')
    x = int.from_bytes(raw[1:], 'big')
    y2 = (pow(x, 3, P) + 7) % P
    if x >= P or pow(y2, (P - 1) // 2, P) not in (0, 1):
        raise ValueError(f'Not a point on secp256k1: {text!r}')
    return key


def write(classes, keys_path=KEYS, manifest_path=MANIFEST, network='noet-testnet-1'):
    clean = {}
    for name in CLASSES:
        keys = [checked_key(k) for k in classes[name]]
        if len(keys) != 3 or len(set(keys)) != 3:
            raise ValueError(f'{name} needs three different keys')
        clean[name] = keys
    every = [k for keys in clean.values() for k in keys]
    document = {'network': network,
                'note': 'Allocation signer public keys (wallet cosigner keys). Private keys stay with each signer.',
                'classes': clean}
    keys_path.write_text(json.dumps(document, indent=2) + '\n')
    rows = outputs(keys_path)
    if manifest_path.parent.exists():
        manifest_path.write_text(json.dumps(manifest(rows, network), separators=(',', ':')) + '\n')
    return {'signers': len(set(every)), 'outputs': len(rows)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in CLASSES:
        parser.add_argument('--' + name, required=True, help='three comma-separated cosigner keys')
    parser.add_argument('--network', choices=sorted(NETWORKS), default='testnet')
    args = parser.parse_args()
    profile, keys_path, manifest_path = NETWORKS[args.network]
    try:
        result = write({name: getattr(args, name).split(',') for name in CLASSES}, keys_path, manifest_path, profile)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result))
    print(f'Next: rebuild the {args.network} node image and rerun the integration test before launch.')
