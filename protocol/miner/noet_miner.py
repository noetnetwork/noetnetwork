#!/usr/bin/env python3
"""NOET miner. Leases work from a public mining gateway, generates the proof on
your GPU with the pinned engine and model, and submits it. Block rewards are
paid by consensus straight to your own address; this program never holds keys.

  python3 noet_miner.py --address tnoet1... [--gateway https://...]
  python3 noet_miner.py --address tnoet1... --check     # test this machine first

Options for watching it: --dashboard 127.0.0.1:8090 serves a status page on this
machine; --notify https://... posts a JSON message (Slack, Discord and ntfy take
it as is) when a block is found or rejected, the miner stalls, or a model switch
is near. --gpu 1 pins this miner to one GPU; run one miner per GPU, each with
its own --out folder, on a multi-GPU machine.

Each attempt is useful work: the gateway hands out a public AI request (or, when
nobody is waiting, an open-dataset item), the model's run behind the proof is
the answer, and the miner sends that answer back before submitting proofs.
Requests and answers are public. --no-tasks mines with a fixed prompt instead.

The gateway cannot change what you are paid: the reward script is built from
your address and bound into the block template the node issues. The node's own
verifier decides every block; this program never retries a rejected proof with
a different seed or target.
"""
import argparse
import base64
from collections import Counter, deque
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

VERSION = '0.2.0'
HERE = Path(__file__).resolve().parent
MODEL = 'Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca'
GGUF_SHA256 = '8fa11f4d815167c4f6e3c91b25aebde25d8346a8f11228e744016592532b0700'
SWITCH_MODEL = 'Qwen/Qwen3-14B@40c069824f4251a91eefaf281ebe4c544efd3e18'
# The consensus models: the genesis model and, from a network's switch height
# (design/MODEL-SWITCH.md), Qwen3-14B. Each GGUF is a reproducible conversion;
# GPU memory is what the engine needs for the model and an attempt.
MODELS = {
    MODEL: {'gguf': 'qwen3-0.6b-bf16.gguf', 'sha256': GGUF_SHA256, 'minGpuMib': 6 * 1024},
    SWITCH_MODEL: {'gguf': 'qwen3-14b-bf16.gguf', 'minGpuMib': 34 * 1024,
                   'sha256': 'b4ac198af720a0d4359a084f162f75553a1050e82869c96e583d3104f599dbe9'},
}
PROOF_LIMIT = 2 * 1024 * 1024
# GPUs the engine is compiled for (Dockerfile CUDA_ARCHITECTURES), and the driver CUDA 12.8 needs.
ARCHITECTURES = ('8.0', '8.6', '8.9', '9.0', '12.0')
MIN_DRIVER = (570, 0)
MIN_DISK_BYTES = 2 * 1024 ** 3
# Each network pins its genesis so a gateway cannot point you at another chain,
# and its model switch, (height, model) or None (protocol/mainnet/network.json).
NETWORKS = {
    'mainnet': {'gateway': 'https://noet-mainnet.fly.dev:9443', 'hrp': 'noet', 'profile': 'noet-mainnet-1',
                'genesis': 'fc3e73901e2831df8457ea56f56cf92cd23018453439a0c1d402cbab61f45a54',
                'switch': (3500, SWITCH_MODEL)},
    'testnet': {'gateway': 'https://noet-testnet.fly.dev:9443', 'hrp': 'tnoet', 'profile': 'noet-testnet-1',
                'genesis': '2da97e8dadb7fe27645263d3e732211cb67650adbc02a016102cc7390785cdaa',
                'switch': None},
}


class ModelUnavailable(RuntimeError):
    """The block being mined needs a model this miner was not given."""
    def __init__(self, model, height):
        super().__init__(f'Height {height} needs {model}; give its GGUF with --model')
        self.model, self.height = model, height


class BlockVerifying(RuntimeError):
    """Another block at the leased height awaits its Full verdict, so this work could only go stale."""
    def __init__(self, height, seconds):
        super().__init__(f'A block at height {height} is being verified ({seconds} s so far)')
        self.height, self.seconds = height, seconds


# A miner without the switch model is told this many blocks (about two days) ahead.
SWITCH_NOTICE_BLOCKS = 288


def switch_notice(network, models, height):
    """What to tell a miner that lacks the model the network switches to soon, or None."""
    switch = network.get('switch')
    if not switch or switch[1] in models or not switch[0] - SWITCH_NOTICE_BLOCKS <= height < switch[0]:
        return None
    return (f'From height {switch[0]} ({switch[0] - height} blocks from now) blocks need {switch[1]}. '
            'Convert it (convert.sh qwen3-14b) and restart the miner with both --model files, '
            'or this miner stops at the switch.')


# One plain sentence per attempt outcome, next to the machine-readable fields.
OUTCOME_NOTES = {
    'green': 'Block found and fully verified; the reward is yours.',
    'no-eligible-proof': 'No proof met the target this time; this is the usual result.',
    'stale': 'Another miner\'s block at this height was verified first.',
    'red': 'The verifier rejected the block; see this attempt\'s folder.',
    'amber': 'The verifier could not decide on the block; see this attempt\'s folder.',
    'timeout': 'No verdict within 15 minutes; check the block in the explorer.',
}


