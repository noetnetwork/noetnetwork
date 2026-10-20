#!/usr/bin/env python3
"""Generate bounded v2/v4 inference evidence in an isolated CPU test, without a node.

Run inside a network-disabled container. The caller supplies a reviewed engine
and the pinned BF16 GGUF conversion. This cannot establish consensus validity.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import time
import urllib.request

# A research host testing another candidate model (protocol/models/profiles.json)
# names it here; the node's registry must still match it exactly.
MODEL = os.environ.get('NOET_MODEL_ID', 'Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca')
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*@[a-f0-9]{40}', MODEL):
    raise ValueError('NOET_MODEL_ID must be organization/model@40-character-revision')
REQUEST_ID = 9201
# The GPU the engine uses; gpu/run.sh sets it when the verifier has a GPU of its own.
PRODUCER_GPU = os.environ.get('NOET_PRODUCER_GPU', '0')
if not re.fullmatch(r'[0-9]', PRODUCER_GPU):
    raise ValueError('NOET_PRODUCER_GPU must be a single GPU index')


def native_target(header_prefix, target):
    """Require an exact node-issued compact target at the registered normalizer."""
    if not isinstance(header_prefix,str) or not re.fullmatch('[0-9a-f]{152}',header_prefix):
        raise ValueError('Invalid native work header')
    if not isinstance(target,str) or not re.fullmatch('[0-9a-f]{64}',target):
        raise ValueError('Invalid native work target')
    bits=int.from_bytes(bytes.fromhex(header_prefix)[72:76],'little')
    exponent,mantissa=bits>>24,bits&0x7fffff
    if bits&0x800000 or not mantissa or exponent>34:raise ValueError('Invalid compact target')
    value=mantissa << (8*(exponent-3)) if exponent>=3 else mantissa >> (8*(3-exponent))
    if not 0<value<=0x7fffff<<(8*29) or value!=int(target,16):
        raise ValueError('Native target differs from its header or private work limit')
    return target


def read_context(path):
    with path.open('rb') as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError('Context is too large')
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {'block_hash', 'header_prefix', 'vdf', 'tick'}:
        raise ValueError('Unexpected experiment context fields')
    for name, size in (('block_hash', 64), ('header_prefix', 152), ('vdf', 400)):
        if not isinstance(data[name], str) or not re.fullmatch('[0-9a-f]{'+str(size)+'}', data[name]):
            raise ValueError('Invalid context field: '+name)
    if data['block_hash'] == '0'*64 or data['header_prefix'][8:72] != data['block_hash']:
        raise ValueError('Context parent mismatch')
    if type(data['tick']) is not int or not 1 <= data['tick'] <= 100_000:
        raise ValueError('Context iteration count is outside experiment bounds')
    return data


def exercise(engine, model, output, context=None, proof_version=2, tokens=260, *, node_job=None,
             registered_difficulty=None, accelerator='cpu', prompt=None, flash_attention='auto',
             preserve_tokens=None):
    if accelerator not in ('cpu', 'cuda'):
        raise ValueError('Unsupported accelerator')
    if flash_attention not in ('auto','on','off'):raise ValueError('Unsupported attention implementation')
    if prompt is not None and (not isinstance(prompt,str) or not prompt.strip() or len(prompt)>2048):
        raise ValueError('Research prompt must contain 1 to 2048 characters')
    if proof_version not in (2, 4) or tokens not in (260, 516, 1028, 2052):
        raise ValueError('Unsupported research profile')
    if proof_version == 2 and tokens != 260:
        raise ValueError('Legacy comparison requires the fixed 260-token profile')
    if registered_difficulty is not None and (node_job is None or
        type(registered_difficulty) is not int or registered_difficulty != 1_000_000):
        # This profile uses the native normalizer, so the issued target needs
        # no scaling. Other model difficulties need a separate reviewed path.
        raise ValueError('Registered difficulty requires a native job at the 1000000 normalizer')
    if proof_version == 4 and (context is None or
        (registered_difficulty is None and context['header_prefix'][-8:] != 'ffff7f20')):
        raise ValueError('V4 research requires the 0x207fffff header context')
    request_id = REQUEST_ID
    if node_job is not None:
        if proof_version != 4 or node_job['header_prefix'] != context['header_prefix']:
            raise ValueError('Recorded node job does not match this v4 context')
        if registered_difficulty is not None:native_target(node_job['header_prefix'],node_job['target'])
        request_id = node_job['req_id']
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    proof_dir = output / 'proofs'
    # Deliberately no inherited credentials, proxy settings or production paths.
    env = {'PATH':'/usr/local/bin:/usr/bin:/bin', 'OMP_NUM_THREADS':'3',
           'POW_SAVE_TO_DISK':'1', 'POW_PROXY_ENABLE':'0', 'POW_PROOF_VERSION':'2' if proof_version == 2 else '3',
           'PROOF_SAVE_DIR':str(proof_dir), 'ZMQ_PUSH_HOST':'127.0.0.1', 'ZMQ_PUSH_PORT':'19999',
           'MINING_SOLUTION_COOLDOWN_SEC':'0', 'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1'}
    if accelerator == 'cuda':
        env['CUDA_VISIBLE_DEVICES'] = PRODUCER_GPU
    body = {'model':'noet-cpu-test', 'prompt':'The quick brown fox jumps over the lazy dog. Once upon a time in a land far away, there lived a wise old wizard.',
            'max_tokens':tokens, 'temperature':0.8, 'top_k':40, 'top_p':0.95, 'seed':42,
            'stream':False, 'ignore_eos':True, 'cache_prompt':False,
            'extra_sampling_params':{'pow':{'block_hash':'0'*63+'1', 'vdf':'', 'tick':1, 'target':'f'*64,
                'header_prefix':'0'*80, 'ipfs_cid':'', 'request_id':REQUEST_ID, 'difficulty':1,
                'model_identifier':MODEL, 'compute_precision':'bf16'}}}
    if prompt is not None:body['prompt']=prompt
    if preserve_tokens:
        # Only changes how these special tokens are rendered in the returned
        # text (so a reader can see where the answer ended); sampling is untouched.
        body['preserved_tokens']=list(preserve_tokens)
    if context is not None:
        body['extra_sampling_params']['pow'].update(context)
    if proof_version == 4:
        body.update(temperature=1.0, top_k=50, top_p=1.0, min_p=0.0)
        body['extra_sampling_params']['pow'].update(proof_version=3, stepbind=1, v4=1,
            difficulty=60_000_000, target='7fffff'+'00'*29)
    if node_job is not None:
        body['extra_sampling_params']['pow'].update(request_id=request_id,
            height=node_job['height'], target=node_job['target'],
            stepbind=node_job['stepbind'], v4=node_job['v4'])
        if registered_difficulty is not None:
            body['extra_sampling_params']['pow']['difficulty'] = registered_difficulty
        (output/'node-job.json').write_text(json.dumps(node_job, indent=2)+'\n')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with (output / 'engine.log').open('xb') as log:
        device_args=['--device','none','--no-op-offload'] if accelerator=='cpu' else []
        process = subprocess.Popen([str(engine), '--model', str(model), '--alias', 'noet-cpu-test',
            '--host', '127.0.0.1', '--port', '18000', '--threads', '3', '--ctx-size', str(1024 if tokens == 260 else 4096),
            '--batch-size', '256', '--parallel', '1', '--flash-attn',flash_attention,
            '--n-gpu-layers', '99' if accelerator == 'cuda' else '0',*device_args], env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            # Larger candidate models take longer to load; 0.6B and 8B keep 120 s.
            deadline = time.monotonic() + max(120, os.path.getsize(model) / 250e6)
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Engine exited during startup')
                try:
                    with opener.open('http://127.0.0.1:18000/health', timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(.25)
            else:
                raise TimeoutError('Engine startup timed out')
            if accelerator == 'cuda':
                loaded = re.search(r'offloaded (\d+)/(\d+) layers to GPU', (output/'engine.log').read_text())
                if not loaded or int(loaded[1]) == 0 or loaded[1] != loaded[2]:
                    raise RuntimeError('GPU acceptance requires all model layers on the GPU')
            request = urllib.request.Request('http://127.0.0.1:18000/v1/completions', data=json.dumps(body).encode(), headers={'Content-Type':'application/json'})
            with opener.open(request, timeout=600) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError('Oversized completion')
            completed = json.loads(raw)
            if completed.get('usage', {}).get('completion_tokens') != tokens:
                raise ValueError('Incomplete inference run')
            (output / 'completion.json').write_bytes(raw)
            deadline = time.monotonic() + 10
            proofs = []
            while time.monotonic() < deadline:
                proofs = list(proof_dir.glob(str(request_id) + '_*.bin'))
                if proofs:
                    break
                time.sleep(.25)
            if not proofs or (proof_version == 2 and len(proofs) != 1):
                raise ValueError('No eligible evidence, or unexpected legacy evidence count; inspect sampler.log')
            # The response writer is asynchronous. Stop the engine below before
            # the caller decodes or inventories its finished output files.
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            # The pinned coordinator writes here inside the isolated container.
            # Keep bounded diagnostics even when no completed window wins.
            sampler_log = Path('/tmp/llama_pow_logs/pow_sampler.log')
            if sampler_log.is_file():
                with sampler_log.open('rb') as stream:
                    stream.seek(0, 2)
                    stream.seek(max(0, stream.tell()-128*1024))
                    (output/'sampler.log').write_bytes(stream.read(128*1024))
    proofs = sorted(proof_dir.glob(str(request_id) + '_*.bin'))
    proof = proofs[0]
    result = {'model':MODEL, 'requestedProofVersion':proof_version, 'completionTokens':tokens,
              'accelerator':accelerator,
              'flashAttention':flash_attention,'cpuOpOffloadDisabled':accelerator=='cpu',
              'promptSha256':hashlib.sha256(body['prompt'].encode()).hexdigest(),
              'evidenceFile':proof.relative_to(output).as_posix(),
              'evidenceSha256':hashlib.sha256(proof.read_bytes()).hexdigest(),
              'boundContextSupplied':context is not None,
              'evidenceFiles':[p.relative_to(output).as_posix() for p in sorted(proofs)],
              'productionReady':False, 'scope':'Synthetic-context inference evidence only; no block or verifier acceptance'}
    if node_job is not None:
        result.update(recordedNodeJob=True, height=node_job['height'], requestId=request_id,
            registeredModel=False, registeredDifficulty=False, liveLease=False,
            scope='Offline replay of recorded lab-node work with research model/difficulty; no block or verifier acceptance')
    (output / 'generation.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--gguf', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--context', type=Path, help='Bound synthetic-header context with a separately generated VDF')
    parser.add_argument('--proof-version', type=int, choices=(2, 4), default=2)
    parser.add_argument('--tokens', type=int, choices=(260, 516, 1028, 2052), default=260)
    args = parser.parse_args()
    context = read_context(args.context) if args.context else None
    print(json.dumps(exercise(args.engine.resolve(), args.gguf.resolve(), args.out.resolve(), context, args.proof_version, args.tokens), indent=2))
