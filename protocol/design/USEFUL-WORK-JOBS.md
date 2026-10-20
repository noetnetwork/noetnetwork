# Useful-work jobs: design proposal

Status: draft for owner review, 12 October 2026. Nothing here is implemented. Phases 0 and 1 need no consensus change; phase 2 does and is optional.

NOET uses useful proof of work (UPoW): a block is valid only when it carries evidence that a pinned AI model ran a specific computation, evidence that the node and an independent verifier can replay. Today that computation is real but its output serves no one. This document proposes how outside requests ("jobs") can become the computation, and how anyone can check a result afterwards.

**Recommendation in one line:** keep miner-chosen work as the default, add an opt-in public job lane through the gateway with verifiable work records anchored on chain (no consensus change), and only consider consensus-level job binding after that lane shows real demand.

## 1. What a block proves today

### 1.1 Correction: the node does not choose the prompt

The brief assumed the protocol generates prompts. The code says otherwise. `create_mining_work_unit` (`protocol/.upstream/services/core-node/bcore/src/rpc/mining.cpp`, around line 1797) returns only `req_id`, a 76-byte `header_prefix`, `target`, `height`, `expires_at`, `tip_hash` and the `stepbind`/`v4` flags. There is no prompt in a work unit.

The miner picks the prompt. `protocol/realwork/generate.py` (used by `protocol/gpu/miner.py` and `protocol/miner/noet_miner.py`) hard-codes a template: `Research case {height}.{req_id}: Write a careful explanation of how to verify numerical computations...`. Upstream's miner proxy fills idle GPU time with "dummy" prompts from a template generator (`.upstream/services/miner-api/src/components/proxy.py`, `default_prompt_generator.py`).

Consequence: **a miner can already mine on a user's prompt without any consensus change.** Consensus constrains prompts (section 1.4) but never supplies them.

### 1.2 What is bound into the proof

| Input | How it is bound | Source |
| --- | --- | --- |
| Parent block | `header_prefix` bytes 4..36 must equal the proof's `block_hash`; VDF is computed over the parent hash (chiavdf, 1024-bit, 10,000 iterations in `generate.py`) | `realwork/generate.py`, `VERIFIER-RUNTIME.md` (challenge binding) |
| Payout address and coinbase | `header_prefix` contains the merkle root, which commits to the coinbase (payout script, height push, `extranonce_tag`, 8-byte allocation nonce) | `rpc/mining.cpp` `EncodeHeaderPrefixFields`, `IssueWorkUnits` |
| Prompt | Prompt commitment `SHA256("TC_V3_PROMPT_CTX" \| n \| prompt_tokens \| pad_mask)` feeds the effective step nonce, so every sampling draw depends on the whole prompt | `verification/pow_v3.h`, `pow_v4.h` (E4 = SHA256(TAG_STEP_V4 \| C \| R \| A \| S4)) |
| Sampling randomness | Chain-bound single Gumbel race; each step's randomness derives from header, nonce R, Argon2id root A and the sampler identity S4. No user seed | `verification/pow_v4.h`, `.upstream/docs/proof-v4.md` |
| Sampler profile | Fixed: temperature 1.0, top_k 50, top_p 1.0, repetition penalty 1.0 | `miner-api/.../proxy.py` `_validate_and_rebound_sampling_params`, `cpu/exercise.py` |
| Model | `model_identifier` = `Qwen/Qwen3-0.6B@c1899de2...`, registry entry and difficulty normalizer 1,000,000 | `gateway.py` `_info`, `primitives/proofblob.h` `GetModelHash` |
| Proof body | `hashPoW` in the header commits to the proof (merkle root over tick, VDF, meta and rest leaves) | `primitives/block.h`, `primitives/proofblob.h` |

A proof covers exactly one 256-token generated window (`prompt_scaffold.h` `WINDOW = 256`). The miner generates 516 tokens per attempt (`generate.py` calls `exercise(..., 4, 516, ...)`), giving up to two candidate windows. For a later window, `prompt_tokens` includes the earlier generated tokens (`pow_uniq.h`), which the verifier treats as prompt, not as proven model output.

### 1.3 What verification replays

