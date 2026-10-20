"""Process supervisor for one hosted NOET node (development network or public testnet).

Runs the native node, the authenticated explorer service and the mutual-TLS
relays that connect it to a separately hosted GPU verifier and miner. Native
RPC and raw ZeroMQ stay on loopback. Secrets arrive as platform secrets, are
written only to a private runtime directory and are never logged.
"""
import base64
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

DATA = Path(os.environ.get('NOET_DATA', '/data'))
RUN = Path(os.environ.get('NOET_RUN', '/run/noet'))
UID = 10001
# The build context records which network this image runs (prepare.py --profile).
NETWORKS = {
    'private-work': {'genesis': '301587d540aafc312cf4606e700d9662e038e7e43ca6cca92779d6b4baa81b4e',
                     'hrp': 'nwrk', 'name': 'noet-private-work-v1', 'p2pPort': 30441, 'public': False},
    'testnet': {'genesis': '2da97e8dadb7fe27645263d3e732211cb67650adbc02a016102cc7390785cdaa',
                'hrp': 'tnoet', 'name': 'noet-testnet-1', 'p2pPort': 33441, 'public': True},
}


def network(path=Path(os.environ.get('NOET_NETWORK_FILE', '/opt/noet/runtime/network.json'))):
    data = json.loads(path.read_text()) if path.exists() else {'profile': 'private-work'}
    if data['profile'] != 'mainnet':
        return NETWORKS[data['profile']]
    # Mainnet's identity comes from the build (protocol/mainnet/network.json), pinned at launch.
    net = {'genesis': data['genesis'], 'hrp': data['hrp'], 'name': data['name'],
           'p2pPort': int(data['p2pPort']), 'public': True, 'faucet': False, 'seeds': data.get('seeds', [])}
    if not re.fullmatch('[0-9a-f]{64}', net['genesis']) or net['hrp'] != 'noet':
        raise ValueError('The mainnet build carries no valid genesis')
    return net


NETWORK = network()
GENESIS = NETWORK['genesis']
RPC_PORT, P2P_PORT = 30443, NETWORK['p2pPort']
REQUEST_PORT, VERDICT_PORT = 18555, 18556
VERDICT_TLS_PORT, RPC_TLS_PORT, EXPLORER_PORT, FAUCET_PORT, GATEWAY_PORT = 17443, 18443, 8788, 8790, 8791
# The remote miner may only lease work, submit proofs and read verdicts.
MINER_METHODS = ('create_mining_work_unit', 'submit_mining_response', 'getrecentvalidations',
                 'getbestblockhash', 'getblockcount', 'getblockhash', 'getblockchaininfo',
                 'getmodelslist', 'validateaddress', 'getvalidationapiinfo')
RELAY_NAMES = ('node-request', 'node-verdict', 'node-rpc')


def log(**event):
    event['at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    print(json.dumps(event, sort_keys=True), flush=True)


def drop_privileges():
    if os.geteuid() != 0:
        return
    for path in (DATA, RUN):
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, UID, UID)
        os.chmod(path, 0o700)
    os.setgroups([])
    os.setgid(UID)
    os.setuid(UID)
    os.environ['HOME'] = '/home/noet'


def private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data if isinstance(data, bytes) else data.encode())
    os.replace(tmp, path)
    return path


def secrets_from_env(env):
    token = env.get('NOET_EXPLORER_TOKEN', '')
    shared = env.get('VALIDATOR_SHARED_SECRET', '')
    if len(token) < 32:
        raise ValueError('NOET_EXPLORER_TOKEN must have at least 32 characters')
    if not re.fullmatch('[0-9a-f]{64}', shared):
        raise ValueError('VALIDATOR_SHARED_SECRET must be 64 lowercase hex characters')
    bundle = json.loads(base64.b64decode(env['NOET_TLS_BUNDLE'], validate=True))
    for name in RELAY_NAMES:
        if not {'cert', 'key', 'peer_sha256', 'server_name'} <= set(bundle.get(name, {})):
            raise ValueError('TLS bundle is missing ' + name)
    miner = env.get('NOET_MINER_RPCAUTH', '')
    if miner and not re.fullmatch(r'miner:[0-9a-f]{32}\$[0-9a-f]{64}', miner):
        raise ValueError('NOET_MINER_RPCAUTH must be a miner rpcauth line')
    # The first endpoint is the verifier; any others are standby verifiers the
    # node's request relay switches to while it is down.
    verifier = [e.strip() for e in env.get('NOET_VERIFIER_ENDPOINT', '').split(',')] \
        if env.get('NOET_VERIFIER_ENDPOINT', '').strip() else []
    if len(verifier) > 4 or len(set(verifier)) != len(verifier) or \
            not all(re.fullmatch(r'[0-9a-fA-F.:\[\]]+:[0-9]{1,5}', e) for e in verifier):
        raise ValueError('NOET_VERIFIER_ENDPOINT must be ip:port, or up to four of them separated by commas')
    faucet_token, passphrase = env.get('NOET_FAUCET_TOKEN', ''), env.get('NOET_FAUCET_PASSPHRASE', '')
    if (faucet_token or passphrase) and (len(faucet_token) < 32 or len(passphrase) < 16):
        raise ValueError('Faucet needs a 32+ character token and a 16+ character wallet passphrase')
    if faucet_token and not NETWORK.get('faucet', True):
        raise ValueError('This network has no faucet; remove NOET_FAUCET_TOKEN and NOET_FAUCET_PASSPHRASE')
    return {'token': token, 'shared': shared, 'bundle': bundle, 'miner': miner, 'verifier': verifier,
            'faucet': {'token': faucet_token, 'passphrase': passphrase} if faucet_token else None}


