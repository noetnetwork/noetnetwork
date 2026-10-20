"""Research receiver for native node-emitted envelopes on container loopback.

Actual Quick and Full run on the unmodified native envelope. Prompt calibration
remains unapproved/shadow: this is expressly NOT the guarded production service.
Only computed verdicts are returned; exceptions and incomplete evidence abstain.
"""
import argparse
from collections import OrderedDict
import hashlib
import hmac
import json
import os
from pathlib import Path
import time


def replay_seed(secret, envelope_sha256):
    """The replay seed for one envelope: the same envelope always gets the same
    draws on this verifier (also after a restart), and a prover, who does not
    hold the verifier's key, cannot predict them to grind a proof."""
    digest = hmac.new(secret, b'noet-replay-seed/v1:' + bytes.fromhex(envelope_sha256), hashlib.sha256).digest()
    return int.from_bytes(digest[:4], 'little')


def native_height(height):
    """A block height the node may ask about: any positive 32-bit height. (The
    research bound of 1,000 stopped the testnet at height 1,001.)"""
    return type(height) is int and 1 <= height < 2 ** 31


def run(output, deadline_seconds, capture_detector=False):
    import flatbuffers
    import numpy as np
    import pfunpack
    import random
    import torch
    import zmq
    import detector_gate
    from enhanced_logger import create_logger
    from proof_verifier import ProofVerifier
    from noet.verifier_entry import require_challenge_binding
    from noet.verifier_transport import check_request
    from utils.proof import ValidationResponse
    from utils.request_context import read_block_validation_fields
    from zmq_send_broker import build_envelope

    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    require_challenge_binding()
    # Keep the limitation explicit. Do not claim reviewed calibration by setting
    # an allowlist to get past the guarded production entry point.
    if os.environ.get('NOET_REQUIRE_PROMPT_CALIBRATION') == '1':
        raise ValueError('This research receiver cannot replace the guarded service')
    os.environ['POW_PROMPT_GIBBERISH_GATE']='shadow'
    secret=bytes.fromhex(os.environ['VALIDATOR_SHARED_SECRET'])
    if len(secret)!=32: raise ValueError('Expected 32-byte test transport key')
    torch.set_num_threads(3); torch.set_num_interop_threads(1)
    logger=create_logger(name='noet-native-research',reports_dir=str(output/'reports'))
    detector_gate.warm_on_start(logger)
    if not detector_gate.detector_ready(): raise RuntimeError('Sealed detector unavailable')
    context=zmq.Context()
    pull=context.socket(zmq.PULL); push=context.socket(zmq.PUSH)
    for sock in (pull,push):
        sock.setsockopt(zmq.LINGER,0);sock.setsockopt(zmq.MAXMSGSIZE,32*1024*1024)
        sock.setsockopt(zmq.RCVHWM,4);sock.setsockopt(zmq.SNDHWM,4)
    request_port=int(os.environ.get('VALIDATOR_PUSH_PORT','18555'))
    response_port=int(os.environ.get('VALIDATOR_PULL_PORT','18556'))
    if not 1024<=request_port<=65535 or not 1024<=response_port<=65535 or request_port==response_port:
        raise ValueError('Expected distinct unprivileged loopback verifier ports')
    pull.bind('tcp://127.0.0.1:'+str(request_port))
    push.connect('tcp://127.0.0.1:'+str(response_port))
    (output/'ready.json').write_text(json.dumps({'ready':True,'productionReady':False})+'\n')
    deadline=time.monotonic()+deadline_seconds
    count=0
    completed=OrderedDict()
    try:
        while time.monotonic()<deadline:
            if not pull.poll(1000):continue
            frames=pull.recv_multipart()
            if len(frames)!=1:raise ValueError('Expected one native request frame')
            raw=frames[0];check_request(raw)
            item=output/str(count);item.mkdir();count+=1
            (item/'request.bin').write_bytes(raw)
            result={'envelopeSha256':hashlib.sha256(raw).hexdigest(),'nodeEmittedEnvelope':True,
                    'productionReady':False,'promptCalibrationApproved':False}
            started=time.monotonic()
            try:
                request=pfunpack.unpack_validation_request(raw)
                height,ruleset=read_block_validation_fields(raw)
                result.update(height=height,ruleset=ruleset,requestType=request['validation_type'])
                if request['request_type']!=1 or not native_height(height):
                    raise ValueError('Unexpected native validation request')
                cache_key=result['envelopeSha256']
                if cache_key in completed:
                    payload,envelope,prior=completed[cache_key]
                    push.send_multipart([payload,envelope])
                    result.update(height=height,ruleset=ruleset,responseSent=True,
                        responseCode=prior['responseCode'],fullVerdict=prior.get('fullVerdict'),
                        reusedExactEnvelope=True,computedResultDirectory=prior['computedResultDirectory'],
                        elapsedSeconds=time.monotonic()-started)
                    (item/'result.json').write_text(json.dumps(result,indent=2)+'\n')
                    continue
                body=request['request'];proof=body['pow_blob']
                result.update(difficulty=body['difficulty'],model=proof['model_identifier'])
                seed=replay_seed(secret,cache_key);result['replaySeed']=seed
                random.seed(seed);np.random.seed(seed);torch.manual_seed(seed)
                verifier=ProofVerifier(logger=logger)
                kind=request['validation_type']
                if kind==2:
                    runtime=detector_gate.runtime()
                    score=runtime.score
                    def observed_score(snapshot, **kwargs):
                        result['replayViews']=[str(name) for name in snapshot['arrays'].get('cand_names',[])]
                        if capture_detector:
                            try:
                                from verify_current import capture_detector_inputs
                                result['detectorCapture']=capture_detector_inputs(snapshot,item/'detector.npz')
                            except Exception as error:
                                result['detectorCaptureError']=type(error).__name__+': '+str(error)[:300]
                        record=score(snapshot,**kwargs)
                        result['detectorRecordStatus']=record.get('status')
                        result['detectorSteps']=record.get('steps')
                        result['detectorFeatureRefusals']=record.get('feature_refusals',[])
                        return record
                    runtime.score=observed_score
                    try:verdict=verifier.full_verify(raw)
                    finally:runtime.score=score
                    result['fullVerdict']=verdict
                    code={'GREEN':6,'AMBER':7,'RED':8}[verdict]
                elif kind in (0,1):
                    code=int(verifier.quick_verify(raw) if kind==0 else verifier.quick_verify_smell_test(raw))
                    result['quickVerdict']=code
                else:raise ValueError('Unsupported native request kind')
                gate=getattr(verifier,'_last_full_gate',None) or {}
                if gate:result['fullGate']={k:gate.get(k) for k in ('status','max_logit_severity','max_abs_prob_residual')}
                result['promptScore']=getattr(verifier,'_last_prompt_gibberish_decision',None)
                result['detector']=getattr(verifier,'_poi_detector_result',None)
                if verifier.model is not None:
                    result['modelDevices']=sorted({str(p.device) for p in verifier.model.parameters()})
                    result['modelDtypes']=sorted({str(p.dtype) for p in verifier.model.parameters()})
                if torch.cuda.is_available():result['peakCudaBytes']=torch.cuda.max_memory_allocated()
                builder=flatbuffers.Builder(256)
                request_id=bytes(request['hash_id'])
                vec=builder.CreateByteVector(request_id)
                ValidationResponse.Start(builder)
                ValidationResponse.AddHashIdentifier(builder,vec)
                ValidationResponse.AddEnumResponse(builder,code)
                builder.Finish(ValidationResponse.End(builder))
                payload=bytes(builder.Output())
                envelope=build_envelope(secret,request_id,code,ruleset,payload)
                push.send_multipart([payload,envelope])
                result['responseCode']=code
                result['responseSent']=True
                # Native retries must not queue repeated model inference. Cache
                # only completed Full verdicts, keyed by every envelope byte;
                # changed proof/context/ruleset never reuses a prior decision.
                if kind==2 and code in (6,8):
                    completed[cache_key]=(payload,envelope,{'responseCode':code,
                        'fullVerdict':result['fullVerdict'],'computedResultDirectory':item.name})
                    while len(completed)>32:completed.popitem(last=False)
                del verifier
            except Exception as error:
                result.update(exceptionType=type(error).__name__,exception=str(error)[:1500],responseSent=False)
            result['elapsedSeconds']=time.monotonic()-started
            (item/'result.json').write_text(json.dumps(result,indent=2,default=str)+'\n')
    finally:
        detector_gate.runtime().close()
        pull.close();push.close();context.term()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--seconds',type=int,default=1800)
    p.add_argument('--capture-detector',action='store_true')
    a=p.parse_args()
    if not 1<=a.seconds<=86400:p.error('Bound the research receiver to one day')
    run(a.out,a.seconds,a.capture_detector)
