# Validating NOET blocks without a GPU

Status: design analysis, 12 October 2026; corpus result added 13 October (section 2a). Nothing here is approved for production. Statements marked **[Assumption]** have not been measured. Statements marked **[Unverified]** come from reading code, not from running it.

## Question

Every block's useful-work proof (a Qwen3-0.6B inference run with a chiavdf VDF) is accepted only after an external **Full** verifier replays it. The public testnet node refuses to start without real external verification (`testnet/prepare.py`, `init()`). Today that verifier runs on a rented GPU. What would let an outside operator with no GPU validate real blocks independently?

## Short answer

1. **The plumbing already works on CPU.** A native node in `-validationapi=real` mode has accepted a block whose Full verdict came from the CPU verifier image: `noet-private-work-v1` height 1, verifier image `38c10227…`, Full GREEN. See `validation/private-realwork.json`. The research receiver that the GPU host runs (`realwork/verify.py`, launched by `gpu/live.py`) does not depend on the device, so the testnet's `init()` guard does not by itself block a CPU node. What is missing is evidence and throughput, not a new integration.
2. **"Bit-for-bit" is the wrong target.** Full verification is a seeded statistical test, not an exact recomputation. Any two verifiers, GPU or CPU, can only aim for the same verdict. Today the CPU and GPU paths deliberately use different noise models. On 13 October, 102 GPU-mined blocks were replayed on CPU: no CPU verdict contradicted the GPU, but 28 got no verdict because the detector could not score them (section 2a).
3. **CPU can keep up with the tip but cannot practically sync long history.** On 3 ARM64 cores, one Full replay takes 90–480 s, depending mostly on prompt length. Blocks arrive every 600 s. Checking a year of history would take 55–290 days on 3 cores.
4. **Recommended path:**
   - **CPU Full at the tip:** aim for the same verdicts as the GPU verifier (option a below).
   - **Assume-full-valid history:** trust a reviewed anchor block for older history (option d).
   - **Optional signed attestations:** k-of-n verifier signatures for machines that cannot keep up (option c).

   Block-sampling (option b) conflicts with the v4 "no chainwork without Full" rule. Cheaper proof formats (option e) are mainnet-scale research.

## 1. How a block is verified today

| Stage | Where it runs | Needs GPU? | What it checks |
| --- | --- | --- | --- |
| Header, transactions, scripts, amounts, the testnet allocation rule | Native node (Bitcoin Core fork) | No | Ordinary ledger validity |
| **Quick** (`verification/quick_verifier.cpp`, `pow_v4.cpp`) | Native node, before anything is sent to the verifier | No | VDF, header hash against target, Argon2 admission nonce/StepBind, sampler parameter bounds, Gumbel race winner and chosen-token CDF on the *reported* logits, evidence pricing, reuse history |
| **Credit kernel** (`verification/credit_v4.cpp`, native library ABI 6) | Node and verifier | No | Deterministic integer "credit units" computed from the declared top-k logit rows. CORE-MATH correctly rounded `exp`/`log`, no FMA contraction, FTZ/DAZ refused, so results are identical across platforms. This is consensus arithmetic over the *reported* numbers, not model inference. |
| **Full** (`proof_verifier.py`, 10.5k lines, upstream TensorCash v1.2.2) | External Python service over ZMQ (local) or the mutual-TLS relay (`noet/secure_transport.py`) | Today, yes in practice | Re-runs Qwen3-0.6B over prompt plus 256 generated tokens and tests whether the reported top-k logits are consistent with honest inference |
| **Detector** (sealed bundle `e738325f…`, `detector_gate.py`) | Bounded **CPU** subprocess, even on GPU workers | No (≈0.5 s) | U1 and F3 classifiers over the six replay views. At ruleset 4 its AMBER/RED count toward Full (`docs/detector-rule.md`). Missing views mean no verdict. |
| Prompt-gibberish gate | Inside Full | No (one prompt forward) | Shadow mode; calibration not approved |

Full verification works in four steps:

1. It computes a reference forward pass and five more "views" of the same prefix: batch sizes 2, 5, 10 and 20 with Monte Carlo Arithmetic noise injected into attention, plus an SDPA view. These emulate honest hardware variation.
2. It builds an error covariance from them.
3. For each step it chooses the view most favourable to the prover.
4. It caps the residual "logit severity" at 12σ.

