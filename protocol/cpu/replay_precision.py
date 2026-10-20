"""Approximate the v4 Full-gate logit severity of recorded proofs under
bf16 and fp32 replay. Research measurement only; mirrors proof_verifier's
delta_mu, residual and ULP sigma floor (without cov_adjuster, which can only
raise sigma, so these severities are upper bounds)."""
import json, sys, hashlib
from pathlib import Path
import torch
from transformers import AutoModelForCausalLM

BINDINGS, MODEL, *PROOFS = sys.argv[1:]
sys.path.insert(0, BINDINGS)
from proof.MiningResponse import MiningResponse


def half_ulp_bf16(x):
    exp = torch.floor(torch.log2(torch.abs(x))).clamp(-126, 127)
    return torch.pow(2.0, exp - 7 - 1)


def sigma_floor(sorted_logits, logits_b):
    ulp_full0 = half_ulp_bf16(sorted_logits) * 2
    ulp_raw = half_ulp_bf16(logits_b) * 2
    s = lambda u: u * (5 ** 0.5) / (6 ** 0.5)
    avg = s(ulp_full0).mean()
    per = torch.maximum(torch.full_like(ulp_raw, avg), s(ulp_raw))
    mag = min(sorted_logits.std().item() * 0.005, 0.1)
    return torch.maximum(torch.full_like(per, mag), per)


models = {}
def model(dtype):
    if dtype not in models:
        models[dtype] = AutoModelForCausalLM.from_pretrained(
            MODEL, local_files_only=True, trust_remote_code=False,
            use_safetensors=True, dtype=dtype, attn_implementation='sdpa').eval()
    return models[dtype]


torch.set_num_threads(8)
out = []
for path in PROOFS:
    raw = Path(path).read_bytes()
    r = MiningResponse.GetRootAs(raw, 0)
    p = r.PowBlob()
    if p is None or p.ChosenTokensLength() != 256:
        continue
    prompt = p.PromptTokensAsNumpy().tolist()
    chosen = p.ChosenTokensAsNumpy().tolist()
    idx = torch.tensor([p.TopkIndices(i).ValuesAsNumpy().tolist() for i in range(256)])
    a = torch.tensor([p.TopkLogits(i).ValuesAsNumpy().tolist() for i in range(256)], dtype=torch.float32)
    lse = [p.LogsumexpStats(i).ValuesAsNumpy().tolist() for i in range(p.LogsumexpStatsLength())]
    mu_a = torch.tensor([row[-1] for row in lse], dtype=torch.float32)
    tokens = torch.tensor([prompt + chosen[:-1]])
    rec = {'proof': path.split('/output/')[-1], 'sha256': hashlib.sha256(raw).hexdigest()[:16],
           'version': p.Version(), 'promptTokens': len(prompt)}
    for name, dt in (('bf16', torch.bfloat16), ('fp32', torch.float32)):
        with torch.inference_mode():
            full = model(dt)(tokens, use_cache=False).logits[0, len(prompt) - 1:].float()
        b = full.gather(1, idx)
        delta_mu = mu_a - full.mean(dim=1)
        resid = (a - b) - delta_mu[:, None]
        sev = []
        for s in range(256):
            sl, _ = torch.sort(full[s], descending=True)
            sev.append((resid[s].abs().max() / sigma_floor(sl, b[s]).max()).item())
        sev = torch.tensor(sev)
        rec[name] = {'maxSeverity': round(sev.max().item(), 2), 'argmaxStep': int(sev.argmax()),
                     'p99Severity': round(torch.quantile(sev, .99).item(), 2),
                     'stepsOver6': int((sev >= 6).sum()), 'stepsOver12': int((sev > 12).sum()),
                     'maxAbsResidual': round(resid.abs().max().item(), 4)}
    print(json.dumps(rec), flush=True)
    out.append(rec)
