#!/usr/bin/env python3
"""NOET miner. Leases work from a public mining gateway, generates the proof on
your GPU with the pinned engine and model, and submits it. Block rewards are
paid by consensus straight to your own address; this program never holds keys.

  python3 noet_miner.py --address tnoet1... [--gateway https://...]
  python3 noet_miner.py --address tnoet1... --check     # test this machine first

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
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

VERSION = '0.1.0'
HERE = Path(__file__).resolve().parent
MODEL = 'Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca'
GGUF_SHA256 = '8fa11f4d815167c4f6e3c91b25aebde25d8346a8f11228e744016592532b0700'
PROOF_LIMIT = 2 * 1024 * 1024
# GPUs the engine is compiled for (Dockerfile CUDA_ARCHITECTURES), and the driver CUDA 12.8 needs.
ARCHITECTURES = ('8.0', '8.6', '8.9', '9.0', '12.0')
MIN_DRIVER = (570, 0)
MIN_GPU_MIB = 6 * 1024
MIN_DISK_BYTES = 2 * 1024 ** 3
# Each network pins its genesis so a gateway cannot point you at another chain.
NETWORKS = {
    'mainnet': {'gateway': 'https://noet-mainnet.fly.dev:9443', 'hrp': 'noet', 'profile': 'noet-mainnet-1',
                'genesis': 'fc3e73901e2831df8457ea56f56cf92cd23018453439a0c1d402cbab61f45a54'},
    'testnet': {'gateway': 'https://noet-testnet.fly.dev:9443', 'hrp': 'tnoet', 'profile': 'noet-testnet-1',
                'genesis': '2da97e8dadb7fe27645263d3e732211cb67650adbc02a016102cc7390785cdaa'},
}


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
        self.opener = build_opener(ProxyHandler({}), NoRedirect)

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=data, headers={
            'Content-Type': 'application/json', 'User-Agent': 'noet-miner/' + VERSION})
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
    models = info.get('models') or []
    if len(models) != 1 or models[0].get('model') != MODEL or models[0].get('difficulty') != 1000000:
        raise RuntimeError('The network model differs from the model this miner runs')
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


def settle(gateway, block, timeout, sleep=time.sleep, clock=time.monotonic):
    """Wait for the node's Full verdict on our block."""
    until = clock() + timeout
    while clock() < until:
        try:
            verdict = gateway.block(block)
        except GatewayError:
            verdict = {}
        if verdict.get('full') in ('red', 'amber') or (verdict.get('full') == 'green' and verdict.get('inBestChain')):
            return verdict
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
    # Check the payout from the node's own coinbase, not the gateway's word.
    payout_proven = verify_payout(job, address, network['hrp'])
    header = bytes.fromhex(job['header_prefix'])
    record = {'profile': network['profile'], 'genesis': info['genesis'], 'parent': header[4:36][::-1].hex(),
              'model': {'model_name': MODEL.split('@')[0], 'model_commit': MODEL.split('@')[1],
                        'difficulty': 1000000}, 'job': job, 'task': task}
    folder = out / f'attempt-{number:06d}'
    folder.mkdir(mode=0o700)
    (folder / 'job.json').write_text(json.dumps(record, indent=2) + '\n')
    result = {'attempt': number, 'height': job['height'], 'startedAt': time.time(), 'submissions': [],
              'payoutVerified': bool(payout_proven)}
    started = time.monotonic()
    with (folder / 'producer.log').open('xb') as log:
        producer = runner([sys.executable, str(HERE / 'generate.py'), '--job', str(folder / 'job.json'),
                           '--engine', str(paths['engine']), '--model', str(paths['model']),
                           '--out', str(folder / 'generation'), '--accelerator', 'cuda',
                           '--flash-attention', flash_attention],
                          stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
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
        verdict = settle(gateway, submitted['block_hash'], 900)
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


def gpus(run=subprocess.run):
    """name, driver, memory MiB and compute capability per GPU, from nvidia-smi."""
    if not shutil.which('nvidia-smi'):
        raise RuntimeError('nvidia-smi was not found: install the NVIDIA driver (or run Docker with --gpus all)')
    out = run(['nvidia-smi', '--query-gpu=name,driver_version,memory.total,compute_cap',
               '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=30, check=True).stdout
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
        found = gpus(run)
        problems = []
        for row in found:
            driver = tuple(int(x) for x in re.findall(r'\d+', row['driver'])[:2])
            if driver < MIN_DRIVER:
                problems.append(f'{row["name"]}: driver {row["driver"]} is older than {MIN_DRIVER[0]} (CUDA 12.8)')
            if row['computeCap'] not in ARCHITECTURES:
                problems.append(f'{row["name"]}: compute capability {row["computeCap"]} is not supported '
                                f'({", ".join(ARCHITECTURES)})')
            if row['memoryMiB'] < MIN_GPU_MIB:
                problems.append(f'{row["name"]}: {row["memoryMiB"]} MiB of GPU memory; {MIN_GPU_MIB} MiB needed')
        if problems:
            raise RuntimeError('; '.join(problems))
        return ', '.join(f'{r["name"]} ({r["memoryMiB"]} MiB, driver {r["driver"]})' for r in found)

    def engine():
        if not args.engine.is_file() or not os.access(args.engine, os.X_OK):
            raise RuntimeError(f'not found or not executable at {args.engine}; run setup.sh or use the Docker image')
        return str(args.engine)

    def model():
        if not args.model.is_file():
            raise RuntimeError(f'not found at {args.model}; run convert.sh or use the Docker image')
        if file_sha256(args.model) != GGUF_SHA256:
            raise RuntimeError('differs from the pinned NOET model; convert or download it again')
        return 'pinned checksum matches'

    def disk():
        folder = args.out if args.out.exists() else args.out.resolve().parent
        free = shutil.disk_usage(folder).free
        if free < MIN_DISK_BYTES:
            raise RuntimeError(f'{free // 1024 ** 2} MiB free in {folder}; 2 GiB needed for attempts')
        return f'{free // 1024 ** 3} GiB free'

    def network_check():
        info = checked_info(gateway, network)
        return f'{gateway.base} at height {info["height"]}'

    def python():
        version = sys.version.split()[0]
        if sys.version_info < (3, 11):  # hashlib.file_digest
            raise RuntimeError(f'Python {version}; 3.11 or newer is needed')
        return version

    check('Python', python)
    check('Reward address', lambda: checked_address(args.address, network))
    check('NVIDIA GPU', gpu)
    check('Engine', engine)
    check('Model', model)
    check('Disk space', disk)
    check('Gateway and network', network_check)
    return checks


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
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--address', required=True, help='Your NOET address; rewards are paid here')
    p.add_argument('--network', choices=sorted(NETWORKS), default='mainnet')
    p.add_argument('--gateway', help='Override the network gateway URL')
    p.add_argument('--engine', type=Path, default=Path(os.environ.get('NOET_ENGINE', '/opt/noet/bin/llama-server')))
    p.add_argument('--model', type=Path, default=Path(os.environ.get('NOET_MODEL', '/models/qwen3-0.6b-bf16.gguf')))
    p.add_argument('--out', type=Path, default=Path(os.environ.get('NOET_WORK_DIR', 'noet-mining')))
    p.add_argument('--flash-attention', choices=('auto', 'on', 'off'), default='off')
    p.add_argument('--max-attempts', type=int, default=0, help='0 runs until stopped')
    p.add_argument('--no-tasks', action='store_true', help='Mine with a fixed prompt instead of public requests')
    p.add_argument('--check', action='store_true', help='Check this machine and the gateway, then exit')
    p.add_argument('--skip-model-check', action='store_true', help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    network = NETWORKS[args.network]
    gateway = Gateway(args.gateway or network['gateway'])
    if args.check:
        results = preflight(args, network, gateway)
        for name, ok, detail in results:
            print(f'{"ok  " if ok else "FAIL"}  {name}: {detail}')
        failed = [name for name, ok, _ in results if not ok]
        print('Ready to mine.' if not failed else f'Fix {len(failed)} item(s) above before mining.')
        return 1 if failed else 0
    address = checked_address(args.address, network)
    if not args.engine.is_file():
        raise SystemExit(f'Engine not found at {args.engine}. Run setup.sh or use the Docker image.')
    if not args.model.is_file():
        raise SystemExit(f'Model not found at {args.model}. Run convert.sh or use the Docker image.')
    if not args.skip_model_check and file_sha256(args.model) != GGUF_SHA256:
        raise SystemExit('The model file differs from the pinned NOET model. Fetch it again.')
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
            print(json.dumps({'event': 'waiting-for-gateway', 'detail': str(error), 'retryIn': delay}), flush=True)
            wait(delay, stopping)
            if stopping:
                return 0
    args.out.mkdir(mode=0o700, parents=True, exist_ok=True)
    print(json.dumps({'event': 'mining', 'network': args.network, 'height': info['height'],
                      'rewardAddress': address}), flush=True)
    number = next_attempt(args.out)
    paths = {'engine': args.engine, 'model': args.model}
    found, failures = 0, 0
    while not stopping and (not args.max_attempts or number < args.max_attempts):
        try:
            outcome = attempt(gateway, network, info, address, paths, args.out, number, args.flash_attention,
                              tasks=not args.no_tasks)
            found += outcome.get('outcome') == 'green'
            print(json.dumps({'attempt': number, 'height': outcome['height'], 'outcome': outcome['outcome'],
                              'block': outcome.get('block'), 'blocksFound': found,
                              'task': outcome.get('task')}), flush=True)
            if producer_failed(outcome):
                # A producer that crashes at once (a broken setup) would otherwise
                # spend the gateway's hourly leases within a minute.
                failures += 1
                delay = backoff(failures, 0)
                print(json.dumps({'attempt': number, 'event': 'producer-failed',
                                  'detail': f'see {args.out}/attempt-{number:06d}/producer.log',
                                  'retryIn': delay}), flush=True)
                wait(delay, stopping)
            else:
                failures = 0
        except GatewayError as error:
            failures += 1
            delay = backoff(failures, error.status)
            print(json.dumps({'attempt': number, 'gateway': error.status, 'detail': str(error),
                              'retryIn': delay}), flush=True)
            wait(delay, stopping)
        except Exception as error:
            print(json.dumps({'attempt': number, 'error': type(error).__name__, 'detail': str(error)[:300]}),
                  flush=True)
            wait(30, stopping)
        number += 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