The replay is randomized: `verify_current.py` and `realwork/verify.py` record a `replaySeed` per run. See `.upstream/documentation/verification/full-verification.md`.

**Full is local fork-choice policy, not a block-validity rule** (`.upstream/.../src/chain.h`):

- Full RED marks the block `BLOCK_FULL_RED_LOCAL`: it adds zero local chainwork and counts as a penalty.
- From `ProofV4Height` onward (height 1 on the testnet), a block connected without a terminal Full verdict is `BLOCK_FULL_PENDING_LOCAL`. It also adds zero chainwork until a GREEN arrives.

Consequences:

- A node with no Full verifier would connect blocks but give the whole chain zero work. Its fork choice would be meaningless.
- A verifier that disagrees with the network on a borderline proof makes its node choose a different tip.
- Peers' Full verdicts are recorded (`RecordPeerFullStatus`, `extFulls`) but only to settle a local AMBER. They are unsigned peer claims. **[Unverified]**

Existing upstream features that matter here:

- `-fullvalidationtipwindow=<n>` (default 400): a non-live node (still in initial sync, or behind its best header) only *requires* Full for blocks within n of a tip. Under v4, blocks outside the window become Full-pending (zero work). A resumable sweep then asks for their Full verdicts anyway: batch 2×window, three retry rounds, then left pending.
- `-validationapi=desktop`: Quick runs locally and Full is fetched over HTTPS from one configured endpoint. The verdict is protected by TLS and an API key, not by a signature over the verdict. The NOET testnet `init()` refuses this mode.
- SPV M-of-N body sampling for deep reorgs. Blocks accepted that way are also Full-pending.

### Why the CPU path is called "legacy"

The scope strings in `validation/cpu-verifier.json` ("CPU legacy experimental replay; no current-rules or native block acceptance") describe the **first** CPU run. It used **proof version 2** envelopes, because the native packer did not carry height or ruleset. Later evidence goes further:

- `cpu-six-views.json`: a ruleset-4 proof passes Quick and Full on CPU with all six detector views, no violation over 105 eligible positions.
- `node-work-cpu.json`: five v4 proofs (prompts of 27–1,563 tokens) pass with six views. This was ruleset 2, where the detector is advisory.
- `private-realwork.json`: one native block accepted with a CPU Full GREEN (ruleset 2).

What genuinely remains "legacy" or different on CPU, from the upstream code:

| Difference | CPU | GPU | Source |
| --- | --- | --- | --- |
| Attention MCA noise constant | 12.0 | 8.0 | `proof_verifier.py` around line 9396 |
| Noise shape | Row-wise σ (max of each row) | Element-wise σ | `_fp_noise` |
| Linear-projection noise | Disabled | k_lin = 1.5 | `_noisy_linear_impl` |
| Candidate batch views | Upstream: `[2]` only. NOET restores `[2,5,10,20]` when the detector attaches. | `[2,5,10,20]` | NOET verifier patch |
| Reference compute dtype | fp32 | fp32 since 12 Oct (`NOET_REPLAY_COMPUTE`); bf16 before | `_replay_compute_dtype` |
| Flash attention view | Not available | Off by policy (flash attention made honest proofs RED) | `gpu-acceptance.json` |

So the CPU verifier is a **different statistical test** from the GPU verifier, on purpose. Its calibration evidence covered only CPU-produced proofs: `replay-precision.json` lists "CPU-produced proofs on CPU replay" as a limit. The 13 October corpus (section 2a) is the first replay of GPU-mined proofs through the CPU verifier.

Other known gaps (from `RELEASE-STATUS.md` and `VERIFIER-RUNTIME.md`):

- **Mandatory-detector nondeterminism.** One unseeded ruleset-4 replay returned no verdict (`DetectorEvidenceIncomplete`): eligible position 233 was not scored, because the sealed 128-slot feature contract cannot hold more than 128 distinct tokens. Seeds 0–6 passed. The corpus in section 2a shows the same refusal on 28 of 102 GPU-mined blocks, so it is a systematic CPU-path gap, not a rare event.
- **Calibration.** Prompt-gibberish calibration is not approved. Detector coverage of this model version is unmeasured. Both apply to GPU and CPU alike.
- **The guarded service refuses to run.** `noet/verifier_entry.py` refuses to start until the calibration is declared. The GPU host uses the research receiver in shadow mode, and a CPU operator would have to as well.