def relay_configs(secrets):
    """Three relays: node requests out to the verifier, verdicts and miner RPC in."""
    tls = RUN / 'tls'
    bundle = secrets['bundle']
    private_write(tls / 'ca.pem', bundle['ca'])
    configs = {}
    for name in RELAY_NAMES:
        item = bundle[name]
        private_write(tls / (name + '.pem'), item['cert'])
        private_write(tls / (name + '.key'), item['key'])
        configs[name] = {'ca_file': str(tls / 'ca.pem'), 'cert_file': str(tls / (name + '.pem')),
                         'key_file': str(tls / (name + '.key')), 'peer_sha256': item['peer_sha256'],
                         'server_name': item['server_name'], 'public_tls': True}
    configs['node-verdict'].update(mode='server', listen_host='0.0.0.0', listen_port=VERDICT_TLS_PORT,
                                   target_host='127.0.0.1', target_port=VERDICT_PORT)
    configs['node-rpc'].update(mode='server', listen_host='0.0.0.0', listen_port=RPC_TLS_PORT,
                               target_host='127.0.0.1', target_port=RPC_PORT)
    if secrets['verifier']:
        (host, port), *standby = [(e.rsplit(':', 1)[0].strip('[]'), int(e.rsplit(':', 1)[1]))
                                  for e in secrets['verifier']]
        configs['node-request'].update(mode='client', listen_host='127.0.0.1', listen_port=REQUEST_PORT,
                                       target_host=host, target_port=port, fallback_targets=standby)
    else:
        configs.pop('node-request')
    paths = {}
    for name, config in configs.items():
        paths[name] = private_write(RUN / (name + '.json'), json.dumps(config, sort_keys=True))
    return paths


def node_command(secrets):
    # A public network accepts inbound peers; the development node stays closed.
    listen = '-listen=1' if NETWORK['public'] else '-listen=0'
    # With seeds (mainnet) the node also dials out, to reach other operators' nodes.
    outbound = ['-dnsseed=1'] if NETWORK.get('seeds') else ['-connect=0', '-dnsseed=0']
    command = ['bitcoind', '-chain=tensor-reg', '-datadir=' + str(DATA / 'node'), '-server=1', '-txindex=1',
               listen, *outbound, '-fixedseeds=0', '-listenonion=0', '-discover=0',
               f'-port={NETWORK["p2pPort"]}', f'-rpcport={RPC_PORT}', '-rpcbind=127.0.0.1', '-rpcallowip=127.0.0.1',
               '-validationapi=real', '-useextapi=1', '-printtoconsole=1', '-fallbackfee=0.0002',
               '-rpcwhitelistdefault=0', '-dbcache=256', '-maxmempool=100']
    if secrets['miner']:
        command += ['-rpcauth=' + secrets['miner'], '-rpcwhitelist=miner:' + ','.join(MINER_METHODS)]
    return command


def node_env(secrets):
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/home/noet',
           'VALIDATOR_HOST': '127.0.0.1', 'VALIDATOR_PUSH_PORT': str(REQUEST_PORT),
           'VALIDATOR_PULL_BIND': '127.0.0.1', 'VALIDATOR_PULL_PORT': str(VERDICT_PORT),
           'VALIDATOR_SHARED_SECRET': secrets['shared']}
    return env


def cert_expiry(bundle):
    """Earliest notAfter (Unix time) among the CA and this node's relay leaves, or None."""
    import ssl
    import tempfile
    decode = getattr(getattr(ssl, '_ssl', None), '_test_decode_cert', None)
    if decode is None:
        return None
    ends = []
    with tempfile.TemporaryDirectory() as folder:
        for name, pem in [('ca', bundle['ca'])] + [(n, bundle[n]['cert']) for n in bundle if n != 'ca']:
            path = Path(folder) / f'{name}.pem'
            path.write_text(pem)
            ends.append(ssl.cert_time_to_seconds(decode(str(path))['notAfter']))
    return int(min(ends))


