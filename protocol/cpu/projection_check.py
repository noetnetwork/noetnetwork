"""Actual CPU replay-method projection parity on pinned weights.

This tests selected-position equivalence, not MCA calibration or detector support.
"""
import argparse
import json
import os
from pathlib import Path
import types
from unittest.mock import Mock


def run(record, output):
    import torch
    import pfunpack
    from transformers import AutoModelForCausalLM
    from noet.model_runtime import approved_pretrained
    from proof_verifier import ProofVerifier
    if output.exists(): raise FileExistsError(output)
    proof = pfunpack.unpack_mining_response(record.read_bytes())['pow_blob']
    model, revision = proof['model_identifier'].split('@')
    torch.set_num_threads(3); torch.set_num_interop_threads(1)
    base = approved_pretrained(AutoModelForCausalLM, model, revision=revision,
                              use_safetensors=True, torch_dtype=torch.float32).eval()
    class Observe(torch.nn.Module):
        def __init__(self, model):
            super().__init__(); self.inner=model; self.calls=[]
        def forward(self, input_ids, attention_mask, use_cache, logits_to_keep=0):
            self.calls.append({'batch':input_ids.shape[0], 'context':input_ids.shape[1],
                'selected':logits_to_keep.tolist() if isinstance(logits_to_keep,torch.Tensor) else logits_to_keep})
            return self.inner(input_ids=input_ids, attention_mask=attention_mask,
                              use_cache=use_cache, logits_to_keep=logits_to_keep)
    model = Observe(base)
    prompt=torch.tensor(proof['prompt_tokens']); chosen=torch.tensor(proof['chosen_tokens'])
    captured=[]
    obj=types.SimpleNamespace(model=model, prompt_tokens=prompt, chosen_tokens=chosen,
        pad_mask=torch.zeros_like(prompt,dtype=torch.bool), use_flash_attn=False,
        stated_dtype=torch.bfloat16, logger=Mock(), _gumbel_head_mode=lambda:'h0',
        _apply_v0_replay_logit_policy=lambda x:x,
        _prompt_gibberish_capture=lambda x:captured.append(list(x.shape)))
    method=ProofVerifier._compute_all_logits_parallel_prefix
    os.environ['NOET_CPU_FULL_LOGITS']='1'
    torch.manual_seed(7)
    old=method(obj,256,batch_size=2,mca_noise_value=12.0,as_tensor=True,compact_rows=True)
    os.environ.pop('NOET_CPU_FULL_LOGITS')
    torch.manual_seed(7)
    new=method(obj,256,batch_size=2,mca_noise_value=12.0,as_tensor=True,compact_rows=True)
    maximum=float((old-new).abs().max())
    assert torch.equal(old,new), 'Projection changed logits: '+str(maximum)
    assert model.calls[0]['selected']==0
    assert model.calls[1]['selected']==list(range(len(prompt)-1,len(prompt)+255))
    assert model.calls[0]['context']==model.calls[1]['context']==len(prompt)+256
    del old,new
    baseline=method(obj,256,batch_size=1,mca_noise_value=None,as_tensor=True)
    assert model.calls[-1]['selected']==0
    assert captured==[[len(prompt)+256,base.config.vocab_size]]
    result={'format':'noet-cpu-logit-projection-parity-v1','model':proof['model_identifier'],
        'promptTokens':len(prompt),'windowTokens':256,'batchSize':2,'maxAbsDifference':maximum,
        'bitwiseEqual':True,'fullAttentionContextPreserved':True,'promptScoringRowsPreserved':True,
        'baselineShape':list(baseline.shape),'productionReady':False,
        'scope':'Actual replay method and pinned model weights; projection equivalence only, no MCA calibration or block acceptance'}
    with output.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proof',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();run(args.proof,args.out)