## 2. Cost per block on CPU

### Recorded Full timings

These ran on 3 Linux ARM64 vCPUs in the colima VM on an Apple M4 Max, with a 20 GiB container and no GPU.

| Proof | Prompt tokens | Views | Full time | Source |
| --- | --- | --- | --- | --- |
| Legacy v2 `75993752…` | 27 | 1, 2, SDPA (compact) | 41.8 s (original), 48.4 s (streamed) | `cpu-verifier.json` |
| v4 `5904f69f…` | 27 | six | 91.0 s | `node-work-cpu.json` |
| v4 `c2bf925e…`, synthetic context, 7 seeds | short **[Assumption: under 100 tokens]** | six | 87.9–90.6 s | `detector-diagnostics.json` |
| v4 `509f9d2e…` | 795 | six | 253 s | `node-work-cpu.json` |
| v4 `4e8a766f…` | 1,051 | six | 331 s | same |
| v4 `7e2b147a…` | 1,307 | six | 399 s | same |
| v4 `f83609ef…` | 1,563 | six | 481 s | same |
| Invalid candidate (RED before replay) | n/a | none | 8.8 s | same |

For comparison, GPU Full times:

| Hardware | Full time |
| --- | --- |
| RTX A5000, fp32 | median 22.8 s (`gpu-acceptance.json`, `fp32ReplayAcceptance`) |
| A40, bf16 | 20–33 s |
| A40 fresh peer re-verifying 34 blocks | 1,019 s total, about 30 s per block |

Peak CUDA memory was 13.7 GB with fp32. The CPU ran 1,051–1,563-token prompts within 20 GiB only after the `logits_to_keep` projection fix. The original 6 GiB VM could not run the legacy replay.

A rough fit to the six-view CPU rows: **t ≈ 19 s + 0.25 s × (prompt tokens + 256)** on 3 cores. It is within about 12% for every row above. Prompt length, which comes from the node-issued job, is therefore the main cost driver. **I found no consensus cap on prompt length** in the verifier or Quick constants (only `_MCA_MAX_CONTEXT_MULTIPLE`). **[Unverified]** Worst-case CPU cost is unbounded unless the work issuer caps it.

### New micro-benchmark (12 Oct, this analysis)

Setup:

- Image `noet-verifier:diagnostics` with torch 2.8.0+cpu.
- Pinned Qwen3-0.6B snapshot loaded in fp32 with SDPA, 3 threads.
- Random token IDs, `logits_to_keep=256`.
- Container options: `--network none`, read-only root, 12 GiB. The container was removed afterwards.

Single forward pass (one "view"):

| Batch × tokens | Seconds | Tokens/s |
| --- | --- | --- |
| 1 × 283 (27-token prompt + 256) | 1.66 | 171 |
| 1 × 1,307 | 8.68 | 151 |
| 1 × 1,819 (1,563 + 256) | 13.71 | 133 |
| 2 × 283 | 2.68 | 212 |
| 5 × 283 | 6.53 | 217 |
| 20 × 283 | 31.58 | 179 |

The six views amount to 1 + 2 + 5 + 10 + 20 + 1 = **39 sequence-forwards**. For a 283-token context that is about 57 s of pure forward work. The rest of the observed ≈90 s is covariance warm-up, bootstrap statistics, the detector and prompt scoring (inferred by subtraction).

Two consequences:

- **A single baseline view costs 1.7–14 s on 3 cores, about 30× less than the six-view Full.** Almost all CPU cost comes from the noise-emulation and detector views, not from checking the model's logits once.
- Sampling token *positions* saves little. Checking position t requires a prefill of prompt + t tokens, and the prompt dominates for long prompts.

### What this means for a node operator

Blocks arrive at 144 per day. "Load" means the fraction of the 600 s block interval spent verifying (t/600). "Catch-up" is the time to clear a backlog while the chain keeps growing: backlog × t / (1 − t/600).

| Full time per block (3 cores) | Verification per day of chain | Load at the tip | Verify 1 month of history (4,320 blocks), static | Catch up 1 month while chain grows | Verify 1 year (52,560), static |
| --- | --- | --- | --- | --- | --- |
| 90 s (short prompts) | 3.6 h | 15% | 4.5 days | 5.3 days | 55 days |
| 250 s (≈800-token prompts) | 10 h | 42% | 12.5 days | 21 days | 152 days |
| 480 s (≈1,560-token prompts) | 19.2 h | 80% | 24 days | 120 days | 292 days |

