"""GPU replay of a captured native envelope; research only, no network verdicts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import time


def run(args):
    import flatbuffers
    import numpy as np
    import pfunpack
    import torch
    import detector_gate
    from enhanced_logger import create_logger
    from proof_verifier import ProofVerifier
    from proof.ValidationRequest import ValidationRequest, ValidationRequestT
    from utils.request_context import read_block_validation_fields
    from noet.verifier_entry import require_challenge_binding

    if args.out.exists():raise FileExistsError(args.out)
    with args.request.open('rb') as stream:original=stream.read(2*1024*1024+1)
    if len(original)>2*1024*1024 or hashlib.sha256(original).hexdigest()!=args.sha256:
        raise ValueError('Native envelope does not match the recorded digest')
    if not torch.cuda.is_available():raise ValueError('This acceptance requires CUDA')
    if os.environ.get('NOET_REQUIRE_PROMPT_CALIBRATION')=='1':
        raise ValueError('Research replay cannot replace the guarded service')
    os.environ['POW_PROMPT_GIBBERISH_GATE']='shadow'
    require_challenge_binding()
    raw=original
    if args.case!='bound':
        message=ValidationRequestT.InitFromObj(ValidationRequest.GetRootAs(original,0))
        block=message.request
        if args.case=='wrong-parent':block.prevBlockHash=list(bytes.fromhex('cd'*32))
        elif args.case=='missing-height':block.height=0
        elif args.case=='missing-ruleset':block.ruleset=0
        elif args.case=='different-model':block.powBlob.modelIdentifier='NOET/unapproved@'+'a'*40
        elif args.case=='corrupt-vdf':
            value=bytearray(block.powBlob.vdf)
            if len(value)!=200:raise ValueError('Unexpected native VDF fixture')
            value[-1]^=1;block.powBlob.vdf=list(value)
        elif args.case=='forged-logits':
            for row in block.powBlob.topkLogits:
                row.values=list(row.values);row.values[0]+=50
        elif args.case=='nudged-logit':
            # One step's top logit raised by 4 and kept on the bf16 grid: far
            # beyond honest arithmetic error (under 0.5 for Qwen3-14B), far
            # below forged-logits, and inside the MCA candidates' noise.
            row=block.powBlob.topkLogits[100];row.values=list(row.values)
            row.values[0]=torch.tensor(row.values[0]+4).bfloat16().float().item()
        builder=flatbuffers.Builder(1024);builder.Finish(message.Pack(builder));raw=bytes(builder.Output())
    parsed=pfunpack.unpack_validation_request(raw)
    height,ruleset=read_block_validation_fields(raw)
    random.seed(args.seed);np.random.seed(args.seed);torch.manual_seed(args.seed)
    torch.set_num_threads(4);torch.set_num_interop_threads(1)
    torch.cuda.reset_peak_memory_stats()
    logger=create_logger(name='noet-gpu-replay',reports_dir=str(args.out.parent/'reports'))
    detector_gate.warm_on_start(logger)
    if not detector_gate.detector_ready():raise RuntimeError('Sealed detector unavailable')
    verifier=ProofVerifier(logger=logger)
    result={'format':'noet-gpu-native-replay-v1','sourceEnvelopeSha256':args.sha256,
        'envelopeSha256':hashlib.sha256(raw).hexdigest(),'case':args.case,'replaySeed':args.seed,
        'height':height,'ruleset':ruleset,'model':parsed['request']['pow_blob']['model_identifier'],
        'productionReady':False,'promptCalibrationApproved':False,'nodeEmittedEnvelope':args.case=='bound',
        'scope':'GPU replay of captured native work; no live lease, block acceptance or calibration approval'}
    started=time.monotonic()
    runtime=detector_gate.runtime();score=runtime.score
    def observe(snapshot,**kwargs):
        result['replayViews']=[str(x) for x in snapshot['arrays'].get('cand_names',[])]
        record=score(snapshot,**kwargs)
        result['detectorRecordStatus']=record.get('status')
        result['detectorSteps']=record.get('steps')
        result['detectorFeatureRefusals']=record.get('feature_refusals',[])
        return record
    runtime.score=observe
    try:
        result['quickVerdict']=int(verifier.quick_verify(raw))
        result['fullVerdict']=verifier.full_verify(raw)
    except Exception as error:
        result.update(exceptionType=type(error).__name__,exception=str(error)[:1500])
    finally:
        runtime.close();runtime.score=score
    result['elapsedSeconds']=time.monotonic()-started
    result['modelLoaded']=verifier.model is not None
    if verifier.model is not None:
        result['modelDevices']=sorted({str(p.device) for p in verifier.model.parameters()})
        result['modelDtypes']=sorted({str(p.dtype) for p in verifier.model.parameters()})
    result['peakCudaBytes']=torch.cuda.max_memory_allocated()
    result['detector']=getattr(verifier,'_poi_detector_result',None)
    result['promptScore']=getattr(verifier,'_last_prompt_gibberish_decision',None)
    args.out.write_text(json.dumps(result,indent=2,default=str)+'\n')
    if args.case=='bound':
        passed=(result.get('quickVerdict')==0 and result.get('fullVerdict')=='GREEN'
            and 'exception' not in result and result.get('modelDevices')==['cuda:0']
            and result.get('replayViews')==['1','2','5','10','20','spda']
            and result.get('detectorRecordStatus')=='scored')
    elif args.case in ('missing-height','missing-ruleset'):
        passed=result.get('exceptionType')=='RulesetContextError' and not result['modelLoaded']
    elif args.case=='different-model':
        passed='exception' in result and not result['modelLoaded'] and 'fullVerdict' not in result
    else:
        passed=result.get('fullVerdict')=='RED' and 'exception' not in result
    print(json.dumps({k:result.get(k) for k in ('case','quickVerdict','fullVerdict','exception','elapsedSeconds','modelDevices','peakCudaBytes')}))
    return passed


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request',type=Path,required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--seed',type=int,default=2342964126)
    p.add_argument('--case',choices=('bound','wrong-parent','missing-height','missing-ruleset','different-model','corrupt-vdf','forged-logits','nudged-logit'),default='bound')
    a=p.parse_args()
    if not 0<=a.seed<2**32:p.error('Seed must be uint32')
    raise SystemExit(0 if run(a) else 2)