1. The node runs the C++ QuickVerifier: carrier flags, sampler profile, Gumbel race replay, pricing, prompt and continuation history, scaffold and candidate-band rules (`verification/quick_verifier.cpp`, `pow_uniq.h`, `prompt_scaffold.h`, `.upstream/docs/candidate-band.md`).
2. The node sends a FlatBuffers `ValidationRequest` (`shared-utils/fb-schemas/validation.fbs`: header fields, `pow_blob`, `difficulty`, `height`, `ruleset`) to the external GPU verifier.
3. The verifier replays the model in fp32 against the declared top-k logits, runs the prompt-surprisal gate and the sealed detector (`.upstream/docs/detector-rule.md`), and returns GREEN, AMBER or RED (`realwork/verify.py`). One recorded Full verdict on an A40 took about 33 s and RED verdicts 3 to 15 s (`validation/gpu-acceptance.json`); treat these as indicative only.
4. The verdict travels back in an envelope authenticated with a shared secret (`build_envelope(secret, ...)` in `verify.py`). It is not a public signature.

### 1.4 Rules every job prompt must pass

| Rule | Effect on user jobs | Source |
| --- | --- | --- |
| Exact-prompt and continuation history (`bad-proof-prompt-reused`) | The same prompt tokens can appear in only one block per model; the continuation slice [5, 69) must also be fresh. Identical user prompts need a per-job salt | `verification/pow_uniq.h` |
| Prompt-surprisal gate (14.0-bit threshold, 32 minimum transitions, 256-transition tail) | "Gibberish-like" prompts get RED. Code, non-English text or encoded data may be at risk; calibration for this model is **not approved** | `VERIFIER-RUNTIME.md` |
| Anti-parrot scaffold rule (`bad-prompt-scaffold`) | Outputs that copy prompt spans are rejected. Extraction, editing, quoting and many summarization jobs are poor fits | `verification/prompt_scaffold.h` |
| Evidence pricing, M in [1, 3] | Low-entropy (predictable) outputs face up to a 3x harder target. Factual and code answers are likely to cost miners more than open-ended text. Coefficients are provisional | `verification/pow_v4.h`, `.upstream/docs/proof-v4.md` |
| Detector and candidate band | Additional RED paths on the output window | `.upstream/docs/detector-rule.md`, `candidate-band.md` |
| Size | Proof at most 2 MiB through the gateway; research harness limits prompts to 2,048 characters and a 4,096-token context. A 1,051-token prompt once exhausted 20 GiB in CPU replay. **No consensus maximum prompt length was found** | `noet/gateway.py`, `cpu/exercise.py`, `VERIFIER-RUNTIME.md` |

### 1.5 Public versus private today

| Data | Where it lives |
| --- | --- |
| Prompt token IDs, pad mask, 256 output tokens, chosen probabilities, sampling values, top-k logits and indices, VDF, header prefix, sampler settings, v3/v4 carrier (nonce R) | On chain inside the serialized block (`CBlock.pow`, `primitives/block.h`). Anyone with the raw block (`getblock <hash> 0`) and the pinned tokenizer can read the prompt and window text |
| Model identifier and `hashPoW` | On chain; also in `getblock` JSON (`tst`, `hashPoW`, `rpc/blockchain.cpp`). The explorer indexer stores only these two (`noet/indexer.py`) |
| Full verdict | Off chain, node-local (`getrecentvalidations`), not signed for third parties |
| Text outside the winning window, losing attempts, prompt source | Off chain, miner only |

Any miner can already publish arbitrary (gate-passing) text on chain through its prompt. The explorer must treat prompt text as untrusted content.

## 2. Design options

### Option A: gateway job lane, miner-side (no consensus change)

The gateway keeps a job queue. When a miner that opted in leases work, the lease may carry a job. The miner runs the job prompt (wrapped in a fixed template with a job salt) as its mining prompt, returns the full output and every window's proof to the gateway, and submits normally if a window meets the target.

```
user --POST /jobs--> gateway queue --lease + task--> miner (pinned engine)
                         ^                              |
                         |   output + window proofs     |
                         +------------------------------+
                         |                              | winning window
                job verifier pool                       v
                (Quick all, Full sampled)        node --> block verifier
                         |
                work record + anchor root --> next lease extranonce_tag --> block coinbase
```