def explorer_env(secrets):
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/home/noet', 'PYTHONPATH': os.environ.get('PYTHONPATH', ''),
            'PYTHONDONTWRITEBYTECODE': '1', 'NOET_RPC_URL': f'http://127.0.0.1:{RPC_PORT}',
            'NOET_RPC_COOKIE': str(DATA / 'node' / 'tensor-reg' / '.cookie'), 'NOET_GENESIS_HASH': GENESIS,
            'NOET_INDEX_PATH': str(DATA / 'explorer.sqlite3'), 'NOET_EXPLORER_TOKEN': secrets['token'],
            'NOET_EXPLORER_BIND': '0.0.0.0', 'NOET_EXPLORER_TLS_PROXY': '1',
            'NOET_EXPLORER_PORT': str(EXPLORER_PORT), 'NOET_NETWORK_NAME': NETWORK['name']}
    expires = cert_expiry(secrets['bundle'])
    if expires:  # shown on the public health check so monitors warn before proofs stop
        env['NOET_RELAY_CERT_EXPIRES'] = str(expires)
    return env


def faucet_env(secrets):
    return {'PATH': '/usr/bin:/bin', 'HOME': '/home/noet', 'PYTHONPATH': os.environ.get('PYTHONPATH', ''),
            'PYTHONDONTWRITEBYTECODE': '1', 'NOET_RPC_URL': f'http://127.0.0.1:{RPC_PORT}',
            'NOET_RPC_COOKIE': str(DATA / 'node' / 'tensor-reg' / '.cookie'),
            'NOET_FAUCET_TOKEN': secrets['faucet']['token'], 'NOET_FAUCET_PASSPHRASE': secrets['faucet']['passphrase'],
            'NOET_FAUCET_DB': str(DATA / 'faucet.sqlite3'), 'NOET_ADDRESS_HRP': NETWORK['hrp'], 'NOET_FAUCET_ADDRESS_FILE': str(DATA / 'faucet-address'),
            'NOET_EXPLORER_BIND': '0.0.0.0', 'NOET_EXPLORER_TLS_PROXY': '1', 'NOET_FAUCET_PORT': str(FAUCET_PORT)}


def gateway_env(secrets):
    # The website queues visitors' prompts with the explorer token it already holds.
    return {'PATH': '/usr/bin:/bin', 'HOME': '/home/noet', 'PYTHONPATH': os.environ.get('PYTHONPATH', ''),
            'PYTHONDONTWRITEBYTECODE': '1', 'NOET_RPC_URL': f'http://127.0.0.1:{RPC_PORT}',
            'NOET_RPC_COOKIE': str(DATA / 'node' / 'tensor-reg' / '.cookie'), 'NOET_GENESIS_HASH': GENESIS,
            'NOET_ADDRESS_HRP': NETWORK['hrp'], 'NOET_EXPLORER_BIND': '0.0.0.0', 'NOET_EXPLORER_TLS_PROXY': '1',
            'NOET_GATEWAY_PORT': str(GATEWAY_PORT), 'NOET_TASKS_DB': str(DATA / 'tasks.sqlite3'),
            'NOET_SITE_TOKEN': secrets['token'], 'NOET_NETWORK_NAME': NETWORK['name']}


class Service:
    def __init__(self, name, command, env):
        self.name, self.command, self.env = name, command, env
        self.process, self.failures, self.next_start = None, 0, 0.0

    def poll(self, now):
        if self.process and self.process.poll() is None:
            return
        if self.process:
            log(service=self.name, event='exited', code=self.process.returncode)
            self.failures += 1
            self.next_start = now + min(60, 2 ** min(self.failures, 6))
            self.process = None
        if now >= self.next_start:
            self.process = subprocess.Popen(self.command, env=self.env, stdin=subprocess.DEVNULL)
            log(service=self.name, event='started', pid=self.process.pid)

    def stop(self, timeout):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)


def services(secrets):
    relays = relay_configs(secrets)
    python = sys.executable
    relay_env = {'PATH': '/usr/bin:/bin', 'PYTHONPATH': os.environ.get('PYTHONPATH', ''),
                 'PYTHONDONTWRITEBYTECODE': '1', 'HOME': '/home/noet'}
    items = [Service('node', node_command(secrets), node_env(secrets))]
    items += [Service(name, [python, '-m', 'noet.secure_transport', '--config', str(path)], relay_env)
              for name, path in sorted(relays.items())]
    items.append(Service('explorer', [python, '-m', 'noet.serve'], explorer_env(secrets)))
    if secrets['faucet']:
        items.append(Service('faucet', [python, '-m', 'noet.faucet'], faucet_env(secrets)))
    if os.environ.get('NOET_PUBLIC_MINING') == '1':
        items.append(Service('gateway', [python, '-m', 'noet.gateway'], gateway_env(secrets)))
    return items


def main():
    drop_privileges()
    (DATA / 'node').mkdir(mode=0o700, exist_ok=True)
    secrets = secrets_from_env(os.environ)
    items = services(secrets)
    log(event='supervisor-started', services=[item.name for item in items],
        verifierConfigured=bool(secrets['verifier']), minerConfigured=bool(secrets['miner']))
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    signal.signal(signal.SIGINT, lambda *_: stopping.append(True))
    while not stopping:
        now = time.monotonic()
        for item in items:
            item.poll(now)
        time.sleep(1)
    # Stop the explorer and relays first; give the node time to flush its state.
    for item in reversed(items):
        item.stop(120 if item.name == 'node' else 10)
    log(event='supervisor-stopped')


if __name__ == '__main__':
    main()
