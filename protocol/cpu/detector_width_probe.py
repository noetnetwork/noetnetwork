"""Adversarial feature-width fixture assembled from recorded research views.

This deliberately combines audits. It is not a mined proof, a natural replay,
or evidence that the original unseeded failure had this exact cause.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import types
from unittest.mock import patch
from detector_diagnostics import checked_archive


def check(directory, output):
    import numpy as np
    import torch
    import pfunpack
    import detector_gate as gate
    from poi_detector import policy
    from poi_detector.advisory_bundle import load
    if output.exists(): raise FileExistsError(output)
    torch.set_num_threads(1)
    reports, captures = [], []
    for seed in range(6):
        report=json.loads((directory/f'diagnostic-seed{seed}.json').read_text())
        source=checked_archive(directory/f'diagnostic-seed{seed}.npz',report['detectorCapture']['sha256'])
        with np.load(source,allow_pickle=False) as archive:
            captures.append({key:archive[key] for key in archive.files})
        reports.append(report)
    assert len({r['proofSha256'] for r in reports}) == 1
    arrays={key:value.copy() for key,value in captures[0].items() if key!='__meta_json'}
    meta=json.loads(str(captures[0]['__meta_json']))
    # This fixed fixture was selected to stress the documented 128-slot limit.
    # Every replacement is a complete, already observed view at this position.
    step=232
    choices=[0,2,3,3,0,4]
    for view,source_index in enumerate(choices):
        source=captures[source_index]
        for key,value in arrays.items():
            if value.ndim>=2 and value.shape[:2]==(6,256):
                value[view,step]=source[key][view,step]
    ids=set(int(t) for t in arrays['declared_ids'][step] if t>=0)
    ids.update(int(t) for t in arrays['cand_value_top50_ids'][:,step].reshape(-1) if t>=0)
    assert len(ids)>128
    raw=(directory/'v4-eight-windows/proofs/9201_2036078033_d1155c79.bin').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==reports[0]['proofSha256']
    proof=pfunpack.unpack_mining_response(raw)['pow_blob']
    support=reports[0]['detectorSupport']
    meta.update(head_declared=support['head_supported']['observed'],
                head_verifier=support['verifier_head_matches_declaration']['observed']['verifier'],
                window_size=support['window_size_supported']['observed'])
    # Older captures stored only the value contract and vocabulary. Explicit
    # fixture metadata supplies the research harness's identity-sampling profile.
    meta.update(replay_eligibility_contract='poi-replay-eligibility-v2',top_k=50,
                temperature=1,top_p=1,repetition_penalty=1,sampling_stop_token_ids=[])
    bundle=os.environ['POI_DETECTOR_BUNDLE']
    manifest=gate.verify_bundle_against_policy(bundle)
    backend,_,loaded_manifest=load(bundle)
    assert manifest==loaded_manifest
    snapshot={'arrays':arrays,'meta':meta,'proof':proof,'bound':{},'verdict':{}}
    record=backend.score(snapshot)
    assert record['status']=='scored' and 233 in record['steps']['eligible_positions'], {
        key:record.get(key) for key in ('status','reason','failed_checks','steps','feature_refusals')}
    refusal=next(r for r in record['feature_refusals'] if r['step']==step)
    assert f'union size {len(ids)} exceeds the fixed token width 128' in refusal['reason']
    activation=policy.Activation(policy.REQUIRED,None,1,4,'v4',None,'Constructed width-stress fixture')
    # Execute the real gate on the real backend record. Only its IPC/readiness
    # boundary is replaced; no feature, score, decision or predicate is changed.
    with patch.object(gate,'detector_ready',return_value=True), \
         patch.object(gate,'_RUNTIME',types.SimpleNamespace(score=lambda *a,**k:record)):
        try:
            gate.evaluate(activation=activation,staged=(meta,arrays),proof=proof,bound={},verdict={})
        except gate.DetectorEvidenceIncomplete as error:
            message=str(error)
        else: raise AssertionError('Oversized eligible evidence did not abstain')
    assert refusal['reason'] in message
    result={'format':'noet-detector-width-stress-v1','position':233,'unionTokens':len(ids),
        'fixedTokenWidth':128,'sourceSeedByView':choices,'featureRefusal':refusal,
        'exceptionType':'DetectorEvidenceIncomplete','exception':message,
        'manifestSha256':manifest,'proofSha256':reports[0]['proofSha256'],
        'sourceCaptureSha256':[r['detectorCapture']['sha256'] for r in reports],
        'naturalReplay':False,'productionReady':False,
        'scope':'Cross-audit adversarial fixture, real sealed feature/model scoring and gate; constructed identity-sampling metadata/context and stubbed IPC readiness. Not a native proof or a reproduction of the lost unseeded inputs.'}
    with output.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args();check(args.directory,args.out)
