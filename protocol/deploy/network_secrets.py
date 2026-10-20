#!/usr/bin/env python3
"""Generate a fresh, independent secret set for one hosted network.

Writes into a new private operator directory (mode 0700, files 0600): relay
CA and identities (certs/), explorer and faucet bearer tokens, the verdict
HMAC secret, the faucet wallet passphrase and the restricted miner RPC
credentials with their rpcauth line. Nothing is printed except file names.

  python3 protocol/deploy/network_secrets.py --out .noet-session/testnet-YYYYMMDD
  python3 protocol/deploy/network_secrets.py --out .noet-session/mainnet-YYYYMMDD --no-faucet
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from certs import issue  # noqa: E402


def rpcauth(user, password, salt=None):
    """The node's rpcauth format: user:salt$HMAC-SHA256(salt, password)."""
    salt = salt or secrets.token_hex(16)
    digest = hmac.new(salt.encode(), password.encode(), hashlib.sha256).hexdigest()
    return f'{user}:{salt}${digest}'


def private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text + '\n')


def generate(out, days=30, faucet=True):
    out = Path(out)
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    pins = issue(out / 'certs', days)
    password = secrets.token_urlsafe(32)
    values = {
        'explorer-token': secrets.token_urlsafe(36),
        **({'faucet-token': secrets.token_urlsafe(36), 'faucet-passphrase': secrets.token_urlsafe(32)} if faucet else {}),
        'shared-secret': secrets.token_hex(32),
        'miner-credentials': 'miner:' + password,
        'miner-rpcauth': rpcauth('miner', password),
    }
    for name, value in values.items():
        private(out / name, value)
    (out / 'pins.json').write_text(json.dumps(pins, indent=2) + '\n')
    return sorted(values) + ['certs/', 'pins.json']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--no-faucet', action='store_true', help='Mainnet: no faucet token or wallet passphrase')
    args = parser.parse_args()
    print(json.dumps({'directory': str(args.out), 'files': generate(args.out, args.days, not args.no_faucet)}))