- Consensus: none. The lease API, miner and gateway change.
- Determinism: unchanged. The job only changes `prompt_tokens`; sampler profile and chain-bound randomness stay fixed, so the user gets one sample at temperature 1.0, not a greedy or seeded answer.
- Enforcement: none at consensus level. A miner can ignore the job and mine its own prompt. Incentive comes from off-chain payment or reputation.

### Option B: commitment-bound jobs (consensus change)

A job is published on chain (a job transaction) with commitment `J = SHA256(template_id | prompt_commitment | max_tokens | expiry)` and an optional bounty. A block whose proof's prompt commitment equals `J`'s may claim the bounty in its coinbase; expired bounties refund.

- Consensus: new output type or claim rule, expiry and refund rules, wallet support, activation height. Verification of the binding is a pure hash comparison against the prompt commitment the QuickVerifier already computes.
- Privacy: none gained. Every verifier needs the plaintext prompt tokens to replay, so "hash on chain, prompt elsewhere" would make block validity depend on off-chain data. Not recommended.
- Capacity: at most one job per block, about 144 per day at 600-second spacing (`nPowTargetSpacing = 10 * 60`, `kernel/chainparams.cpp`).

### Option C: hybrid (recommended)

Miner-chosen prompts remain the default so mining never stalls waiting for jobs. Option A runs as an opt-in lane. Option B is a later, separate decision.

### Comparison

