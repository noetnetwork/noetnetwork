#!/usr/bin/env python3
"""Exercise job-height parsing and slot reuse in the real CPU engine.

Run only in an offline, disposable container. Synthetic context and the pinned
source's activation schedule are laboratory fixtures, not NOET network policy.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

from exercise import MODEL, read_context


def check(engine, model, context, output):
    if context['header_prefix'][-8:] != 'ffff7f20':
        raise ValueError('The check requires the priced synthetic context')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    env = {'PATH':'/usr/local/bin:/usr/bin:/bin', 'OMP_NUM_THREADS':'3',
           'POW_SAVE_TO_DISK':'1', 'POW_PROXY_ENABLE':'0', 'POW_PROOF_VERSION':'3',
           'PROOF_SAVE_DIR':str(output/'proofs'), 'ZMQ_PUSH_HOST':'127.0.0.1',
           'ZMQ_PUSH_PORT':'19999', 'MINING_SOLUTION_COOLDOWN_SEC':'0',
           'POW_CHAIN_NETWORK':'tensor-main', 'POW_PRE_SEND_STRICT':'1',
           'POW_PRE_SEND_ANTI_PARROT':'1', 'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1'}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    missing = object()
    outcomes = []

    def request(envelope, height=missing, tokens=1):
        pow_job = {**context, 'target':'7fffff'+'00'*29, 'request_id':9202,
                   'difficulty':60_000_000, 'model_identifier':MODEL,
                   'compute_precision':'bf16', 'proof_version':3, 'stepbind':1, 'v4':1}
        if height is not missing:
            pow_job['height'] = height
        body = {'prompt':'The quick brown fox jumps over the lazy dog. Once upon a time in a land far away, there lived a wise old wizard.',
                'n_predict':tokens, 'temperature':1.0, 'top_k':50, 'top_p':1.0,
                'min_p':0.0, 'seed':42, 'stream':False, 'ignore_eos':True,
                'cache_prompt':False, envelope:{'pow':pow_job}}
        req = urllib.request.Request('http://127.0.0.1:18000/completion',
            data=json.dumps(body).encode(), headers={'Content-Type':'application/json'})
        try:
            with opener.open(req, timeout=600) as response:
                code, raw = response.status, response.read(1024*1024+1)
        except urllib.error.HTTPError as error:
            code, raw = error.code, error.read(65537)
        if len(raw) > 1024*1024:
            raise ValueError('Oversized engine response')
        return code, json.loads(raw)

    with (output/'engine.log').open('xb') as log:
        process = subprocess.Popen([str(engine), '--model', str(model), '--alias','noet-cpu-test',
            '--host','127.0.0.1','--port','18000','--threads','3','--ctx-size','4096',
            '--batch-size','256','--parallel','1','--n-gpu-layers','0'],
            env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic()+120
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Engine exited during startup')
                try:
                    with opener.open('http://127.0.0.1:18000/health', timeout=2) as response:
                        if response.status == 200: break
                except OSError: time.sleep(.25)
            else: raise TimeoutError('Engine startup timed out')
            for envelope in ('extra_sampling_params', 'vllm_xargs'):
                for height in (None, True, False, -1, 1.5, '1', {}, [], 2**32, 2**63, 2**64-1):
                    code, data = request(envelope, height)
                    if code != 400 or 'pow.height must be an unsigned 32-bit integer' not in json.dumps(data):
                        raise AssertionError(f'Invalid height was not rejected: {envelope} {height!r} {code}')
                    outcomes.append({'envelope':envelope, 'height':height, 'rejected':True})
                # Reuse the same single slot across boundaries and missing values.
                for height in (missing, 0, 2**32-1, missing, 24150, 1, missing):
                    code, data = request(envelope, height)
                    if code != 200 or data.get('tokens_predicted') != 1:
                        raise AssertionError(f'Valid height failed: {code} {data}')
                    params = data['generation_settings']['pow_params']
                    if height is missing:
                        if 'height' in params: raise AssertionError('Height leaked between jobs')
                    elif type(params.get('height')) is not int or params['height'] != height:
                        raise AssertionError('Height changed during request parsing')
                    outcomes.append({'envelope':envelope, 'height':'absent' if height is missing else height, 'retained':True})
            # A window need not meet the unchanged raw target. Try at most
            # eight windows per case; no observed boundary is inconclusive.
            sampler_path = Path('/tmp/llama_pow_logs/pow_sampler.log')
            window_counts = {}
            for label, height in (('active', 24150), ('absent', missing)):
                for windows in range(1, 9):
                    code, data = request('extra_sampling_params', height, 260)
                    if code != 200 or data.get('tokens_predicted') != 260:
                        raise AssertionError('Incomplete height-check inference')
                    sampler = sampler_path.read_text()
                    if label == 'active':
                        if 'this job carries no height' in sampler:
                            raise AssertionError('Height was dropped before the coordinator')
                        observed = 'proof v4: seq ' in sampler
                    else:
                        observed = 'this job carries no height' in sampler
                    if observed:
                        window_counts[label] = windows
                        break
                else:
                    raise AssertionError(f'No {label} v4 boundary observed; run is inconclusive')
            active_rejections = [line for line in sampler.splitlines()
                if 'anti-parrot gate REJECTS' in line and 'height=24150' in line]
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
            sampler = Path('/tmp/llama_pow_logs/pow_sampler.log')
            if sampler.is_file():
                with sampler.open('rb') as stream:
                    stream.seek(0, 2); stream.seek(max(0, stream.tell()-128*1024))
                    (output/'sampler.log').write_bytes(stream.read(128*1024))
    result = {'format':'noet-engine-height-check-v1', 'engineSha256':hashlib.sha256(engine.read_bytes()).hexdigest(),
              'cases':outcomes, 'activeHeightRejections':len(active_rejections),
              'heightReachedCoordinator':True, 'absentHeightDidNotInherit':True, 'windowAttempts':window_counts,
              'completionTokens':260*sum(window_counts.values())+sum(case.get('retained', False) for case in outcomes),
              'productionReady':False, 'nativeNodeContext':False,
              'scope':'Actual CPU engine with synthetic jobs; no block, detector or network acceptance'}
    (output/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--gguf', type=Path, required=True)
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.engine, args.gguf, read_context(args.context), args.out), indent=2))