- **[Assumption]** With 8–16 modern cores, Full time might fall 2.5–5×. Thread scaling was not measured: the VM has 3 vCPUs, and resizing it would disturb the running `noet-testnet-check` container.
- **[Assumption]** x86 servers with AVX-512 or AMX may differ substantially from these ARM64 numbers.
- **Queueing at the tip.** Blocks arrive at random intervals. At 80% load, a single verifier queue adds roughly t × ρ / (2(1 − ρ)) ≈ 16 minutes of average extra lag (M/D/1 queue estimate).
- **[Unverified]** Whether a live node relays a block before its own Full verdict. If it does not, CPU relays would add minutes of delay per hop. CPU nodes should be leaves, not miners' relays.
- **Memory.** Six-view replay of long prompts needs about 16–20 GiB of RAM. A practical CPU validator is therefore 8+ cores with 24–32 GB RAM, not a laptop. **[Assumption]**

Conclusion: CPU Full verification is viable **at the tip** for short-to-medium prompts. It is **not** viable for syncing long history, and it is marginal for long prompts on small machines.

## 2a. Corpus result: GPU-mined blocks replayed on CPU (13 October)

Record: `validation/cpu-gpu-corpus.json`. Tool: `cpu/replay_envelopes.py`. Each of the 102 distinct GPU-verified envelopes from the 12 October A5000 run (heights 1–102; 100 at ruleset 4, where the detector is required) was replayed in the CPU verifier image `38c10227…` on 3 ARM64 cores with the GPU run's recorded `replaySeed`.

| Measure | Result |
|---|---|
| GPU verdicts | 102 GREEN |
| CPU verdicts | 74 GREEN, 0 AMBER, 0 RED, 28 no verdict |
| Detector decision and flags, where both scored | 72 of 72 identical |
| Maximum logit severity on CPU | 0.28σ (median 0.14σ; cap 12σ) |
| Time per block | median 130 s, 95th percentile 243 s, maximum 330 s |
| Peak memory | 9.5 GiB RSS |

**Every no-verdict is the detector width limit.** At one to three eligible positions per block, the union of candidate tokens across the replay views was 129–157, above the sealed contract width of 128, so the required detector refused and the block got `DetectorEvidenceIncomplete`. The GPU verifier recorded no such refusal in any of its 847 Full checks. The CPU path's different noise model produces wider candidate sets. The rate depends on prompt length: 24 of 43 blocks with ~294-token prompts, 4 of 59 with ~38-token prompts.

**What this shows.** The CPU verifier does not falsely reject GPU-mined blocks in this sample, with a wide severity margin. It cannot act as a ruleset-4 validator today: it would stall on about one block in four (more than half of long-prompt blocks). The fix is upstream of NOET's code: a detector bundle with a wider token contract, or CPU replay views that stay within 128 candidates.

**What it does not show.** There is no negative set, so nothing about rejecting forgeries. It covers one GPU run and one CPU platform, with no repeat runs. CPU severities are measured against the CPU noise model and are not comparable to GPU severities. **The GPU's two rejections look like false rejections.** Of 1,199 live GPU Full checks of our own miner's proofs (12–13 October), 2 were RED, both on the logit-severity cap: 14.19σ (testnet height 9) and 12.42σ (development chain height 201). Replayed on CPU, neither came near the cap: height 9 passed GREEN at 0.21σ with the detector clear, and height 201 reached 0.20σ before the same detector width refusal. Each was checked once on GPU, with one seed, and later requests reused the cached RED, so whether a different GPU seed would pass them is not measured. About 1 in 600 honest Full checks rejected on GPU is a liveness cost for miners now, and a split risk once several independent verifiers decide the same block.

## 3. Options

### (a) CPU verifier that gives the same verdicts as the GPU verifier

