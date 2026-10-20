"""Continuous GPU miner for the hosted private-work development network.

Leases work from the remote node through the local mutual-TLS relay, generates
the real proof with the pinned engine and submits it. The node's own verifier
decides; this loop never retries a rejected envelope with another seed,
changes targets or substitutes mock validation. Every attempt is recorded.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time

ROOT = Path('/workspace/noet-gpu')
GENESIS = '301587d540aafc312cf4606e700d9662e038e7e43ca6cca92779d6b4baa81b4e'
MODEL = 'Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca'
PROOF_LIMIT = 2 * 1024 * 1024


def checked_registry(rpc):
    if rpc('getblockhash', 0) != GENESIS:
        raise RuntimeError('Remote node is not the private-work network')
    registry = rpc('getmodelslist', False)
    if (len(registry) != 1 or registry[0]['model_name'] + '@' + registry[0]['model_commit'] != MODEL
            or registry[0]['difficulty'] != 1000000):
        raise RuntimeError('Remote model registry differs from the pinned research model')
    return registry[0]


def reward_script(rpc, address):
    if not address.startswith('nwrk1'):
        raise ValueError('Reward address must be a private-work nwrk address')
    info = rpc('validateaddress', address)
    if not info.get('isvalid') or not info.get('scriptPubKey'):
        raise ValueError('Reward address is not valid on the node')
    return info['scriptPubKey']


def settle(rpc, block, timeout, sleep=time.sleep, clock=time.monotonic):
    """Wait for the node's own Full verdict on a submitted block."""
    until = clock() + timeout
    while clock() < until:
        rows = [r for r in rpc('getrecentvalidations', 100) if r['block_hash'] == block and r['type'] == 'full']
        if rows:
            status = rows[0]['full_status']
            if status in ('red', 'amber') or (status == 'green' and rpc('getbestblockhash') == block):
                return rows[0]
        sleep(2)
    return None


def attempt(rpc, registry, script, out, number, flash_attention='off', runner=subprocess.Popen):
    parent = rpc('getbestblockhash')
    job = rpc('create_mining_work_unit', 'tensor-reg', script, secrets.token_hex(8))
    record = {'profile': 'noet-private-work-v1', 'genesis': GENESIS, 'parent': parent, 'model': registry, 'job': job}
    folder = out / f'attempt-{number:06d}'
    folder.mkdir(mode=0o700)
    (folder / 'job.json').write_text(json.dumps(record, indent=2) + '\n')
    result = {'attempt': number, 'height': job['height'], 'startedAt': time.time(), 'submissions': []}
    with (folder / 'producer.log').open('xb') as log:
        producer = runner([sys.executable, str(ROOT / 'checks/generate.py'), '--job', str(folder / 'job.json'),
                           '--engine', str(ROOT / 'engine/build/bin/llama-server'),
                           '--model', str(ROOT / 'qwen3-0.6b-bf16.gguf'), '--out', str(folder / 'generation'),
                           '--accelerator', 'cuda', '--flash-attention', flash_attention],
                          stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            producer.wait(timeout=900)
        except subprocess.TimeoutExpired:
            result['producerTimeout'] = True
        finally:
            # Never leave an engine grandchild holding the GPU.
            try:
                os.killpg(producer.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            producer.wait(timeout=10)
    result['producerExit'] = producer.returncode
    for proof in sorted((folder / 'generation/proofs').glob(str(job['req_id']) + '_*.bin')):
        with proof.open('rb') as source:
            raw = source.read(PROOF_LIMIT + 1)
        if len(raw) > PROOF_LIMIT:
            raise ValueError('Oversized mining proof')
        submitted = rpc('submit_mining_response', job['req_id'], base64.b64encode(raw).decode(), 0)
        entry = {'proofSha256': hashlib.sha256(raw).hexdigest(), 'response': submitted}
        result['submissions'].append(entry)
        if submitted.get('status') not in ('accepted', 'accepted_pending_connect'):
            continue
        verdict = settle(rpc, submitted['block_hash'], 900)
        entry['fullValidation'] = verdict
        result['outcome'] = 'timeout' if verdict is None else verdict['full_status']
        if verdict and verdict['full_status'] == 'green':
            result['acceptedHeight'] = rpc('getblockcount')
        break
    result.setdefault('outcome', 'no-eligible-proof')
    result['finishedAt'] = time.time()
    (folder / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--rpc-url', default='http://127.0.0.1:30443')
    p.add_argument('--credentials', type=Path, required=True, help='Private file containing miner:password')
    p.add_argument('--reward-address', required=True)
    p.add_argument('--flash-attention', choices=('auto', 'on', 'off'), default='off')
    p.add_argument('--max-attempts', type=int, default=0, help='0 means run until stopped')
    args = p.parse_args(argv)
    sys.path.insert(0, str(ROOT / 'checks'))
    from noet_rpc import NodeRPC
    rpc = NodeRPC(args.rpc_url, args.credentials)
    args.out.mkdir(mode=0o700, parents=True, exist_ok=True)
    registry = checked_registry(rpc)
    script = reward_script(rpc, args.reward_address)
    number = len(list(args.out.glob('attempt-*')))
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    while not stopping and (not args.max_attempts or number < args.max_attempts):
        try:
            outcome = attempt(rpc, registry, script, args.out, number, args.flash_attention)
            print(json.dumps({k: outcome.get(k) for k in ('attempt', 'height', 'outcome', 'acceptedHeight')}), flush=True)
        except Exception as error:
            print(json.dumps({'attempt': number, 'error': type(error).__name__, 'detail': str(error)[:300]}), flush=True)
            time.sleep(30)
        number += 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