| Concern | A: gateway lane | B: on-chain binding | C: hybrid |
| --- | --- | --- | --- |
| Consensus change | No | Yes | Not for phases 0 and 1 |
| Miners choosing easy prompts | Miners can cherry-pick high-entropy jobs or skip jobs; assign jobs with deadlines and pay per verified job, not per block | Miners pick profitable bounties; pricing already penalizes low-entropy output, so bounties must offset it | As A |
| Grinding | Job prompt is fixed by template and salt; miner can still vary nonce R and pick among samples. Gateway checks exact `prompt_tokens` | Same; binding is exact | As A |
| Prompt stuffing / griefing | Adversarial prompts can trip the surprisal gate or detector and cost the miner a block. Gateway pre-screens with the same gate; miner may refuse | Same, but on-chain jobs cannot be pre-screened by anyone trusted | As A |
| Privacy leak | Miner sees plaintext; the winning window publishes prompt and 256 output tokens forever | Prompt fully public in the job transaction | Public jobs only may reach a block |
| Spam | Existing gateway rate limits (60 leases and 120 submits per hour per client) plus job quotas | Paid by transaction fee | As A |
| Verifier DoS | Job verification runs on a separate pool, never the block verifier; Full replay sampled or paid; prompt length capped | Block verifier only; needs a consensus prompt-length cap | As A |
| Latency to output | Seconds once a persistent engine is used (today's miner restarts the engine each attempt, up to 120 s startup) | Next block at best, often never | As A |
| Latency to on-chain anchor | Next block mined from a later lease, about 10 minutes plus confirmations | Only if a miner takes the bounty | As A |
| Cost | Miner GPU seconds per job, verifier replay per checked job, 40 bytes of coinbase | Plus job transaction bytes and wallet work | As A |

## 3. Verifiable work records ("receipts")

Naming note: the brand guide retires receipt-themed assets (`branding/current/brand-guide.md`). This document uses "work record" in product copy and "receipt" only as an engineering term. Owner decision below.

### 3.1 Grades

| Grade | Meaning | Trust needed |
| --- | --- | --- |
| Replayable | Record plus raw proof bytes; anyone with the pinned model and verifier can rerun it | None beyond the pinned artifacts |
| Anchored | Record hash is a leaf in a merkle root committed in a block coinbase | Chain consensus for the timestamp |
| In block | The job's window is the block's own proof, accepted with a Full GREEN | Chain consensus |

### 3.2 Format `noet-work-record/1`

| Field | Content |
| --- | --- |
| `format` | `noet-work-record/1` |
| `network` | name and genesis hash |
| `model` | `Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca`, GGUF SHA-256 `8fa11f4d...`, manifest SHA-256 `c18e242c...` |
| `job` | `id`, `templateId`, `salt`, `promptTextSha256`, `promptTokensSha256`, `promptCommitment` (TC_V3_PROMPT_CTX digest), `maxTokens`, `lane` (public or committed) |
| `sampler` | fixed profile and S4 identifier `ff256c6c...` |
| `work` | `headerPrefix`, `parent`, `height`, `target`, `reqId` |
| `windows[]` | per 256-token window: `index`, `proofSha256`, `hashPoW` (proof merkle commitment), `outputTokensSha256`, `isSolution` |
| `output` | `textSha256`, `tokenCount`, `provenRanges` (token ranges covered by window proofs) |
| `verification` | `quick`, `full` (GREEN/AMBER/RED/null), `ruleset`, detector bundle digest, `replayDtype`, optional `attestation` {`keyId`, `signature`} |
| `anchor` | `kind` (`none`, `coinbase-root`, `in-block`), `blockHash`, `height`, `leafIndex`, `merklePath` |

Leaf hash: `SHA256(0x00 | "NOET_WORK_RECORD_V1" | JCS(record without anchor and attestation))`, with interior nodes `SHA256(0x01 | left | right)` to separate leaves from nodes. The coinbase anchor goes in the `extranonce_tag` the gateway already passes to `create_mining_work_unit` (today `secrets.token_hex(8)`): `"NWR1" | root (32 bytes) | batch sequence (4 bytes)` is 40 bytes. With the height push and the node's 8-byte allocation nonce this fits the 100-byte scriptSig limit (`rpc/mining.cpp` line 1764 enforces it).

### 3.3 How a third party checks a record

Offline (strongest):

1. Verify model files against the manifest digest (`noet/model_artifacts.py`).
2. Decode the proof (`MiningResponse` FlatBuffer), check `promptTokensSha256`, recompute the prompt commitment, check `header_prefix` parent and the VDF (`noet/vdf.py`).
3. Run Quick and Full with the pinned verifier image and detector bundle (`cpu/verify_full.py`, `realwork/verify.py` paths). Expect GREEN.
4. Detokenize the window with the pinned tokenizer and compare `outputTokensSha256`.

Online:

5. Coinbase anchor: fetch the block at `height`, check it is in the best chain, parse the coinbase scriptSig, follow `merklePath` to the root.
6. In block: check the block's `hashPoW` equals the proof's merkle commitment and the block is in the best chain.

### 3.4 What a record proves and does not prove

Proves: the pinned model, given exactly these prompt tokens and this chain-derived randomness, produced these 256 tokens within verifier tolerances; the work happened after the parent block (header and VDF binding) and, if anchored, before the anchor block.

Does not prove:

- that the answer is correct, safe or useful (a 0.6B model sampled at temperature 1.0 is often wrong);
- anything about tokens outside proven windows, including earlier text carried into a later window's prompt;
- that this was the first or only sample: a miner can vary nonce R and return the sample it prefers (assumption to test: whether the gateway can fix R per job in the free tier);
- who asked, or that the miner and gateway did not keep the prompt;
- a verifier verdict, unless the record is replayed or carries an attestation signature the reader chooses to trust. Today's verdict envelopes use a shared secret, so they cannot be checked by outsiders.

## 4. Privacy, pricing and decisions

### 4.1 Privacy model

| Lane | Prompt visible to | Can become a block | Notes |
| --- | --- | --- | --- |
| Public | Everyone, permanently if it wins | Yes | Explicit consent per job; content screening before queueing |
| Committed | Miner and job verifier; only salted hashes in records | No | The block must carry plaintext tokens, so committed jobs are served and anchored, never in block. Salt the hash so short prompts cannot be guessed |
| Encrypted | Miner after decryption | No | Upstream has X25519/AES-GCM confidential-mode code (`miner-api/.../confidential_crypto.py`), but the miner still sees plaintext. Real confidentiality needs trusted hardware; out of scope |

Writing user text to an immutable chain creates content and data-protection risk (no erasure). The public lane needs terms, screening and a reporting route before launch.

### 4.2 Pricing and payment

- **Free lane (testnet):** rate-limited, public prompts only, best effort, no payment to miners beyond the ordinary chance of a block. Fits the brand rule of never describing rewards as earnings.
- **API credits:** reuse Portal keys and spend tracking (`web/server/portalHttp.ts`, `lib/inference.ts`, `lib/spend.ts`). A job lane would appear as a separate provider labeled clearly; ordinary provider-based inference stays separate and does not mine.
- **Fees in NOET:** off-chain payouts from the gateway to miners (custodial, operator-run) in phase 1, or on-chain bounties in phase 2. Test coins have no value; no fee should be described as income.

### 4.3 Owner decisions

- [ ] Accept option C (hybrid) and the phase order below.
- [ ] Public lane only for phase 1, or also a committed lane?
- [ ] Content policy, terms and moderation owner for prompts that may be written permanently on chain.
- [ ] Explorer display policy for prompt text in existing blocks (shown by default or behind a click).
- [ ] Product name: "work record", "receipt" or other, given retired receipt-themed assets.
- [ ] Whether to add a public verifier attestation key, and who holds it.
- [ ] Payment model: free only, API credits, off-chain NOET payouts, or none until mainnet.
- [ ] Allowed task types given the anti-parrot and surprisal gates (exclude extraction and copy tasks).
- [ ] Prompt-calibration approval for the model, a prerequisite for any public prompts.
- [ ] Job limits: maximum prompt tokens (proposal 1,024), output tokens (516), per-key quotas.
- [ ] Full-replay sampling rate for job records and the verifier GPU budget.
- [ ] Data retention for off-chain prompts and outputs.
- [ ] Whether other gateways may run job lanes, or only the official gateway at first.
- [ ] Whether to fund phase 2 consensus work, and whether to add a consensus prompt-length cap regardless.
- [ ] Whether the fixed temperature 1.0 profile and a 0.6B model are acceptable for "useful" output, or a model or sampler change (consensus) belongs on the roadmap.

## 5. Phased plan

### Phase 0: show the work that already exists (no consensus or miner change)

- Indexer reads raw blocks (`getblock <hash> 0`), decodes `CBlock.pow`, and stores prompt tokens, window tokens, sampler fields, VDF tick and `hashPoW`. Detokenize with the pinned tokenizer.
- Explorer block page: "Work in this block" with prompt text (escaped, labeled as miner-chosen and untrusted), the proven 256-token window, model, parent binding and the node's Full verdict while available.
- In-block work records for every block, downloadable with the raw proof, plus an offline checker script built from `cpu/verify_full.py`.
- Optional: replace the fixed research template with a curated public task list (still miner-side).
- Requires: block deserializer for the proof blob, indexer schema change, explorer page, checker script. Risks: displaying arbitrary miner text; verdicts are node-local and expire from `getrecentvalidations`, so persist them at index time.

### Phase 1: opt-in public job lane (no consensus change)

- Gateway: `POST /jobs`, job queue, screening (length cap, surprisal pre-check, content policy), template with job salt, optional `task` in `/mining/work` leases, `/jobs/{id}/result` for output and window proofs.
- Miner: `--accept-jobs` flag, persistent engine instead of a restart per attempt, return all window proofs (upstream "audit" emissions are prior art: `proof_processor.cpp` `audit_emit`).
- Job verifier pool separate from the block verifier: Quick on every record, Full sampled or paid.
- Records anchored through `extranonce_tag` roots in later leases.
- Requires: prompt calibration approval, terms, verifier capacity, Portal integration. Risks: miners ignore jobs; griefing prompts cost miners blocks; verifier cost; reliance on one gateway; content liability.

### Phase 2: consensus job binding (only with demand)

- Job transactions with commitment and bounty, coinbase claim rule, expiry refunds, consensus prompt-length cap, optional consensus-recognized record root.
- Requires: written specification, C++ and Python mirrors, golden vectors, functional tests, activation height, wallet support, independent review.
- Risks: consensus complexity for at most about 144 jobs per day; bounty economics interacting with entropy pricing; job-transaction spam; any bug is a chain-split risk.

## 6. Assumptions and unknowns

- Not verified in code: whether a window may contain an end-of-sequence token (the harness forces `ignore_eos`), and exactly how 516 tokens map to windows.
- Not verified: whether the miner engine accepts a gateway-chosen nonce R in the free tier.
- No consensus maximum prompt length was found; the 2 MiB proof limit and model context are the only observed bounds.
- How the prompt-surprisal gate treats code, other languages or structured text is unmeasured; calibration is not approved.
- Verifier timings come from a handful of recorded runs and are not throughput measurements.
- The upstream audit and confidential paths were read, not run, and are not part of NOET's current miner.
- Phase 1 economics (whether anyone pays for 0.6B samples at temperature 1.0) are unknown and should be tested with the free lane first.
