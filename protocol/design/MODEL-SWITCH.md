# Model switch: Qwen3-0.6B to Qwen3-14B

NOET Mainnet launched with Qwen3-0.6B as its consensus model. It moves to Qwen3-14B (`Qwen/Qwen3-14B@40c069824f4251a91eefaf281ebe4c544efd3e18`) at a height announced in advance, S = 3,500. Blocks below S carry the 0.6B model and blocks from S the 14B model. The switch is a hard fork: every node, verifier and miner upgrades before S.

The switch is defined in `mainnet/network.json` under `modelSwitch`: height 3,500 and `targetScale` [2, 1], set on 23 October 2026. With no height, the node sources are byte for byte those of the deployed node (`tests/test_model_switch.py`).

## Consensus (node overlay, `testnet/prepare.py`)

| Change | Where | Effect |
| --- | --- | --- |
| One model per height | `validation.cpp` `CheckBlockModelRegistered` | A block below S must carry the 0.6B identifier and a block from S the 14B identifier, or it is invalid (`bad-model-for-height`). |
| 14B registered | `modeldb.cpp` | Registered at startup like the genesis model, also in a database created before the upgrade. Difficulty 1,000,000 (the normalizer), so `nAdjBits` equals `nBits` as for 0.6B. |
| Both models protected | `validation.cpp` `sole_model_protected` | See below. |
| Difficulty reset at S | `consensus/params.h` phase C | Block S takes `floor(T(S-1) * num / den)`, and ASERT continues from a fresh anchor at S with the same 12-hour half-life. |
| Model of the height | `node/miner.cpp`, `net_processing.cpp` | Block assembly and compact-block reconstruction fill in the model of the block's height. |

**Why both models are protected.** Upstream bans a model after 50 challenge commits. The verifier check of a challenge is commented out upstream, so a challenge needs only deposits. Upstream protects a model while it is the only registered one, and registering 14B ends that. Without the extra rule, anyone could ban the model a height requires and stop the chain. NOET resolves a challenge against either scheduled model as failed, the same path upstream uses for a sole model.

**Why phase C.** Upstream already has a one-off target reset with a fresh ASERT anchor, which NOET had switched off. The overlay points it at S and sets its ratio. ASERT corrects any error in the ratio: a target off by 2x costs about a day of slow or fast blocks.

**Alternatives not taken.**
- *On-chain registration.* Registering 14B through a deposit and verifier audit would keep 0.6B valid unless a minimum difficulty is also set. It would also depend on the registration audit, which NOET's verifier restricts to its approved model.
- *Upstream's minimum model difficulty.* This would give 14B a higher registered difficulty and so change the work accounting.

The height rule is explicit, small and testable.

## Verifier

`noet/model_runtime.py` accepts a second reviewed snapshot. It uses `NOET_MODEL_ROOT_2`, `NOET_MODEL_MANIFEST_2` and `NOET_MODEL_MANIFEST_SHA256_2`, and loads whichever snapshot matches the proof's model exactly. One verifier therefore replays both sides of the switch. It needs an 80 GB GPU: the fp32 14B replay peaked at 70 GiB (`validation/model-qwen3-14b.json`). Full verification of a 14B proof took 224 to 275 s on an A100 in the rehearsal (median 242 s), against about 20 s for 0.6B on the same GPU (`validation/model-switch-rehearsal.json`).

## Miner

`miner/noet_miner.py`:
- **Pins.** It pins both GGUF digests and each network's switch, as (height, model) or none.
- **Per attempt.** It runs the model of the job's height.
- **Both files.** Given both GGUF files (`--model` twice), it crosses S on its own.
- **Old file only.** It stops with a message at S.
- **New file only.** It waits until S.
- **Outdated miner.** One that does not know the switch refuses a node that lists 14B, so it stops at the node upgrade rather than mining invalid blocks after S.

| | 0.6B | 14B |
| --- | --- | --- |
| GGUF (bf16) | 1.5 GB, `8fa11f4d…0700` | 29.6 GB, `b4ac198a…dbe9` |
| Producer GPU memory | 6 GiB required | 28 GiB peak; 34 GiB required |
| 516 tokens | 11.7 s (A40) | 19.2 s (A100) |
| Attempt with engine start, A100 | 24 s | 36 s |

24 GB cards (RTX 3090, RTX 4090) cannot run the 14B producer. Miners need a 48 GB card (A40, RTX A6000, L40S) or larger.

## Upgrade order

1. **Announce.** Set `modelSwitch.height` (S) and `targetScale` and announce them at least a week ahead. `targetScale` is the expected slowdown of an attempt on the network's mining hardware, between 1 and 4. On one A100 an attempt took 1.5 times as long with 14B. 48 GB cards have less memory bandwidth and should slow down more, and 24 GB cards drop out, so mainnet uses [2, 1].
2. **Verifier.** Start a verifier host with both snapshots (80 GB GPU) and point the node's relay at it. The A40 live pod cannot hold the 14B replay.
3. **Miners.** Release the miner with the switch pinned in `NETWORKS` and the 14B GGUF available. Miners convert it with `convert.sh`. The hosted vault miner needs a GPU with at least 34 GiB free for 14B.
4. **Node.** Deploy the node built from the overlay with the height set. Its restart registers 14B; nothing changes before S.
5. **At S.** The first 14B block takes the eased target. Watch block times for a day while ASERT settles.

Keep the 0.6B snapshot on verifiers after S, so the chain from genesis can still be checked.

## Checks

- `tests/test_model_switch.py`, `test_public_miner.py`, `test_model_runtime.py`, `test_gpu_evidence.py`: the overlay, the release node unchanged without a height, the miner's schedule and model choice, the two-snapshot verifier and the rehearsal audit.
- `testnet/switch_check.py` on a real test-variant node (22 October 2026, 260 blocks on a mocked clock). It mined to height 60 with no switch, then restarted with the switch at 200: the 14B model was registered next to 0.6B. A 14B block at height 199 and a 0.6B block at height 200 were both refused (`bad-model-for-height`). Every block carried its height's model. The target eased exactly 2.5x at the switch (`201b248c` to `2043db5e`). Every nBits matched the independent ASERT port, and a restart after the switch kept the chain and both models.
- Not exercised on a node: the challenge protection, which needs 50 challenge transactions. It is covered by source checks only.
- A GPU rehearsal on a private chain with real verification (22 October 2026, `validation/model-switch-rehearsal.json`, `gpu/stage.py --switch-profile`, `native.py --relabel-after-switch`). The chain switched from 0.6B to 14B at height 8 and ran to height 61 in six hours: 7 0.6B and 54 14B blocks, each final only after a GREEN Full verdict, and none wrongly refused by Full. Honest 14B proofs peaked at 1.75 sigma against the 12 sigma cap. Proofs made with 0.6B weights but claiming 14B passed the quick checks and were RED in Full at 102 to 133 sigma (6 of 6 over two runs). One honest 14B proof of 56 copied its prompt and was refused at submit by the anti-parrot rule (`bad-prompt-scaffold`); like a miss, that costs the attempt only. It was not a borderline case: 120 of 128 tokens in its window came from the prompt where the rule needs 77, and 60 of 64 where it needs 52, so the thresholds stay as they are.
