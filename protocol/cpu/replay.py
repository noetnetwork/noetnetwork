#!/usr/bin/env python3
"""Measure a legacy evidence record against an independent CPU model replay.

No pass threshold is invented here. This is a numerical comparison, not the
consensus verifier, and it must never be used to award mining rewards.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca'
MANIFEST_SHA256 = 'c18e242c1f4c2273d89a27a226856ad1a6623a7a0c22839495b45371eefa3ae4'


def replay(proof_path, schema_path, model_path):
    # Set before importing the model libraries. No Hub token or remote code.
    for name in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY'):
        os.environ[name] = '1'
    for name in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'HUGGINGFACE_HUB_TOKEN'):
        os.environ.pop(name, None)
    sys.path.insert(0, str(ROOT))
    from noet.model_artifacts import verify
    verify(model_path, (Path(__file__).parent / 'qwen3-06b.candidate.json').read_bytes(), MANIFEST_SHA256)
    sys.path.insert(0, str(schema_path))
    from proof.MiningResponse import MiningResponse
    import torch
    from transformers import AutoModelForCausalLM
    with proof_path.open('rb') as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if not 1 <= len(raw) <= 2 * 1024 * 1024:
        raise ValueError('Evidence size is outside the experiment limit')
    response = MiningResponse.GetRootAs(raw, 0)
    proof = response.PowBlob()
    if response.ReqId() != 9201 or proof is None or proof.Version() != 2 or proof.ModelIdentifier() != MODEL.encode() or proof.ComputePrecision() != b'bf16':
        raise ValueError('Unexpected experiment evidence identity')
    if not 1 <= proof.PromptTokensLength() <= 768 or proof.ChosenTokensLength() != 256 or proof.TopkIndicesLength() != 256 or proof.TopkLogitsLength() != 256:
        raise ValueError('Unexpected experiment evidence dimensions')
    if any(proof.TopkIndices(i).ValuesLength() != 70 or proof.TopkLogits(i).ValuesLength() != 70 for i in range(256)):
        raise ValueError('Unexpected reported logit row dimensions')
    prompt = proof.PromptTokensAsNumpy().tolist()
    chosen = proof.ChosenTokensAsNumpy().tolist()
    indices = torch.tensor([proof.TopkIndices(i).ValuesAsNumpy().tolist() for i in range(256)], dtype=torch.long)
    expected = torch.tensor([proof.TopkLogits(i).ValuesAsNumpy().tolist() for i in range(256)], dtype=torch.float32)
    tokens = torch.tensor([prompt + chosen[:-1]], dtype=torch.long)
    if tokens.min() < 0 or tokens.max() >= 151936 or indices.min() < 0 or indices.max() >= 151936 or not torch.isfinite(expected).all():
        raise ValueError('Invalid token identities or non-finite evidence')
    torch.set_num_threads(3)
    torch.set_num_interop_threads(1)
    model = AutoModelForCausalLM.from_pretrained(model_path, local_files_only=True,
        trust_remote_code=False, use_safetensors=True, torch_dtype=torch.float32,
        attn_implementation='eager').to('cpu').eval()
    if next(model.parameters()).device.type != 'cpu':
        raise ValueError('This experiment requires CPU execution')
    with torch.inference_mode():
        logits = model(tokens, use_cache=False).logits[0, len(prompt)-1:].float()
        actual = logits.gather(1, indices)
        differences = (actual - expected).abs()
        if not torch.isfinite(differences).all():
            raise ValueError('Non-finite replay output')
        return {'model':MODEL, 'candidateManifestSha256':MANIFEST_SHA256,
            'evidenceSha256':hashlib.sha256(raw).hexdigest(), 'proofVersion':2,
            'replayedTokens':256, 'reportedLogits':expected.numel(),
            'referenceDtype':'float32', 'producerDtype':'BF16 GGUF', 'device':'cpu',
            'torch':torch.__version__,
            'maxAbsoluteLogitDifference':float(differences.max()),
            'meanAbsoluteLogitDifference':float(differences.mean()),
            'p99AbsoluteLogitDifference':float(torch.quantile(differences.flatten(), .99)),
            'productionReady':False, 'consensusVerified':False,
            'scope':'Independent numerical replay of legacy v2 evidence; no full verifier, VDF, GPU, block or consensus acceptance'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proof', type=Path, required=True)
    parser.add_argument('--schemas', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Use a new report path')
    report = replay(args.proof.resolve(), args.schemas.resolve(), args.model.resolve())
    with args.out.open('x') as stream:
        stream.write(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
