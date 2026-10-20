# Candidate models

`profiles.json` lists the model NOET Mainnet runs (`qwen3-0.6b`) and larger candidates for a future upgrade. A candidate is not approved for any network. Mainnet keeps its own hard pins (`mainnet/genesis.py`, `miner/noet_miner.py`, `gpu/run.sh` defaults); `tests/test_model_profiles.py` checks that they agree with the mainnet profile.

| Profile | Weights (bf16) | Files | Tokenizer |
| --- | --- | --- | --- |
| qwen3-0.6b | 1.5 GB | 10 | reference |
| qwen3-8b | 16.4 GB | 15 | identical |
| qwen3-14b | 29.6 GB | 18 | identical |
| qwen3-32b | 65.5 GB | 27 | identical |

All four are Apache-2.0 and share the tokenizer byte for byte, so gateway tasks and the answer vocabulary do not change with the model. The larger models do not tie their input and output embeddings; the verifier reads the output head through `get_output_embeddings()` and handles both.

## Manifests

`candidate.py` writes a manifest from the Hugging Face API without downloading weights. Small files are downloaded and checked against their Git blob IDs; weight files are Git LFS objects, whose ID is the SHA-256 of the content. Every host still verifies the full download against the manifest. Regenerating the mainnet manifest this way reproduces `cpu/qwen3-06b.candidate.json` byte for byte.

```sh
python3 protocol/models/candidate.py --model Qwen/Qwen3-8B \
  --revision b968826d9c46dd6066d109eabc6255188de91218 --license Apache-2.0 --out qwen3-8b.candidate.json
```

## Testing a candidate on a rented GPU

```sh
python3 protocol/gpu/stage.py --out stage-8b --builder sha256:<native builder> --profile qwen3-8b
```

The stage builds a private network that registers the candidate (`realwork/prepare.py --model --revision`) and writes `model.json`, which `gpu/run.sh` and `gpu/native.py` read; without it they run the mainnet model as before. On the host, `gpu/model_session.sh` fetches and converts the model twice (the GGUF digest is pinned in `profiles.json` only when both conversions agree), then mines a private chain with real verification and records every verdict, GPU memory every 5 seconds, an audit and a summary.

The producer holds the bf16 GGUF (about the size of the weights) and the verifier replays in fp32 (twice the weights). Before building or downloading anything, `gpu/plan_host.py` checks the host: with two GPUs that fit, the producer runs on GPU 0 and the verifier on GPU 1; otherwise both share GPU 0, and a host that is too small stops at once with what the model needs. Its headroom figures (6 GB producer, 10 GB verifier) are estimates until a test records peak memory in `gpu-memory.csv`. Sizes are in GiB, as GPU names count them (an "80 GB" card has 80 GiB).

| Profile | One GPU | Two GPUs (producer, verifier) | Disk |
| --- | --- | --- | --- |
| qwen3-8b | 62 GB: A100 or H100 80 GB | 22 GB, 41 GB: 2x RTX A6000 48 GB | 72 GB |
| qwen3-14b | 99 GB: H200 141 GB | 34 GB, 66 GB: 2x A100 80 GB | 121 GB |
| qwen3-32b | 200 GB: none | 68 GB, 133 GB: 2x H200 141 GB | 255 GB |

The verifier never splits one model across GPUs, so 32B needs a card of at least 133 GB for the verifier alone (H200 or B200).

## Before a candidate can replace the mainnet model

1. Honest proofs pass and faked proofs are rejected, measured over many blocks.
2. The detector covers the candidate (no unexplained withheld verdicts) and its error margins hold on the GPUs miners use.
3. Prompt-gate calibration for the candidate.
4. Difficulty set for the new attempt time.
5. A planned upgrade height for node, verifier and miners, announced in advance.