- **Exact equality is not achievable.** GPU kernels, MCA noise and bootstrap draws are not bitwise reproducible across hardware, and llama.cpp BF16 miners never match an fp32 reference exactly.
- **The achievable target is verdict equivalence.** On every honest proof both say GREEN with margin. On every adversarial proof both say RED. The detector reaches the same decision.
- **Work:**
  1. **Make draws deterministic.** Seed every random draw from the block hash, envelope hash and rule digest, and fix thread counts. Then the same platform class always gives the same verdict, and disputes can be replayed.
  2. **Decide the noise model.**
     - Option 1: use one MCA model on both devices (element-wise, k_attn 8, k_lin 1.5). This changes the CPU path.
     - Option 2: keep the CPU-specific model and calibrate it separately against GPU-mined proofs.
  3. **Build a cross-hardware corpus.** Replay real GPU-mined blocks on CPU and CPU-mined blocks on GPU, plus graded forgeries.
  4. **Close the detector coverage gap.** It currently causes nondeterministic abstention at the 128-slot limit, and needs a reviewed bundle release.
- **Security:** the same as GPU Full once equivalence is shown. If the verdicts diverge, the CPU node follows a different tip than GPU nodes: a fork-choice split rather than an invalid ledger.
- **Consensus change:** none. Full is local policy and the thresholds stay unchanged. The deterministic seed is a verifier-side change; it must enter the verdict cache key.
- **Effort [Assumption]:** 1–2 weeks for seeding plus corpus tooling, 2–4 weeks for calibration analysis, plus GPU rental for the reverse direction. Detector width is upstream-dependent and open-ended.
- **Limit:** still 90–480 s per block, so syncing history needs (d).

### (b) Probabilistic or sampled verification

Three things could be sampled. They behave very differently:

- **b1, sample blocks.** Fully verify a random fraction p and leave the rest provisional.
  - Under v4, an unverified block contributes **zero** chainwork. Sampling would therefore need a fork-choice rule change, such as provisional work that is revoked on RED. That is a consensus-relevant redesign of the v4 "no work without Full" invariant.
  - Detection probability per block is p. A cheater who mines k blocks escapes with probability (1 − p)^k.
  - Fraud proofs are weak here: a "this block is RED" claim costs a full replay to check, and verdicts are seeded and statistical. Claims would need bonding and rate limits to avoid denial of service.
  - Not recommended for v4 as specified.
- **b2, sample views: baseline-only spot check.** About 30× cheaper (1.7–14 s per block on 3 cores).
  - It should catch gross forgery: the existing `forged-logits` (+50) and `falsifiedLogits` cases already return RED. That is not measured for a baseline-only rule.
  - It cannot evaluate the ruleset-4 detector, which needs all six views, and it has no honest-noise envelope until calibrated.
  - Useful as an **advisory local tier** for attested nodes: independent evidence that the logits are roughly the model's. It must not award GREEN on its own without a reviewed rule.
  - Effort: 1–2 weeks plus calibration. No consensus change if advisory.
- **b3, sample tokens or layers.** Positions save little (see section 2). Layers cannot be checked independently unless the proof commits intermediate activations, which is option (e).

### (c) Signed verdict attestations (k-of-n)

- **Design:**
  - Verifier operators sign `(block hash, envelope SHA-256, height, ruleset, verdict, verifier image digest, detector rule digest, seed)`.
  - The node is configured with n verifier public keys and a threshold k.
  - It treats a block as Full GREEN when at least k keys say GREEN and none says RED.
  - Any RED triggers local escalation: an operator alert, plus a local CPU replay if one is available.
  - Upstream `-validationapi=desktop` is a one-endpoint, unsigned precursor. The ZMQ verdict HMAC (`VALIDATOR_SHARED_SECRET`) is a 1:1 shared secret and cannot be published.
- **What it protects:** the node still checks headers, the VDF, the target, Quick (including the credit kernel, Gumbel race and pricing), reuse history and **every transaction rule** locally. Colluding attesters (at least k of them) can only:
  1. make the node credit chainwork to blocks whose logits were not produced by the model (fake useful work);
  2. withhold or RED honest blocks, which censors or partitions this node.

  They cannot create invalid transactions or inflate the supply.
- **Security:** weaker than (a); the node trusts the attester set for useful-work validity. **[Unverified]** How much a fake-inference miner saves while still paying the VDF, Argon2 and target costs; this needs a separate cost model.
- **Consensus change:** none. It is a new local validation backend. The testnet `init()` guard currently allows only `real`, so an `attested` mode needs an owner decision and a guard update.
- **Effort [Assumption]:** 2–4 weeks: signature format with test vectors, key distribution, node backend, gossip or HTTP fetch, tests. The attesters must actually be independent; three GPU hosts run by one operator add little.