def network_model(network, height):
    """The model every block at `height` carries on this network."""
    switch = network.get('switch')
    return switch[1] if switch and height >= switch[0] else MODEL


class GatewayError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Gateway:
    def __init__(self, url, timeout=60):
        parts = urlsplit(url)
        try:
            loopback = ipaddress.ip_address(parts.hostname or '').is_loopback
        except ValueError:
            loopback = parts.hostname == 'localhost'
        if parts.scheme != 'https' and not (parts.scheme == 'http' and loopback):
            raise ValueError('The gateway must use HTTPS')
        if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/'):
            raise ValueError('Give the gateway as https://host[:port]')
        self.base, self.timeout = url.rstrip('/'), timeout
        # The gateway publishes only how many miners run each version (--no-version sends none).
        self.agent = 'noet-miner/' + VERSION
        self.opener = build_opener(ProxyHandler({}), NoRedirect)

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=data, headers={
            'Content-Type': 'application/json', 'User-Agent': self.agent})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(1024 * 1024 + 1)
        except HTTPError as error:
            try:
                message = json.loads(error.read(4096)).get('error', error.reason)
            except ValueError:
                message = str(error.reason)
            raise GatewayError(error.code, str(message)[:200]) from None
        except (URLError, TimeoutError, ConnectionError) as error:
            raise GatewayError(0, 'Gateway unreachable: ' + str(getattr(error, 'reason', error))[:200]) from None
        if len(raw) > 1024 * 1024:
            raise GatewayError(0, 'Gateway response is too large')
        return json.loads(raw)

    def info(self):
        return self.call('/mining/info')

    def lease(self, address, tasks=False):
        return self.call('/mining/work', {'address': address, **({'tasks': True} if tasks else {})})

    def answer(self, req_id, lease, task, text, truncated):
        return self.call('/mining/answer', {'reqId': req_id, 'lease': lease, 'task': task,
                                            'answer': text, 'truncated': truncated})

    def release(self, req_id, lease):
        return self.call('/mining/release', {'reqId': req_id, 'lease': lease})

    def submit(self, req_id, lease, raw):
        return self.call('/mining/submit', {'reqId': req_id, 'lease': lease,
                                            'proof': base64.b64encode(raw).decode()})

    def block(self, block_hash):
        return self.call('/mining/blocks/' + block_hash)


def checked_info(gateway, network):
    info = gateway.info()
    if network['genesis'] and info.get('genesis') != network['genesis']:
        raise RuntimeError('The gateway serves a different network than the one you chose')
    if info.get('addressPrefix') != network['hrp'] + '1':
        raise RuntimeError('The gateway uses a different address format')
    # The node lists the genesis model and, once upgraded for a switch, the switch
    # model. Any other model means this miner is outdated or the gateway is wrong.
    switch = network.get('switch')
    known = {MODEL, *([switch[1]] if switch else [])}
    models = info.get('models') or []
    if not models or any(m.get('model') not in known or m.get('difficulty') != 1000000 for m in models) or \
            network_model(network, info.get('height', 0) + 1) not in {m.get('model') for m in models}:
        raise RuntimeError('The network model differs from the models this miner runs; update the miner')
    return info


CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'


def _polymod(values):
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ v
        for i, g in enumerate((0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3)):
            chk ^= g if (top >> i) & 1 else 0
    return chk


