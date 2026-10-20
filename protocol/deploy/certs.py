#!/usr/bin/env python3
"""Issue the private CA and the six relay identities for one node/GPU pairing.

Output stays in a private operator directory. The CA key never leaves it; each
host receives only the CA certificate, its own leaf keys and the exact peer
pins. Leaves are short-lived (30 days by default); rotate by re-running into a
new directory and restarting both sides.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import ssl
import subprocess

# relay name -> (purpose, server name it presents or expects, peer relay name)
LINKS = {
    'node-request': ('clientAuth', 'verifier-requests.noet.internal', 'verifier-request'),
    'verifier-request': ('serverAuth', 'verifier-requests.noet.internal', 'node-request'),
    'verifier-verdict': ('clientAuth', 'node-verdicts.noet.internal', 'node-verdict'),
    'node-verdict': ('serverAuth', 'node-verdicts.noet.internal', 'verifier-verdict'),
    'miner-rpc': ('clientAuth', 'node-rpc.noet.internal', 'node-rpc'),
    'node-rpc': ('serverAuth', 'node-rpc.noet.internal', 'miner-rpc'),
}
NODE = ('node-request', 'node-verdict', 'node-rpc')
GPU = ('verifier-request', 'verifier-verdict', 'miner-rpc')


def openssl(folder, *args):
    subprocess.run(['openssl', *args], cwd=folder, check=True, capture_output=True, timeout=30)


def pin(path):
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(Path(path).read_text())).hexdigest()


def issue(out, days=30):
    out = Path(out)
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    openssl(out, 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
            '-keyout', 'ca.key', '-out', 'ca.pem', '-days', str(days + 1), '-subj', '/CN=NOET private relay CA',
            '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0', '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
            '-addext', 'subjectKeyIdentifier=hash')
    for name, (purpose, server_name, _) in LINKS.items():
        openssl(out, 'req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
                '-keyout', name + '.key', '-out', name + '.csr', '-subj', '/CN=' + name)
        san = server_name if purpose == 'serverAuth' else name + '.noet.internal'
        (out / (name + '.ext')).write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n'
            f'extendedKeyUsage={purpose}\nsubjectAltName=DNS:{san}\nsubjectKeyIdentifier=hash\n'
            'authorityKeyIdentifier=keyid,issuer\n')
        openssl(out, 'x509', '-req', '-in', name + '.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key', '-CAcreateserial',
                '-days', str(days), '-extfile', name + '.ext', '-out', name + '.pem')
        for suffix in ('.csr', '.ext'):
            (out / (name + suffix)).unlink()
    for path in out.iterdir():
        os.chmod(path, 0o600)
    ca = (out / 'ca.pem').read_text()
    def bundle(names):
        result = {'ca': ca}
        for name in names:
            _, server_name, peer = LINKS[name]
            result[name] = {'cert': (out / (name + '.pem')).read_text(), 'key': (out / (name + '.key')).read_text(),
                            'peer_sha256': pin(out / (peer + '.pem')), 'server_name': server_name}
        return result
    for label, names in (('node', NODE), ('gpu', GPU)):
        raw = json.dumps(bundle(names), sort_keys=True).encode()
        fd = os.open(out / f'{label}-bundle.b64', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(base64.b64encode(raw))
    return {name: pin(out / (name + '.pem')) for name in LINKS}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--days', type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.days <= 90:
        parser.error('Use short-lived certificates (1 to 90 days)')
    print(json.dumps({'pins': issue(args.out, args.days)}, indent=2))