### (d) Assume-full-valid history plus full verification at the tip

- **Design:**
  - Add a release-pinned or operator-supplied anchor (`-assumefullvalid=<blockhash>`, analogous to Bitcoin's `-assumevalid`).
  - Ancestors of the anchor that are on the best-header chain are treated as Full GREEN for chainwork, so they skip the Full-pending zero-work lane.
  - Blocks after the anchor are verified locally (a) or attested (c).
  - Today's `-fullvalidationtipwindow` already skips Full outside the window. Under v4, though, it leaves those blocks at zero work and sweeps them later, so it does not help on its own.
- **Security:** like Bitcoin's assume-valid. The node trusts the release reviewers' claim that historical proofs were GREEN, but still checks headers, the VDF, Quick and all transactions for history. An attacker would need to get a fake-work chain into a reviewed release. The anchor must be disabled whenever the chain containing it lacks the most proven work (`nChainWorkRaw`).
- **Consensus change:** none (local policy). **Effort:** small, about 3–7 days with functional tests for reorgs across the anchor and restart persistence. Also publish the anchor's evidence: the Full verdict log of the GPU verifiers.

### (e) Cheaper proof formats (new proof version)

Approaches:

- **Committed activations.** Merkle-commit hidden states at chosen layers and positions. A verifier checks one random segment from its committed input, costing roughly one layer × a few tokens, and bisects on dispute (refereed delegation, as in optimistic ML).
- **Locality-sensitive hashes.** Commit hashes of the final hidden states (TOPLOC-style); verification is prefill only.
- **zk proofs of inference.** Not practical today for 0.6B × 256 tokens on commodity CPUs. **[Assumption, based on published zkML costs]**

Constraints:

- BF16 llama.cpp miners and an fp32 verifier never agree bitwise, so segment checks still need tolerances and calibration.
- Every approach needs a new proof version, miner changes, a hard fork, and fresh adversarial review.
- **Effort:** months, with research risk. It is the only path that makes CPU sync of long history fully trustless at low cost, so it belongs on the mainnet roadmap, not the testnet.

### Comparison

| Option | Trust added | Cost per block (3 cores) | Consensus change | Effort | Ready for testnet? |
| --- | --- | --- | --- | --- | --- |
| a. Same verdicts on CPU | None | 90–480 s | None | Weeks plus GPU rental | After corpus evidence |
| b1. Block sampling | Probabilistic | p × 90–480 s | Yes (fork choice) | Large | No |
| b2. View spot check (advisory) | n/a (advisory) | 2–14 s | None | 1–2 weeks | As an add-on |
| c. k-of-n attestations | k attesters, useful work only | about 0 | None (owner policy) | 2–4 weeks | Yes |
| d. Assume-full-valid | Release reviewers, history only | 0 for history | None | Days | Yes |
| e. New proof format | None | Small | Hard fork | Months | No |

## 4. Recommendation

Adopt **a hybrid of (a), (c) and (d)**:

- **History:** `-assumefullvalid` anchors (d), published with the verifier evidence for every anchored block.
- **Tip:** local CPU Full with the existing verifier (a), once the corpus shows the same verdicts as the GPU verifier. Target hardware is 8+ cores and 24–32 GB RAM.
- **Fallback:** k-of-n signed attestations (c) for nodes that cannot keep up, during catch-up, and to cross-check local verdicts. Any disagreement raises an alert and never silently flips state.
- **Add-on:** the b2 spot check as advisory evidence for attested nodes.
- **Later:** keep (e) as mainnet research. Do not pursue b1 under v4 rules.

## 5. Next engineering steps

1. **Cross-hardware verdict-agreement corpus (highest value, the missing evidence).**
   - Done for the 102-block A5000 run and the two GPU RED envelopes (section 2a). Still to do: later testnet blocks, and replaying the two RED envelopes on GPU with several seeds.
   - Add a negative set:
     - forged logits at graded perturbations (0.05–50);
     - outputs from a substituted model, such as a quantized Qwen3-0.6B or a different Qwen size;
     - context mutations.
   - On a rented GPU, do the reverse: replay the CPU-mined proofs from `node-work-cpu.json`.
2. **Deterministic verifier draws.** Partly done: `realwork/verify.py` now derives `replaySeed` as an HMAC of the envelope under the verifier key (`replay_seed`), so one verifier always draws the same for one envelope. Still to do: pin thread counts. Include the seed policy in the verdict cache key and the attestation payload. Then re-run the position-233 abstention case to see whether it becomes reproducible.
3. **Node: `-assumefullvalid` plus an `attested` backend.** Use the signed verdict format above with test vectors. Add functional tests for reorgs across the anchor, restart persistence, an attester sending RED, and a timed-out attester. Updating the testnet `init()` guard is an owner decision.
4. **Thread-scaling and x86 profile.** Measure Full time on 4, 8 and 16 cores, on ARM64 and x86, for prompts of 27, 800 and 1,563 tokens. Decide whether the work issuer should cap prompt length to bound the worst-case CPU cost.
5. **CPU validator package.** A one-command compose file: node, CPU verifier on loopback ZMQ, pinned model snapshot and manifest check, 24 GiB memory limit. This needs no GPU and no outside verifier. Document the honest scope: shadow calibration, research receiver.
6. **Advisory baseline-only spot check (b2).** Measure detection of graded forgeries with one view, against the six-view Full.
7. **Detector coverage (now the blocker for CPU validation).** The corpus reached the 128-slot limit on 28 of 102 real proofs on CPU, never on GPU. Get a reviewed detector bundle with a wider token contract, or bring the CPU replay views within 128 candidates, then rerun the corpus.

## 6. Evidence that would show this works

- **Verdict agreement.**
  - At least 100 real GPU-mined blocks: CPU verdicts match GPU verdicts 100%, with no abstentions. **[Assumption: 100 is a minimum; the sample size should come from the target miss rate.]**
  - CPU maximum logit severity has a clear margin below the 12σ cap. For reference, the fp32 median is 1.78σ with a maximum of 3.82σ, measured on CPU replays of CPU-produced proofs (`replay-precision.json`).
  - Detector decisions match at every eligible position.
- **Negatives.** Every forgery class above RED on both devices, at the smallest perturbation the GPU verifier also rejects.
- **Determinism.** The same envelope and seed give the same verdict, and the same severities to a stated tolerance, across 10 runs and 2 machines.
- **Throughput.** On the target CPU spec, the 95th-percentile Full time stays under about 300 s on the real prompt-length distribution: under 50% load, so a backlog clears. A live CPU node follows the tip for 7 days with lag under 2 blocks.
- **Sync.** A fresh CPU node with an anchor syncs the development chain, verifies the post-anchor blocks locally and reaches the same tip and UTXO set hash as the GPU-backed node (`gettxoutsetinfo`).
- **Attestation.** Functional tests prove:
  - k−1 signatures never promote a block;
  - one RED blocks promotion and raises an alert;
  - an unknown key or a wrong rule digest is refused;
  - a restart preserves the state.
- **Independence.** At least two outside operators run CPU validators on their own hardware and report matching tips. Containers on one host do not count, per `NETWORK-PLAN.md`.

## Sources read

- `protocol/gpu/replay.py`, `gpu/live.py`
- `protocol/noet/verifier_entry.py`, `noet/verifier_transport.py`
- `protocol/cpu/{verify_current,verify_full,replay,replay_precision,detector_*}.py`
- `protocol/realwork/verify.py`, `protocol/testnet/prepare.py`
- `protocol/{VERIFIER-RUNTIME,RELEASE-STATUS,VALIDATION}.md`, `cpu/README.md`, `realwork/README.md`
- `protocol/validation/{cpu-verifier,cpu-six-views,cpu-v4,node-work-cpu,detector-diagnostics,replay-precision,gpu-acceptance,private-realwork}.json`
- Upstream `.upstream/services/verification-api/src/proof_verifier.py` (selected sections), `.upstream/docs/{proof-v4,detector-rule}.md`, `.upstream/documentation/verification/{full,quick}-verification.md`
- Upstream `.upstream/services/core-node/bcore/src/{chain.h,validation.h,validation.cpp,validationapi.{h,cpp},init.cpp,verification/quick_verifier.h,verification/credit_v4.h}`

No live hosts were contacted. The only experiment was the local forward-pass benchmark above.