def payout_script(address, hrp):
    """scriptPubKey for a segwit address (BIP173 bech32 / BIP350 bech32m)."""
    sep = address.rfind('1')
    if address[:sep] != hrp or len(address) > 90:
        raise ValueError('Address is for another network')
    data = [CHARSET.index(c) for c in address[sep + 1:]]
    const = _polymod([ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + data)
    version, payload = data[0], data[1:-6]
    if const != (1 if version == 0 else 0x2bc830a3):
        raise ValueError('Address checksum is invalid')
    acc, bits, program = 0, 0, bytearray()
    for v in payload:
        acc, bits = (acc << 5) | v, bits + 5
        while bits >= 8:
            bits -= 8
            program.append((acc >> bits) & 0xff)
    if bits >= 5 or (acc << (8 - bits)) & 0xff or not 2 <= len(program) <= 40 or version > 16:
        raise ValueError('Address payload is invalid')
    return bytes([0x50 + version if version else 0, len(program)]) + bytes(program)


def _sha256d(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def _first_paid_output(coinbase):
    """(value, script) of the first positive-value output of a no-witness coinbase."""
    at = 4

    def compact():
        nonlocal at
        first = coinbase[at]
        size = {0xfd: 2, 0xfe: 4, 0xff: 8}.get(first, 0)
        value = int.from_bytes(coinbase[at + 1:at + 1 + size], 'little') if size else first
        at += 1 + size
        return value
    for _ in range(compact()):  # inputs
        at += 36
        size = compact()  # read the length before moving past the script
        at += size + 4
    for _ in range(compact()):
        value = int.from_bytes(coinbase[at:at + 8], 'little')
        at += 8
        size = compact()
        script = coinbase[at:at + size]
        at += size
        if value > 0:
            return value, script
    raise ValueError('The coinbase pays nobody')


def verify_payout(job, address, hrp):
    """True when the work provably pays `address`; None when the node sends no proof.

    The coinbase's txid, folded up its merkle branch (coinbase is leaf 0), must
    equal the merkle root inside the header prefix the miner will hash, and the
    first paid output must be this miner's script.
    """
    if 'coinbase_tx' not in job:
        return None
    coinbase = bytes.fromhex(job['coinbase_tx'])
    root = _sha256d(coinbase)
    for sibling in job.get('coinbase_merkle_branch', []):
        root = _sha256d(root + bytes.fromhex(sibling))
    if bytes.fromhex(job['header_prefix'])[36:68] != root:
        raise RuntimeError('The work unit\'s coinbase does not match its header; refusing it')
    value, script = _first_paid_output(coinbase)
    if script != payout_script(address, hrp):
        raise RuntimeError('The work unit pays a different address; refusing it')
    return True


def checked_address(address, network):
    if not re.fullmatch(re.escape(network['hrp']) + r'1[023456789acdefghjklmnpqrstuvwxyz]{8,85}', address or ''):
        raise ValueError(f'Use a {network["hrp"]}1... address from your NOET wallet')
    return address


def settle(gateway, block, timeout, height=None, sleep=time.sleep, clock=time.monotonic):
    """Wait for the node's Full verdict on our block at this height. Once the chain
    holds another block at our height, ours has lost the race (the node extends
    only its tip): "stale", seen on two polls in a row so that our own block
    connecting between the two lookups is not taken for a loss."""
    until = clock() + timeout
    behind = 0
    while clock() < until:
        try:
            verdict = gateway.block(block)
        except GatewayError:
            verdict = {}
        if verdict.get('full') in ('red', 'amber') or (verdict.get('full') == 'green' and verdict.get('inBestChain')):
            return verdict
        tip = None
        if height is not None and verdict and not verdict.get('inBestChain'):
            try:
                tip = gateway.info().get('height')
            except GatewayError:
                pass
        behind = behind + 1 if isinstance(tip, int) and tip >= height else 0
        if behind >= 2:
            return {**verdict, 'full': 'stale', 'tipHeight': tip}
        sleep(5)
    return None


# Where an answer ends: the next entry of the question-and-answer document, or an
# end token (rendered visibly by the engine's preserved_tokens setting).
ANSWER_ENDS = ('\nQuestion:', '\nAnswer:', '<|im_end|>', '<|endoftext|>', '<|im_start|>')
TASK_ID = re.compile(r'[0-9a-f]{24}')


def answer_from(text):
    """(answer, truncated): the reply up to the end of its entry. The proof covers
    a fixed number of tokens, so the model writes on past its answer."""
    ends = [i for i in (text.find(marker) for marker in ANSWER_ENDS) if i >= 0]
    reply = text[:min(ends)] if ends else text
    return re.sub(r'^\s*<think>.*?</think>', '', reply, flags=re.S).strip()[:8000], not ends


def answer_task(gateway, job, lease, task, folder):
    """Send the answer for this lease's task; never fails the attempt."""
    try:
        completion = json.loads((folder / 'generation/completion.json').read_text())
        text, truncated = answer_from(completion['choices'][0]['text'])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return {'id': task['id'], 'answered': False, 'detail': 'no completion'}
    if not text:
        return {'id': task['id'], 'answered': False, 'detail': 'empty answer'}
    try:
        gateway.answer(job['req_id'], lease['lease'], task['id'], text, truncated)
    except GatewayError as error:
        return {'id': task['id'], 'answered': False, 'detail': str(error)}
    return {'id': task['id'], 'kind': task.get('kind'), 'answered': True, 'chars': len(text), 'truncated': truncated}


def attempt(gateway, network, info, address, paths, out, number, flash_attention='off', runner=subprocess.Popen,
            tasks=True):
    lease = gateway.lease(address, tasks)
    job = lease['job']
    task = lease.get('task') if tasks else None
    if task is not None and not (isinstance(task, dict) and TASK_ID.fullmatch(str(task.get('id', ''))) and
                                 isinstance(task.get('prompt'), str)):
        raise RuntimeError('The gateway sent an invalid task')
    if lease.get('rewardAddress') != address:
        raise RuntimeError('The gateway changed the reward address')
    model = network_model(network, job['height'])
    if model not in paths['models']:
        try:
            gateway.release(job['req_id'], lease['lease'])
        except GatewayError:
            pass
        raise ModelUnavailable(model, job['height'])
    verifying = lease.get('verifying')
    if isinstance(verifying, dict) and isinstance(verifying.get('height'), int) and \
            verifying['height'] >= job['height']:
        try:
            gateway.release(job['req_id'], lease['lease'])
        except GatewayError:
            pass
        raise BlockVerifying(verifying['height'], int(verifying.get('seconds') or 0))
    # Check the payout from the node's own coinbase, not the gateway's word.
    payout_proven = verify_payout(job, address, network['hrp'])
    header = bytes.fromhex(job['header_prefix'])
    record = {'profile': network['profile'], 'genesis': info['genesis'], 'parent': header[4:36][::-1].hex(),
              'model': {'model_name': model.split('@')[0], 'model_commit': model.split('@')[1],
                        'difficulty': 1000000}, 'job': job, 'task': task}
    folder = out / f'attempt-{number:06d}'
    folder.mkdir(mode=0o700)
    (folder / 'job.json').write_text(json.dumps(record, indent=2) + '\n')
    result = {'attempt': number, 'height': job['height'], 'model': model, 'startedAt': time.time(),
              'submissions': [], 'payoutVerified': bool(payout_proven)}
    started = time.monotonic()
    with (folder / 'producer.log').open('xb') as log:
        producer = runner([sys.executable, str(HERE / 'generate.py'), '--job', str(folder / 'job.json'),
                           '--engine', str(paths['engine']), '--model', str(paths['models'][model]),
                           '--out', str(folder / 'generation'), '--accelerator', 'cuda',
                           '--flash-attention', flash_attention],
                          stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                          env={**os.environ, 'NOET_MODEL_ID': model,
                               **({'CUDA_VISIBLE_DEVICES': str(paths['gpu'])} if paths.get('gpu') is not None else {})})
        try:
            producer.wait(timeout=900)
        except subprocess.TimeoutExpired:
            result['producerTimeout'] = True
        finally:
            try:
                os.killpg(producer.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            producer.wait(timeout=10)
    result['producerExit'] = producer.returncode
    result['producerSeconds'] = round(time.monotonic() - started, 1)
    if task is not None:
        result['task'] = answer_task(gateway, job, lease, task, folder)
    for proof in sorted((folder / 'generation/proofs').glob(f'{job["req_id"]}_*.bin')):
        with proof.open('rb') as source:
            raw = source.read(PROOF_LIMIT + 1)
        if len(raw) > PROOF_LIMIT:
            raise ValueError('Oversized proof')
        try:
            submitted = gateway.submit(job['req_id'], lease['lease'], raw)
        except GatewayError as error:
            submitted = {'status': 'gateway-error', 'detail': str(error)}
        entry = {'proofSha256': hashlib.sha256(raw).hexdigest(), 'response': submitted}
        result['submissions'].append(entry)
        if submitted.get('status') not in ('accepted', 'accepted_pending_connect'):
            continue
        verdict = settle(gateway, submitted['block_hash'], 900, job['height'])
        entry['fullValidation'] = verdict
        result['outcome'] = 'timeout' if verdict is None else verdict['full']
        if verdict and verdict['full'] == 'green':
            result['block'], result['acceptedHeight'] = submitted['block_hash'], verdict.get('height')
        break
    if 'outcome' not in result:
        result['outcome'] = 'no-eligible-proof'
        # Free the gateway slot now rather than when the lease expires.
        try:
            gateway.release(job['req_id'], lease['lease'])
            result['leaseReleased'] = True
        except GatewayError:
            result['leaseReleased'] = False
    result['finishedAt'] = time.time()
    (folder / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def gpus(run=subprocess.run, index=None):
    """name, driver, memory MiB and compute capability per GPU (or the one at `index`), from nvidia-smi."""
    if not shutil.which('nvidia-smi'):
        raise RuntimeError('nvidia-smi was not found: install the NVIDIA driver (or run Docker with --gpus all)')
    out = run(['nvidia-smi', '--query-gpu=name,driver_version,memory.total,compute_cap',
               '--format=csv,noheader,nounits', *(['-i', str(index)] if index is not None else [])],
              capture_output=True, text=True, timeout=30, check=True).stdout
    rows = []
    for line in out.strip().splitlines():
        name, driver, memory, cap = [field.strip() for field in line.split(',')]
        rows.append({'name': name, 'driver': driver, 'memoryMiB': int(float(memory)), 'computeCap': cap})
    if not rows:
        raise RuntimeError('No NVIDIA GPU is visible')
    return rows


def preflight(args, network, gateway, run=subprocess.run):
    """Each check as (name, ok, detail). Nothing here leases work or spends GPU time."""
    checks = []

    def check(name, test):
        try:
            detail = test()
            checks.append((name, True, detail))
        except Exception as error:  # every failure becomes a readable line
            checks.append((name, False, str(error)[:300]))

    def gpu():
        found = gpus(run, getattr(args, 'gpu', None))
        problems = []
        for row in found:
            driver = tuple(int(x) for x in re.findall(r'\d+', row['driver'])[:2])
            if driver < MIN_DRIVER:
                problems.append(f'{row["name"]}: driver {row["driver"]} is older than {MIN_DRIVER[0]} (CUDA 12.8)')
            if row['computeCap'] not in ARCHITECTURES:
                problems.append(f'{row["name"]}: compute capability {row["computeCap"]} is not supported '
                                f'({", ".join(ARCHITECTURES)})')
            needed = max(MODELS[m]['minGpuMib'] for m in mined_models())
            if row['memoryMiB'] < needed:
                problems.append(f'{row["name"]}: {row["memoryMiB"]} MiB of GPU memory; {needed} MiB needed')
        if problems:
            raise RuntimeError('; '.join(problems))
        return ', '.join(f'{r["name"]} ({r["memoryMiB"]} MiB, driver {r["driver"]})' for r in found)

    def engine():
        if not args.engine.is_file() or not os.access(args.engine, os.X_OK):
            raise RuntimeError(f'not found or not executable at {args.engine}; run setup.sh or use the Docker image')
        return str(args.engine)

    def mined_models():
        """The models this miner will run: the ones it was given, or the network's current one."""
        return sorted(state.get('models') or [network_model(network, state.get('height', 0) + 1)])

    def model():
        state['models'] = models_given(args.model)
        current = network_model(network, state.get('height', 0) + 1)
        if current not in state['models']:
            raise RuntimeError(f'the network mines {current} now; give its GGUF with --model')
        return ', '.join(f'{m.split("@")[0]} (pinned checksum matches)' for m in sorted(state['models']))

    def disk():
        folder = args.out if args.out.exists() else args.out.resolve().parent
        free = shutil.disk_usage(folder).free
        if free < MIN_DISK_BYTES:
            raise RuntimeError(f'{free // 1024 ** 2} MiB free in {folder}; 2 GiB needed for attempts')
        return f'{free // 1024 ** 3} GiB free'

    def network_check():
        info = checked_info(gateway, network)
        state['height'] = info['height']
        return f'{gateway.base} at height {info["height"]}'

    def python():
        version = sys.version.split()[0]
        if sys.version_info < (3, 10):
            raise RuntimeError(f'Python {version}; 3.10 or newer is needed')
        return version

    state = {}
    check('Python', python)
    check('Reward address', lambda: checked_address(args.address, network))
    check('Gateway and network', network_check)
    check('Model', model)
    check('NVIDIA GPU', gpu)
    check('Engine', engine)
    check('Disk space', disk)
    order = ('Python', 'Reward address', 'NVIDIA GPU', 'Engine', 'Model', 'Disk space', 'Gateway and network')
    return sorted(checks, key=lambda row: order.index(row[0]))


# What to do about each failed --check line.
FIXES = {
    'Python': 'Install Python 3.10 or newer, or use the miner image (ghcr.io/noetnetwork/noet-miner).',
    'Reward address': 'Copy a receive address from app.noet.network/wallet; mainnet addresses start with noet1.',
    'Gateway and network': 'Check the machine reaches the internet on port 9443. If the gateway is restarting, '
                           'cloud-start.sh retries every five minutes; status: www.noet.network/network.',
    'Model': 'Run ./convert.sh . models (and ./convert.sh . models qwen3-14b for the switch), or use the image, '
             'which carries the pinned files. A checksum mismatch means a partial download: delete and convert again.',
    'NVIDIA GPU': 'Use an Ampere or newer GPU (RTX 30, A-series, RTX 40, H100, RTX 50) with driver 570 or newer. '
                  'In Docker, start with --gpus all. Qwen3-14B needs 34 GiB of GPU memory.',
    'Engine': 'Run ./setup.sh --build in the miner folder, or use the miner image.',
    'Disk space': 'Free 2 GiB for attempt folders, or point --out at a larger disk. Old attempt-* folders can go.',
}


def next_attempt(out):
    """One past the highest attempt folder; refused leases leave no folder, so counting would collide."""
    numbers = [int(p.name[8:]) for p in out.glob('attempt-*') if p.name[8:].isdigit()]
    return max(numbers, default=-1) + 1


def wait(seconds, stopping, sleep=time.sleep):
    """Sleep in short steps so Ctrl-C or SIGTERM stops the miner promptly."""
    end = time.monotonic() + seconds
    while not stopping and time.monotonic() < end:
        sleep(min(1.0, max(0.0, end - time.monotonic())))


# The producer also exits non-zero when no proof meets the target, a normal
# result reached only after the model has run (22-29 s on an A5000). A broken
# setup fails before that (0.1 s for a missing network profile).
FAST_FAILURE_SECONDS = 5
# Seconds between leases while another block awaits its verdict.
VERDICT_WAIT = 15


def producer_failed(outcome):
    """The proof producer failed at once, before running the model, and produced nothing."""
    return (outcome.get('producerExit') not in (0, None) and not outcome.get('producerTimeout')
            and not outcome.get('submissions')
            and outcome.get('producerSeconds', 0) < FAST_FAILURE_SECONDS)


def backoff(failures, status):
    """Seconds to wait after the nth consecutive gateway failure."""
    if status == 429:
        return 60
    return min(300, 15 * 2 ** min(failures - 1, 5))


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def models_given(paths, check=True):
    """{model: path} for the GGUF files given, each identified by its pinned checksum
    (or, with check=False, by its file name)."""
    found = {}
    for path in paths:
        if not path.is_file():
            raise RuntimeError(f'Model not found at {path}. Run convert.sh or use the Docker image.')
        if check:
            digest = file_sha256(path)
            model = next((m for m, pin in MODELS.items() if pin['sha256'] == digest), None)
        else:
            model = next((m for m, pin in MODELS.items() if pin['gguf'] == path.name), None)
        if model is None:
            raise RuntimeError(f'{path} differs from every pinned NOET model. Fetch it again.')
        found[model] = path
    return found


def default_models():
    """The GGUF files for the models in NOET_MODEL_DIR, one or more directories
    separated by ':' (the image: /models, then /work/models for a model built on
    first start), or the genesis model's path."""
    roots = [Path(part) for part in os.environ.get('NOET_MODEL_DIR', '/models').split(':') if part]
    found = []
    for pin in MODELS.values():
        path = next((root / pin['gguf'] for root in roots if (root / pin['gguf']).is_file()), None)
        if path:
            found.append(path)
    return found or [Path(os.environ.get('NOET_MODEL', roots[0] / MODELS[MODEL]['gguf']))]


class Tally:
    """What the dashboard shows: totals since start, the latest attempts and events."""
    def __init__(self, address, network, models, gpu=None, run=subprocess.run, clock=time.time):
        self.lock, self.run, self.clock = threading.Lock(), run, clock
        self.state = {'version': VERSION, 'address': address, 'network': network, 'models': sorted(models),
                      'gpu': gpu, 'startedAt': clock(), 'attempts': 0, 'blocksFound': 0}
        self.outcomes, self.attempts, self.events = Counter(), deque(maxlen=20), deque(maxlen=10)
        self.gpu_reading = (0.0, None)

    def add(self, record):
        with self.lock:
            if 'outcome' in record:
                self.state['attempts'] += 1
                self.state['blocksFound'] = record.get('blocksFound', self.state['blocksFound'])
                self.outcomes[record['outcome']] += 1
                self.attempts.appendleft({'at': self.clock(), **record})
            elif record.get('event') != 'mining':
                self.events.appendleft({'at': self.clock(), **record})

    def gpu(self):
        """Temperature, use and memory from nvidia-smi, read at most every 10 s."""
        stamp, reading = self.gpu_reading
        if self.clock() - stamp < 10:
            return reading
        try:
            index = [] if self.state['gpu'] is None else ['-i', str(self.state['gpu'])]
            out = self.run(['nvidia-smi', '--query-gpu=index,name,temperature.gpu,utilization.gpu,memory.used,'
                            'memory.total,power.draw', '--format=csv,noheader,nounits', *index],
                           capture_output=True, text=True, timeout=10, check=True).stdout
            keys = ('index', 'name', 'temperatureC', 'utilizationPercent', 'memoryUsedMiB', 'memoryMiB', 'powerW')
            reading = [dict(zip(keys, (f.strip() for f in line.split(',')))) for line in out.strip().splitlines()]
        except (OSError, subprocess.SubprocessError, ValueError):
            reading = None
        self.gpu_reading = (self.clock(), reading)
        return reading

    def snapshot(self):
        gpu = self.gpu()
        with self.lock:
            return {**self.state, 'uptimeSeconds': int(self.clock() - self.state['startedAt']),
                    'outcomes': dict(self.outcomes), 'recentAttempts': list(self.attempts),
                    'events': list(self.events), 'gpus': gpu}


DASHBOARD = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>NOET miner</title><style>
:root{color-scheme:light dark;--bg:#fbfaf7;--fg:#1d1b17;--muted:#6b665c;--line:#e4e0d6;--good:#1f7a4d;--bad:#b4362a}
@media (prefers-color-scheme:dark){:root{--bg:#141310;--fg:#ece8df;--muted:#a19b8e;--line:#2c2a25;--good:#58c08b;--bad:#ef7b6e}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:960px;margin:0 auto;padding:24px 16px}h1{font-size:20px;margin:0 0 4px}p{color:var(--muted);margin:0 0 20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px}
.tile{border:1px solid var(--line);border-radius:10px;padding:12px}.tile b{display:block;font-size:22px;font-variant-numeric:tabular-nums}
.tile span{color:var(--muted);font-size:13px}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);font-size:14px}th{color:var(--muted);font-weight:500}
.green{color:var(--good)}.red,.amber{color:var(--bad)}h2{font-size:16px;margin:24px 0 8px}a{color:inherit}
.wrap{overflow-x:auto}</style><main><h1>NOET miner</h1><p id="who">Loading</p>
<div class="grid" id="tiles"></div><h2>GPU</h2><div class="wrap"><table id="gpus"></table></div>
<h2>Latest attempts</h2><div class="wrap"><table id="attempts"></table></div><h2>Events</h2>
<div class="wrap"><table id="events"></table></div></main><script>
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const time=t=>new Date(t*1000).toLocaleTimeString();
const rows=(head,list)=>"<tr>"+head.map(h=>"<th>"+h+"</th>").join("")+"</tr>"+list.join("");
async function tick(){try{const s=await (await fetch("status.json",{cache:"no-store"})).json();
document.getElementById("who").innerHTML="Version "+esc(s.version)+" on "+esc(s.network)+", paying <a href=\"https://www.noet.network/#explorer/address/"+encodeURIComponent(s.address)+"\">"+esc(s.address)+"</a>";
const h=Math.floor(s.uptimeSeconds/3600),m=Math.floor(s.uptimeSeconds%3600/60),o=s.outcomes;
document.getElementById("tiles").innerHTML=[["Blocks found",s.blocksFound],["Attempts",s.attempts],["No proof",o["no-eligible-proof"]||0],["Stale",o.stale||0],["Rejected",(o.red||0)+(o.amber||0)],["Running",h+" h "+m+" min"]].map(([k,v])=>"<div class=tile><b>"+esc(v)+"</b><span>"+k+"</span></div>").join("");
document.getElementById("gpus").innerHTML=s.gpus?rows(["GPU","Temperature","Use","Memory","Power"],s.gpus.map(g=>"<tr><td>"+esc(g.index+" "+g.name)+"</td><td>"+esc(g.temperatureC)+" C</td><td>"+esc(g.utilizationPercent)+"%</td><td>"+esc(g.memoryUsedMiB)+" / "+esc(g.memoryMiB)+" MiB</td><td>"+esc(g.powerW)+" W</td></tr>")):"<tr><td>nvidia-smi is not available</td></tr>";
document.getElementById("attempts").innerHTML=s.recentAttempts.length?rows(["Time","Attempt","Height","Outcome","Note"],s.recentAttempts.map(a=>"<tr><td>"+time(a.at)+"</td><td>"+esc(a.attempt)+"</td><td>"+esc(a.height)+"</td><td class=\""+esc(a.outcome)+"\">"+esc(a.outcome)+"</td><td>"+esc(a.note)+"</td></tr>")):"<tr><td>No attempt has finished yet. The first takes about a minute.</td></tr>";
document.getElementById("events").innerHTML=s.events.length?rows(["Time","Event"],s.events.map(e=>"<tr><td>"+time(e.at)+"</td><td>"+esc(e.event||e.error||("gateway "+e.gateway))+": "+esc(e.detail)+"</td></tr>")):"<tr><td>Nothing to report.</td></tr>";
}catch(e){document.getElementById("who").textContent="The miner is not answering."}}
tick();setInterval(tick,5000);</script></html>"""


def dashboard(tally, host, port):
    """Serve the status page; bound to this machine unless you choose another address."""
    allowed = {host, 'localhost', '127.0.0.1', '[::1]'}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            # A Host check stops other websites reading the page through DNS rebinding.
            if self.headers.get('Host', '').rsplit(':', 1)[0] not in allowed:
                return self.send(403, 'text/plain', b'Forbidden')
            if self.path == '/':
                return self.send(200, 'text/html; charset=utf-8', DASHBOARD.encode())
            if self.path == '/status.json':
                return self.send(200, 'application/json', json.dumps(tally.snapshot()).encode())
            self.send(404, 'text/plain', b'Not found')

        def send(self, status, kind, body):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer((host.strip('[]'), port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# Consecutive failed attempts (gateway, producer or other errors) before a stall alert.
STALL_FAILURES = 5


class Notifier:
    """Posts one JSON message per notable event to a webhook; never blocks mining."""
    def __init__(self, url, address, post=None):
        parts = urlsplit(url)
        if parts.scheme != 'https' or not parts.hostname:
            raise ValueError('--notify needs an https:// webhook URL')
        self.url, self.address, self.failures, self.stalled, self.sending = url, address, 0, False, []
        self.post = post or self._post

    def _post(self, body):
        request = Request(self.url, data=json.dumps(body).encode(), method='POST',
                          headers={'Content-Type': 'application/json', 'User-Agent': 'noet-miner/' + VERSION})
        try:
            build_opener(NoRedirect).open(request, timeout=10).close()
        except (OSError, ValueError):
            pass  # an alert that cannot be sent must not stop the miner

    def message(self, record):
        """The alert text for this record, or None."""
        failed = 'gateway' in record or 'error' in record or record.get('event') == 'producer-failed'
        if failed:
            self.failures += 1
            if self.failures >= STALL_FAILURES and not self.stalled:
                self.stalled = True
                return (f'NOET miner stalled: {self.failures} failed attempts in a row. '
                        f'Latest: {record.get("detail", "")[:200]}')
            return None
        if 'outcome' in record:
            recovered = self.stalled
            self.failures, self.stalled = 0, False
            if record['outcome'] == 'green':
                return (f'NOET block found at height {record["height"]}, paid to {self.address}: '
                        f'https://www.noet.network/#explorer/block/{record.get("block")}')
            if record['outcome'] in ('red', 'amber'):
                return f'NOET verifier returned {record["outcome"]} for attempt {record["attempt"]}: {record["note"]}'
            return 'NOET miner is attempting again after the stall.' if recovered else None
        if record.get('event') in ('switch-ahead', 'model-switch'):
            return 'NOET model switch: ' + record['detail']
        return None

    def __call__(self, record):
        text = self.message(record)
        if text:
            body = {'text': text, 'content': text, 'event': record.get('event') or record.get('outcome') or 'stalled'}
            sending = threading.Thread(target=self.post, args=(body,), daemon=True)
            sending.start()
            self.sending = [t for t in self.sending if t.is_alive()] + [sending]

    def drain(self, timeout=10):
        """Give alerts still being sent a moment before the miner exits."""
        for sending in self.sending:
            sending.join(timeout)


def host_port(value):
    host, _, port = value.rpartition(':')
    if not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise argparse.ArgumentTypeError('give HOST:PORT, for example 127.0.0.1:8090')
    return host, int(port)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--address', required=True, help='Your NOET address; rewards are paid here')
    p.add_argument('--network', choices=sorted(NETWORKS), default='mainnet')
    p.add_argument('--gateway', help='Override the network gateway URL')
    p.add_argument('--engine', type=Path, default=Path(os.environ.get('NOET_ENGINE', '/opt/noet/bin/llama-server')))
    p.add_argument('--model', type=Path, action='append',
                   help='A pinned model GGUF; give it twice to mine across a model switch (default: /models)')
    p.add_argument('--out', type=Path, default=Path(os.environ.get('NOET_WORK_DIR', 'noet-mining')))
    p.add_argument('--flash-attention', choices=('auto', 'on', 'off'), default='off')
    p.add_argument('--max-attempts', type=int, default=0, help='0 runs until stopped')
    p.add_argument('--no-tasks', action='store_true', help='Mine with a fixed prompt instead of public requests')
    p.add_argument('--check', action='store_true', help='Check this machine and the gateway, then exit')
    p.add_argument('--gpu', type=int, help='Run on this GPU only (nvidia-smi index); one miner per GPU')
    p.add_argument('--dashboard', type=host_port, metavar='HOST:PORT',
                   help='Serve a status page, for example 127.0.0.1:8090 (this machine only)')
    p.add_argument('--notify', metavar='URL', help='Webhook for alerts: block found or rejected, stalled, model switch')
    p.add_argument('--no-version', action='store_true', help='Do not tell the gateway which miner version runs')
    p.add_argument('--skip-model-check', action='store_true', help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    args.model = args.model or default_models()
    network = NETWORKS[args.network]
    gateway = Gateway(args.gateway or network['gateway'])
    if args.no_version:
        gateway.agent = 'noet-miner'
    if args.check:
        results = preflight(args, network, gateway)
        for name, ok, detail in results:
            print(f'{"ok  " if ok else "FAIL"}  {name}: {detail}')
            if not ok and name in FIXES:
                print(f'      Fix: {FIXES[name]}')
        failed = [name for name, ok, _ in results if not ok]
        print('Ready to mine.' if not failed else f'Fix {len(failed)} item(s) above before mining.')
        return 1 if failed else 0
    address = checked_address(args.address, network)
    if not args.engine.is_file():
        raise SystemExit(f'Engine not found at {args.engine}. Run setup.sh or use the Docker image.')
    try:
        models = models_given(args.model, check=not args.skip_model_check)
    except RuntimeError as error:
        raise SystemExit(str(error))
    try:
        notify = Notifier(args.notify, address) if args.notify else None
    except ValueError as error:
        raise SystemExit(str(error))
    tally = Tally(address, args.network, models, args.gpu)
    if args.dashboard:
        dashboard(tally, *args.dashboard)

    def emit(record):
        print(json.dumps(record), flush=True)
        tally.add(record)
        if notify:
            notify(record)

    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    signal.signal(signal.SIGINT, lambda *_: stopping.append(True))
    failures, info = 0, None
    while info is None:
        try:
            info = checked_info(gateway, network)
        except GatewayError as error:
            # The gateway may be restarting; a wrong network is fatal (raised as RuntimeError).
            failures += 1
            delay = backoff(failures, error.status)
            emit({'event': 'waiting-for-gateway', 'detail': str(error), 'retryIn': delay})
            wait(delay, stopping)
            if stopping:
                return 0
    args.out.mkdir(mode=0o700, parents=True, exist_ok=True)
    emit({'event': 'mining', 'network': args.network, 'height': info['height'],
                      'rewardAddress': address, 'models': sorted(models)})
    number = next_attempt(args.out)
    paths = {'engine': args.engine, 'models': models, 'gpu': args.gpu}
    found, failures, noticed = 0, 0, None
    while not stopping and (not args.max_attempts or number < args.max_attempts):
        try:
            outcome = attempt(gateway, network, info, address, paths, args.out, number, args.flash_attention,
                              tasks=not args.no_tasks)
            found += outcome.get('outcome') == 'green'
            emit({'attempt': number, 'height': outcome['height'], 'outcome': outcome['outcome'],
                  'block': outcome.get('block'), 'blocksFound': found,
                  'task': outcome.get('task'), 'note': OUTCOME_NOTES.get(outcome['outcome'])})
            notice = switch_notice(network, models, outcome['height'])
            if notice and (noticed is None or outcome['height'] - noticed >= 50):
                noticed = outcome['height']
                emit({'event': 'switch-ahead', 'height': network['switch'][0], 'detail': notice})
            if producer_failed(outcome):
                # A producer that crashes at once (a broken setup) would otherwise
                # spend the gateway's hourly leases within a minute.
                failures += 1
                delay = backoff(failures, 0)
                emit({'attempt': number, 'event': 'producer-failed',
                      'detail': f'see {args.out}/attempt-{number:06d}/producer.log', 'retryIn': delay})
                wait(delay, stopping)
            else:
                failures = 0
        except GatewayError as error:
            failures += 1
            delay = backoff(failures, error.status)
            emit({'attempt': number, 'gateway': error.status, 'detail': str(error), 'retryIn': delay})
            wait(delay, stopping)
        except BlockVerifying as error:
            # Its verdict usually lands within three minutes; mining on now could only go stale.
            emit({'event': 'waiting-for-verdict', 'height': error.height, 'detail': str(error),
                  'retryIn': VERDICT_WAIT})
            wait(VERDICT_WAIT, stopping)
            continue
        except ModelUnavailable as error:
            switch = network.get('switch')
            if switch and error.height >= switch[0]:
                # The network has switched models; this miner cannot help until restarted with the new one.
                emit({'event': 'model-switch', 'height': switch[0], 'model': error.model,
                      'detail': 'The network now mines ' + error.model + '. Convert it (convert.sh) '
                                'and restart the miner with its GGUF.'})
                if notify:
                    notify.drain()
                return 3
            # Given only the switch model before the switch: wait for the switch height.
            emit({'event': 'waiting-for-switch', 'height': error.height, 'detail': str(error), 'retryIn': 60})
            wait(60, stopping)
        except Exception as error:
            emit({'attempt': number, 'error': type(error).__name__, 'detail': str(error)[:300]})
            wait(30, stopping)
        number += 1
    if notify:
        notify.drain()
    return 0

if __name__ == '__main__':
    sys.exit(main())
