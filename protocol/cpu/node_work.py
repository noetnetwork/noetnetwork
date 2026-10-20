"""Generate research evidence for an authentic, recorded lab-node work unit.

The node is gone by replay time. Its lease cannot be submitted. Model and
registered difficulty are NOT supplied by the node or approved by this test.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

if __package__:
    from .exercise import exercise
else:
    from exercise import exercise

GENESIS = 'fc0ac105e8adb2401780e6fd54ec48650912c5e460d0a4ffe14ba1b83e2df359'


def read_job(path):
    with path.open('rb') as stream: raw = stream.read(64*1024+1)
    if len(raw) > 64*1024: raise ValueError('Oversized node-work record')
    report = json.loads(raw)
    if (report.get('format') != 'noet-native-work-issuance-v1' or report.get('genesis') != GENESIS
        or report.get('validationMode') != 'real' or report.get('externalValidationEnabled') is not True
        or report.get('blocksAccepted') != 0):
        raise ValueError('Expected the isolated native-work acceptance record')
    case = next(case for case in report['cases'] if case['activeV4'] is True)
    job = case['workUnits'][0]
    if job['network'] != 'tensor-reg' or job['tip_hash'] != GENESIS or job['height'] != 1:
        raise ValueError('Unexpected laboratory identity')
    for name in ('req_id', 'height'):
        if type(job[name]) is not int or not 0 < job[name] < 2**32: raise ValueError('Invalid '+name)
    if not isinstance(job['header_prefix'], str) or not re.fullmatch('[0-9a-f]{152}', job['header_prefix']):
        raise ValueError('Invalid header prefix')
    header = bytes.fromhex(job['header_prefix'])
    if header[4:36][::-1].hex() != GENESIS or header[72:76].hex() != 'ffff7f20':
        raise ValueError('Header identity or bounded lab target differs')
    if job['target'] != '7fffff'+'00'*29 or any(type(job[k]) is not int or job[k] != 1 for k in ('v4', 'stepbind')):
        raise ValueError('Target or activation flags differ')
    return job, hashlib.sha256(raw).hexdigest()


def run(record, engine, model, output):
    import chiavdf
    from noet.vdf import verify
    job, record_sha = read_job(record)
    parent = bytes.fromhex(job['header_prefix'])[4:36]
    vdf = chiavdf.prove_from_hash(parent, 1024, 10000)
    if not verify(parent.hex(), vdf.hex(), 10000, chiavdf.verify_from_hash):
        raise ValueError('Generated VDF failed verification')
    context = {'block_hash':parent.hex(), 'header_prefix':job['header_prefix'], 'vdf':vdf.hex(), 'tick':10000}
    result = exercise(engine, model, output, context, 4, 2052, node_job=job)
    result.update(nodeWorkRecordSha256=record_sha, realVdfVerified=True,
                  engineSha256=hashlib.sha256(engine.read_bytes()).hexdigest())
    (output/'generation.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('record', 'engine', 'gguf', 'out'): parser.add_argument('--'+flag, required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.record, args.engine, args.gguf, args.out), indent=2))
