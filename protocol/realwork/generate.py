"""Generate an actual proof for a live disposable private-node work lease.

The caller must verify the chain and registry and keep the node alive. This
never substitutes model weights, changes targets or fabricates proof evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, '/cpu')
from exercise import exercise, MODEL

PROFILES = ('noet-private-work-v1', 'noet-testnet-1', 'noet-mainnet-1')
END_TOKENS = ('<|im_end|>', '<|endoftext|>')
TASK_ID = re.compile(r'[0-9a-f]{24}')


def document_prompt(task):
    """The prompt for a gateway task: a question-and-answer document the model
    keeps writing. A proof needs complete 256-token windows and the proof sampler
    stops at an end-of-turn token, so a chat turn (which ends after its answer)
    would rarely produce one; the miner cuts the answer at the next "Question:".
    The task id makes every prompt unique, so a repeated question is new work."""
    if not isinstance(task, dict) or not TASK_ID.fullmatch(str(task.get('id', ''))):
        raise ValueError('Invalid task from the gateway')
    question = task.get('prompt')
    if not isinstance(question, str) or not 3 <= len(question.strip()) <= 1500 or '<|' in question:
        raise ValueError('Invalid task prompt from the gateway')
    return (f'NOET open answers, task {task["id"]}\n'
            'Questions sent to the NOET network, each followed by a careful, accurate answer.\n\n'
            f'Question: {question.strip()}\nAnswer:')


def run(job_path, engine, model, output, accelerator='cpu', flash_attention='auto'):
    import chiavdf
    from noet.vdf import verify
    with job_path.open('rb') as stream: raw = stream.read(64*1024+1)
    if len(raw) > 64*1024: raise ValueError('Oversized work record')
    record = json.loads(raw)
    if record['profile'] not in PROFILES: raise ValueError('Unknown network profile')
    registry = record['model']
    if registry['model_name']+'@'+registry['model_commit'] != MODEL:
        raise ValueError('Registry differs from the pinned test weights')
    if registry['difficulty'] != 1000000: raise ValueError('Unexpected private model difficulty')
    job = record['job']
    if time.time() >= job['expires_at']: raise ValueError('Work lease has expired')
    header = bytes.fromhex(job['header_prefix'])
    if len(header) != 76 or header[4:36][::-1].hex() != record['parent']:
        raise ValueError('Header does not bind to the native parent')
    parent = header[4:36]
    vdf = chiavdf.prove_from_hash(parent, 1024, 10000)
    if not verify(parent.hex(), vdf.hex(), 10000, chiavdf.verify_from_hash):
        raise ValueError('Generated VDF did not verify')
    context = {'block_hash':parent.hex(), 'header_prefix':header.hex(), 'vdf':vdf.hex(), 'tick':10000}
    # Useful work: the gateway's task (a public request, or the open dataset).
    # Without one, the old fixed research prompt; each is unique, because
    # reusing an exact prompt after history enforcement is refused by consensus.
    if record.get('task') is not None:
        prompt, preserve = document_prompt(record['task']), END_TOKENS
    else:
        prompt, preserve = (f'Research case {job["height"]}.{job["req_id"]}: Write a careful explanation of '
            'how to verify numerical computations. Include one worked example and describe '
            'common sources of rounding error and how to compare independent implementations.'), None
    result = exercise(engine, model, output, context, 4, 516, node_job=job,
                      registered_difficulty=registry['difficulty'], accelerator=accelerator,prompt=prompt,
                      flash_attention=flash_attention, preserve_tokens=preserve)
    result.update(workRecordSha256=hashlib.sha256(raw).hexdigest(),
        registeredModel=True, registeredDifficulty=True, liveLease=True,
        productionReady=False, scope='Live isolated private-node work, not block acceptance or model approval')
    (output/'generation.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ('job', 'engine', 'model', 'out'): p.add_argument('--'+arg, type=Path, required=True)
    p.add_argument('--accelerator', choices=('cpu','cuda'), default='cpu')
    p.add_argument('--flash-attention',choices=('auto','on','off'),default='auto')
    a=p.parse_args()
    print(json.dumps(run(a.job,a.engine,a.model,a.out,a.accelerator,a.flash_attention),indent=2))
