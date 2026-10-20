"""Diagnostic reference replay, not a consensus verdict or calibration approval."""
import argparse
import gc
import hashlib
import json
from pathlib import Path


def run(args):
    import pfunpack
    import torch
    from transformers import AutoModelForCausalLM
    from noet.model_runtime import approved_pretrained

    if args.out.exists():raise FileExistsError(args.out)
    with args.request.open('rb') as stream:raw=stream.read(2*1024*1024+1)
    if len(raw)>2*1024*1024 or hashlib.sha256(raw).hexdigest()!=args.sha256:
        raise ValueError('Captured envelope differs from the recorded digest')
    proof=pfunpack.unpack_validation_request(raw)['request']['pow_blob']
    model,revision=proof['model_identifier'].split('@')
    # This diagnostic deliberately handles only the unpadded windows observed
    # in this acceptance. Padded contexts need their own positional replay.
    if (not 1<=len(proof['prompt_tokens'])<=4096 or len(proof['chosen_tokens'])!=256
            or any(proof['pad_mask']) or proof['compute_precision']!='bf16'):
        raise ValueError('Unsupported diagnostic context')
    if not torch.cuda.is_available():raise ValueError('CUDA is required')
    torch.set_num_threads(4)
    result={'format':'noet-gpu-logit-diagnostic-v1','envelopeSha256':args.sha256,
        'productionReady':False,'consensusVerified':False,'model':proof['model_identifier'],
        'purpose':'Numerical diagnosis only; no acceptance threshold','references':[]}
    for dtype in (torch.bfloat16,torch.float32):
        reference=approved_pretrained(AutoModelForCausalLM,model,revision=revision,
            dtype=dtype,attn_implementation='eager').to('cuda').eval()
        with torch.inference_mode():
            tokens=torch.tensor([proof['prompt_tokens']+proof['chosen_tokens'][:-1]],device='cuda')
            logits=reference(tokens,use_cache=False).logits[0,len(proof['prompt_tokens'])-1:].float()
            ids=torch.tensor(proof['topk_indices'],device='cuda',dtype=torch.long)
            expected=torch.tensor(proof['topk_logits'],device='cuda')
            if ids.shape!=(256,70) or expected.shape!=ids.shape:raise ValueError('Unexpected evidence dimensions')
            actual=logits.gather(1,ids);difference=(actual-expected).abs()
            if not torch.isfinite(difference).all():raise ValueError('Non-finite comparison')
            result['references'].append({'dtype':str(dtype),'device':str(tokens.device),
                'maxAbsoluteDifference':difference.max().item(),
                'meanAbsoluteDifference':difference.mean().item(),
                'firstStepTop10':logits[0].topk(10).indices.tolist(),
                'reportedRow0':expected[0,:10].tolist(),'referenceRow0':actual[0,:10].tolist(),
                'worstSteps':[
                    {'step':int(step),'maximumAbsoluteDifference':difference[step].max().item(),
                     'meanAbsoluteDifference':difference[step].mean().item(),
                     'token':int(ids[step,difference[step].argmax()]),
                     'reportedLogit':expected[step,difference[step].argmax()].item(),
                     'referenceLogit':actual[step,difference[step].argmax()].item()}
                    for step in difference.amax(dim=1).topk(8).indices.tolist()]})
        del reference,tokens,logits,ids,expected,actual,difference
        gc.collect();torch.cuda.empty_cache()
    args.out.write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request',type=Path,required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--out',type=Path,required=True)
    print(json.dumps(run(parser.parse_args()),indent=2))
