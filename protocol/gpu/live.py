"""Supervise the GPU half of a hosted NOET network (development, testnet or mainnet) on one Pod.

Runs as root only to prepare private files, then starts every service as the
restricted worker (UID 10001): three mutual-TLS relays to the hosted node, the
real verifier receiver and the continuous miner. Plaintext sockets stay on
loopback. Configuration lives in /workspace/noet-live (mode 0700):

  gpu-bundle.b64   relay certificates, keys and peer pins (deploy/certs.py)
  live.json        {"node": "IP", "verdictPort": 17443, "rpcPort": 18443,
                    "rewardAddress": "nwrk1...", "gateway": "https://..." (optional),
                    "network": "dev" | "testnet" | "mainnet" (default dev),
                    "producerGpu": 0, "verifierGpu": 1 (optional, a two-GPU Pod),
                    "mine": false (optional: a verifier host, such as an 80 GB GPU
                    holding the Qwen3-14B replay, runs no miner)}
  shared-secret    64 hex characters, identical to the node's verdict secret
  miner-credentials  "miner:<password>" matching the node's rpcauth entry
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

LIVE = Path('/workspace/noet-live')
ROOT = Path('/workspace/noet-gpu')
OUT = ROOT / 'output/live'
UID = 10001
REQUEST_TLS_PORT, REQUEST_PORT, VERDICT_PORT, RPC_PORT = 16443, 18555, 18556, 30443
PREFIXES = {'dev': 'nwrk1', 'testnet': 'tnoet1', 'mainnet': 'noet1'}
# The GGUF files a live Pod may hold. The miner identifies each by its pinned
# checksum and runs the one each height needs, across a model switch too
# (design/MODEL-SWITCH.md).
GGUFS = ('qwen3-0.6b-bf16.gguf', 'qwen3-14b-bf16.gguf')
# A service that ran this long before exiting starts again at once; only a
# crash loop waits longer (2, 4, ... 120 s) between starts.
HEALTHY_SECONDS = 600


def log(**event):
    event['at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    with (OUT / 'supervisor.jsonl').open('a') as stream:
        stream.write(json.dumps(event, sort_keys=True) + '\n')


def owned(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, 'w') as stream:
        stream.write(data)
    os.chown(path, UID, UID)
    return path


def load(live=LIVE):
    settings = json.loads((live / 'live.json').read_text())
    prefix = PREFIXES.get(settings.setdefault('network', 'dev'))
    if prefix is None:
        raise ValueError('live.json network must be dev, testnet or mainnet')
    if not re.fullmatch(r'[0-9a-fA-F.:]+', settings['node']) or not settings['rewardAddress'].startswith(prefix):
        raise ValueError(f'live.json needs the node IP address and a {prefix} reward address')
    if settings['network'] != 'dev' and not settings.get('gateway'):
        raise ValueError('Only the development network supports the direct RPC miner; set gateway')
    for key in ('producerGpu', 'verifierGpu'):
        if key in settings and (type(settings[key]) is not int or not 0 <= settings[key] <= 9):
            raise ValueError(key + ' must be a GPU index from 0 to 9')
    if type(settings.setdefault('mine', True)) is not bool:
        raise ValueError('live.json mine must be true or false')
    secret = (live / 'shared-secret').read_text().strip()
    if not re.fullmatch('[0-9a-f]{64}', secret):
        raise ValueError('shared-secret must be 64 lowercase hex characters')
    if not re.fullmatch(r'miner:\S{16,}', (live / 'miner-credentials').read_text().strip()):
        raise ValueError('miner-credentials must be miner:<password>')
    bundle = json.loads(base64.b64decode((live / 'gpu-bundle.b64').read_bytes(), validate=True))
    return settings, secret, bundle


def relay_configs(settings, bundle, folder):
    tls = folder / 'tls'
    owned(tls / 'ca.pem', bundle['ca'], 0o644)
    configs = {}
    for name in ('verifier-request', 'verifier-verdict', 'miner-rpc'):
        item = bundle[name]
        owned(tls / (name + '.pem'), item['cert'], 0o644)
        owned(tls / (name + '.key'), item['key'])
        configs[name] = {'ca_file': str(tls / 'ca.pem'), 'cert_file': str(tls / (name + '.pem')),
                         'key_file': str(tls / (name + '.key')), 'peer_sha256': item['peer_sha256'],
                         'server_name': item['server_name'], 'public_tls': True}
    configs['verifier-request'].update(mode='server', listen_host='0.0.0.0', listen_port=REQUEST_TLS_PORT,
                                       target_host='127.0.0.1', target_port=REQUEST_PORT)
    configs['verifier-verdict'].update(mode='client', listen_host='127.0.0.1', listen_port=VERDICT_PORT,
                                       target_host=settings['node'], target_port=int(settings.get('verdictPort', 17443)))
    configs['miner-rpc'].update(mode='client', listen_host='127.0.0.1', listen_port=RPC_PORT,
                                target_host=settings['node'], target_port=int(settings.get('rpcPort', 18443)))
    return {name: owned(folder / (name + '.json'), json.dumps(config, sort_keys=True)) for name, config in configs.items()}


def worker(command, env=None):
    base = ['setpriv', f'--reuid={UID}', f'--regid={UID}', '--clear-groups', '--bounding-set=-all',
            '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs']
    return base + command


class Service:
    def __init__(self, name, command, env, log_name):
        self.name, self.command, self.env, self.log_name = name, command, env, log_name
        self.process, self.failures, self.next_start, self.started_at = None, 0, 0.0, 0.0

    def poll(self, now):
        if self.process and self.process.poll() is None:
            return
        if self.process:
            log(service=self.name, event='exited', code=self.process.returncode)
            if now - self.started_at >= HEALTHY_SECONDS:
                self.failures = 0
            self.failures += 1
            self.next_start = now + min(120, 2 ** min(self.failures, 7))
            self.process = None
        if now >= self.next_start:
            stream = (OUT / self.log_name).open('ab')
            self.process = subprocess.Popen(self.command(), env=self.env, stdin=subprocess.DEVNULL,
                                            stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            stream.close()
            self.started_at = now
            log(service=self.name, event='started', pid=self.process.pid)

    def stop(self):
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)


def services(settings, secret, configs):
    relay_env = {'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(ROOT / 'relay'), 'PYTHONDONTWRITEBYTECODE': '1',
                 'HOME': '/tmp/noet-gpu-home'}
    items = [Service(name, lambda path=path: worker(['/usr/bin/python3', '-m', 'noet.secure_transport',
                                                     '--config', str(path)]), relay_env, name + '.log')
             for name, path in sorted(configs.items())]
    # run.sh places the verifier and the engine; a two-GPU Pod splits them.
    gpus = {name: str(settings[key]) for name, key in (('NOET_PRODUCER_GPU', 'producerGpu'),
                                                        ('NOET_VERIFIER_GPU', 'verifierGpu')) if key in settings}
    verify_env = {'PATH': '/usr/bin:/bin', 'VALIDATOR_SHARED_SECRET': secret,
                  'VALIDATOR_PUSH_PORT': str(REQUEST_PORT), 'VALIDATOR_PULL_PORT': str(VERDICT_PORT), **gpus}
    sessions = OUT / 'verifier'

    def verifier():
        target = sessions / time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        return ['bash', str(ROOT / 'run.sh'), str(ROOT / 'checks/verify.py'), '--out', str(target), '--seconds', '86400',
                '--capture-detector']
    items.append(Service('verifier', verifier, verify_env, 'verifier.log'))
    if settings.get('gateway'):
        # Mine through the public gateway exactly as any outside miner does.
        models = [ROOT / name for name in GGUFS if (ROOT / name).exists()] or [ROOT / GGUFS[0]]
        miner = ['bash', str(ROOT / 'run.sh'), str(ROOT / 'checks/noet_miner.py'), '--network', settings['network'],
                 '--gateway', settings['gateway'], '--address', settings['rewardAddress'],
                 '--engine', str(ROOT / 'engine/build/bin/llama-server'),
                 *[part for path in models for part in ('--model', str(path))], '--out', str(OUT / 'public-miner')]
    else:
        miner = ['bash', str(ROOT / 'run.sh'), str(ROOT / 'checks/miner.py'), '--out', str(OUT / 'miner'),
                 '--credentials', str(LIVE / 'worker/miner-credentials'), '--reward-address', settings['rewardAddress']]
    if settings.get('mine', True):
        items.append(Service('miner', lambda: miner, {'PATH': '/usr/bin:/bin', **gpus}, 'miner.log'))
    return items


def main():
    if os.geteuid() != 0:
        raise SystemExit('Start as root; services drop to the restricted worker')
    OUT.mkdir(parents=True, exist_ok=True)
    for path in (OUT, OUT / 'verifier', OUT / 'miner', OUT / 'public-miner'):
        path.mkdir(exist_ok=True)
        os.chown(path, UID, UID)
    settings, secret, bundle = load()
    # The worker may traverse the root-owned directory but not read its
    # secrets; its own copies live in worker/ (owned by UID 10001).
    os.chmod(LIVE, 0o711)
    worker_dir = LIVE / 'worker'
    worker_dir.mkdir(mode=0o700, exist_ok=True)
    os.chown(worker_dir, UID, UID)
    owned(worker_dir / 'miner-credentials', (LIVE / 'miner-credentials').read_text().strip() + '\n')
    configs = relay_configs(settings, bundle, worker_dir)
    items = services(settings, secret, configs)
    log(event='live-started', node=settings['node'], services=[item.name for item in items])
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    while not stopping:
        now = time.monotonic()
        for item in items:
            item.poll(now)
        time.sleep(2)
    for item in reversed(items):
        item.stop()
    log(event='live-stopped')


if __name__ == '__main__':
    sys.exit(main())
