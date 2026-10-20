# NOET technical whitepaper

Useful proof of work. Built around AI.

Draft v0.2, 20 October 2026. This paper describes the protocol as it exists in this repository and runs on NOET Mainnet, and separates it from design work that has not shipped. Statements marked **Unverified** could not be confirmed from the repository for this draft.

## 1. Abstract

NOET is a blockchain in which blocks are earned by running a pinned AI model and proving that the run happened as declared. We call this useful proof of work (UPoW): instead of hashing arbitrary data, a miner generates text with a registered language model, binds a transcript of that inference to the current chain tip, and submits it as the proof behind a block. A separate verifier replays the inference against the same model weights and either accepts the block (GREEN), rejects it (RED or AMBER) or withholds a verdict when its evidence is incomplete.

The implementation integrates the open-source TensorCash v1.2.2 stack (a Bitcoin-Core-derived node, a proof-capable llama.cpp engine and a PyTorch verifier). NOET adds its own networks, guarded miner and verifier deployments, an encrypted node-to-verifier relay, a public mining gateway and an emission schedule with allocations enforced in block 1.

NOET Mainnet (`noet-mainnet-1`) has run since 15 October 2026. Its node accepts a block only after an authenticated Full verdict from a GPU verifier, and one operator currently runs both. Every mining attempt answers a question: a request someone sent to the network or, when none is waiting, the next item of an open dataset. The answer is the inference behind the proof, so the "useful" part of the work is a correctly executed and checkable answer. Connecting paid user jobs to block production is planned design work.

## 2. Motivation

### 2.1 Why useful work

Bitcoin-style proof of work secures a ledger by making block production expensive, and the hashing has no other use. NOET asks whether that spending can go into model inference that has value outside the chain, while still producing something a network can check.

Two properties have to hold. The work must be expensive to fake, so a miner cannot claim a block without running the model. It must also be checkable by someone else, without trusting the miner's word about which model ran or what it produced. Miners compute. Validators check.

### 2.2 What "useful" means today

- **Today:** a miner leases a work unit from the node together with a task: a public request waiting in the queue (sent through the website or the gateway) or, when none is waiting, the next item of the open dataset *NOET open explanations*, plain-language explanations of common ideas published under CC0 with their prompts. The miner runs the pinned Qwen3-0.6B model on the task prompt, and the answer it generates is the inference behind its proof. Each prompt carries its task ID, which matters because consensus rejects reused prompts. Prompts and answers are public, and an answer counts as checked only when the attempt that produced it became a block the verifier accepted (`protocol/noet/tasks.py`, `protocol/noet/open_dataset.py`).
- **Goal:** a paid inference job from a real user is also the work behind a block. The economics proposal says this needs a proof that securely binds job, model, input, result and chain challenge, and that it "is not yet demonstrated" (`protocol/economics/README.md`). The goal also raises an open privacy question: what a proof reveals about a customer's prompt and how long it is kept (`protocol/NETWORK-PLAN.md`).

### 2.3 What verification does not mean

Verifying inference shows that the declared model produced the declared tokens within tolerance. It says nothing about whether the answer is correct. GPU inference is also not bit-for-bit reproducible across hardware and kernels, so verification is a statistical replay check with calibrated tolerances, not a byte comparison and not a zero-knowledge proof (`protocol/.upstream/Readme.md`, pinned upstream checkout).

## 3. System overview

### 3.1 Components

| Component | What it is | Where it comes from |
|---|---|---|
| Node | bcore, a Bitcoin-Core-derived UTXO node with proof-of-inference consensus, VDF timing and a model registry. NOET patches network identity, genesis, address prefixes, ports and (for the testnet and mainnet) economics. | `services/core-node/bcore` pinned in `protocol/upstream.lock.json`; `protocol/patches/noet-lab.patch`; `protocol/testnet/prepare.py` |
| Miner | The pinned TensorCash llama.cpp fork, patched by NOET, running `Qwen/Qwen3-0.6B` at revision `c1899de2...47ca` as a BF16 GGUF file, plus a local chiavdf VDF prover. | `protocol/patches/noet-engine.patch`, `protocol/patches/noet-miner.patch`, `protocol/miner/README.md` |
| Verifier | The upstream PyTorch proof verifier, run in a guarded NOET deployment that accepts one approved model, refuses synthetic and remote-delegation modes and replays in fp32. | `protocol/VERIFIER-RUNTIME.md`, `protocol/gpu/README.md` |
| Relay | Mutual TLS 1.3 tunnels that carry the node's ZeroMQ verification traffic to the GPU host and verdicts back. | `protocol/deploy/transport/README.md` |
| Gateway | A small public HTTPS service that lets anyone lease work paying their own address and submit proofs, without exposing node RPC. | `protocol/noet/gateway.py` |

### 3.2 Hosted development topology

```text
   Outside miners (HTTPS)                    Fly.io: one persistent machine
  +---------------------+        +------------------------------------------------+
  | noet_miner.py       |------->| mining gateway :9443                           |
  | llama.cpp + VDF     |  lease | (4 calls, rate limits, method allowlist)       |
  +---------------------+ submit |        |  loopback RPC                         |
                                 |        v                                        |
                                 | bcore node  (-validationapi=real)               |
                                 |   QuickVerify, then waits for a Full verdict    |
                                 |   indexer + explorer API                       |
                                 |   relay node-request  ---------------------+   |
                                 |   relay node-verdict  <-------------+      |   |
                                 |   relay node-rpc      <------+      |      |   |
                                 +------------------------------|------|------|---+
                                         mutual TLS 1.3, pinned |      |      |
                                 +------------------------------|------|------|---+
                                 | Rented GPU host              |      |      v   |
                                 |   operator's miner ----------+      |  verifier |
                                 |   (whitelisted RPC)                 +-----+     |
                                 |                         HMAC-signed verdicts    |
                                 +------------------------------------------------+
```

Every plaintext socket stays on loopback. The three links between the node host and the GPU host use TLS 1.3 with a private CA, exact peer certificate pins and DNS server names. Verdicts also carry an HMAC under a shared 32-byte secret. The operator's own miner on the GPU host reaches the node through an RPC user restricted to work leases, proof submission and verdict reads (`protocol/deploy/HOSTED-NETWORK.md`). When no GPU host is attached, the node and explorer stay online and simply produce no blocks.

### 3.3 How a block is produced and accepted

```text
 miner                  gateway                 node (bcore)                 verifier (GPU)
   |  POST /mining/work   |                          |                              |
   |--------------------->| validateaddress(addr)    |                              |
   |                      |------------------------->|                              |
   |                      | create_mining_work_unit  |  builds template whose      |
   |                      |------------------------->|  coinbase pays addr's script |
   |  job + lease secret  |<-------------------------|                              |
   |<---------------------|                          |                              |
   | run VDF on parent hash, run model, search for a proof meeting the target       |
   |  POST /mining/submit |                          |                              |
   |--------------------->| submit_mining_response   |                              |
   |                      |------------------------->| QuickVerify: parse, context, |
   |                      |                          | VDF, header target           |
   |                      |                          |---- request over TLS ------->|
   |                      |                          |               replay in fp32, |
   |                      |                          |               detector, gates |
   |                      |                          |<--- signed Full verdict -----|
   |                      |                          | GREEN: connect block          |
   |  GET /mining/blocks/<hash> (poll quick/full verdict, confirmations)            |
```

Step by step, as implemented in `protocol/noet/gateway.py`, `protocol/miner/noet_miner.py` and `protocol/realwork/generate.py`:

1. **Lease.** The miner sends its address. The gateway has the node validate it and create a work unit whose coinbase pays that address. The node returns a request ID, a 76-byte header prefix, a target, a height and an expiry. The gateway returns a random lease secret and keeps only its hash.
2. **Check.** The miner pins each network's genesis hash and the single registered model (`Qwen/Qwen3-0.6B@c1899de...`, difficulty normalizer 1,000,000) and refuses to work if the gateway reports anything else.
3. **Compute.** The miner computes a chiavdf proof over the parent hash in the header prefix (1024-bit discriminant, 10,000 iterations), then runs the model in 516-token windows. A window may yield zero or more proofs that meet the target. A typical attempt takes a few minutes (`protocol/miner/README.md`).
4. **Submit.** Each proof (at most 2 MiB) goes in with the lease secret. The node's QuickVerify, including the proof-of-work target check, rejects junk before it costs GPU time.
5. **Full verification.** The node sends the proof over the relay. The verifier replays the inference and returns a signed verdict, and the node connects the block only on an authenticated Full GREEN.
6. **Maturity.** The reward matures after 100 blocks. The miner never retries a rejected proof with a different seed or target.

### 3.4 The public mining gateway

The gateway lets people outside the operator mine without any access to node RPC (`protocol/noet/gateway.py`):

- Only eight read, lease and submit methods on the node are reachable.
- Each client gets 360 leases and 120 submissions per hour and at most 8 open leases. IPv6 clients are grouped by /64. The global cap is 512 open leases. A lease accepts at most 8 candidate proofs and expires within 30 minutes.
- POST bodies must declare `application/json`. That forces a CORS preflight the gateway never answers, so other websites cannot make visitors' browsers lease work.
- At most four large submissions are held in memory at once, and bodies must arrive within 30 seconds.

The mainnet gateway runs at `https://noet-mainnet.fly.dev:9443`. The limits above are those in the code; the running gateway reports its own at `/mining/info`.

## 4. Proof and verification

This section stays at the level the repository documents. Several mechanisms are inherited from upstream TensorCash and are described in the pinned upstream documents rather than in NOET's own files; those are marked.

### 4.1 What is bound into a proof

```text
 node-issued context                       miner-produced evidence
 +-------------------------------+         +-----------------------------------+
 | header prefix (76 bytes):     |         | VDF proof (200 bytes) over the    |
 |   version                     |    +--->|   parent hash, 10,000 iterations  |
 |   parent hash   [bytes 4..36] |----+    | prompt and generated tokens       |
 |   merkle root (commits the    |         | per-step sampling values and      |
 |     coinbase / reward script) |         |   reported logits                 |
 |   time, compact target        |         | commitment fields, step nonces    |
 | height, ruleset (from chain)  |         | model name@revision, precision    |
 | registered model + difficulty |         +-----------------------------------+
 +-------------------------------+
```

- **Chain position.** The proof carries the 76-byte header prefix the node issued. Its parent hash must match the prefix and, for node-issued requests, the trusted parent the node supplies with the verification request. The guarded verifier enforces this challenge binding immediately for every proof mode and refuses to start if it is disabled or delayed (`protocol/VERIFIER-RUNTIME.md`, "Mandatory challenge binding").
- **Payout.** The header's merkle root commits to the coinbase, so changing the reward address changes the header and invalidates the proof. The node issued distinct coinbase commitments per work unit in the native work-issuance test (`protocol/validation/native-work.json`).
- **Time.** The VDF is a sequential computation over the parent hash. The node rejects malformed or mismatched VDFs; a native run accepted a real 10,000-iteration proof and rejected all 204 mutated cases (`protocol/validation/native-vdf.json`). The miner must compute the VDF locally: remote VDF fallback and unverified proof mode are disabled in the miner patch (`protocol/README.md`, "Miner proof-source policy").
- **Model.** The registered model name and immutable revision are part of the work. The guarded miner launcher and the verifier check the complete model file inventory against an independently approved manifest digest before loading (`protocol/MODEL-ARTIFACTS.md`, `protocol/MINER-RUNTIME.md`). The standalone miner rebuilds its GGUF from the pinned Hugging Face revision and fails on any byte difference from SHA-256 `8fa11f4d...0700`.
- **Height and ruleset.** The node derives height from chain state and tells the verifier which ruleset applies. The verifier does not infer height from the proof or the clock; missing context produces no verdict (`protocol/.upstream/docs/proof-v4.md`).
- **Sampling and nonces (upstream).** In proof version 4 each step's nonce is `SHA256(TAG_STEP_V4 || C || R || A || S4)`, where A is an Argon2id output and S4 commits to a single fixed Gumbel sampling race. Replay checks the actual race winner (`protocol/.upstream/docs/proof-v4.md`). NOET did not modify these rules.
- **Reuse.** From the history-enforcement height, consensus rejects v4 proofs that reuse a prompt or a protected 64-token continuation slice, checked against branch-aware history (`protocol/.upstream/docs/proof-v4.md`). On `noet-testnet-1`, history collection starts at height 1 and enforcement at height 2 (`protocol/testnet/prepare.py`). Separately, a duplicate submission of the same block leaves height and balances unchanged (`protocol/validation/private-realwork.json`).

### 4.2 The proof-of-work target

A block still has a header hash compared against a target, as in Bitcoin. The testnet genesis nonce, for example, is the first one whose double-SHA-256 header hash meets compact target `0x207fffff` (`protocol/testnet/prepare.py`, `protocol/tests/test_testnet_economics.py`). Upstream adds "evidence pricing" as a separate header-target check of the form `H <= floor(T0 * Q / P)`, where the factor `P/Q` depends on the proof's sampling profile and structure, and applies a model-aware difficulty normalizer (`protocol/.upstream/docs/proof-v4.md`). In NOET's networks the registered normalizer is 1,000,000.

Mainnet retargets with ASERT from height 2: a 12-hour half-life toward 600-second blocks, starting from the minimum difficulty (`protocol/mainnet/README.md`, `protocol/testnet/asert_check.py`). **Unverified:** how this behaves with many independent miners. Before ASERT, the A5000 research harness produced about 59 blocks per hour on a single GPU (`protocol/validation/gpu-acceptance.json`).

### 4.3 Two-stage verification

**QuickVerify (node, C++).** Cheap checks that run on every submission before any GPU work: envelope parsing and size limits, context binding (proof parent, header parent, header length, outer request parent), VDF validity and the header target. In the CPU verifier experiments all six altered-context and altered-VDF cases failed Quick, and 132 malformed envelopes and 202 invalid VDFs were rejected (`protocol/VALIDATION.md`, "Complete CPU verifier acceptance").

**Full verification (verifier, PyTorch).** The verifier loads the approved weights from a read-only, hash-checked snapshot and replays the generated sequence. In outline:

- **Logit replay.** It compares the miner's reported logits with its own forward pass. Deviations are measured in sigma units derived from the declared precision's ULP spacing, with a hard cap of 12 sigma (`protocol/validation/replay-precision.json`).
- **fp32 reference.** As of 12 October 2026 the GPU path computes the reference forward pass in fp32 for bf16 and fp16 proofs, as the CPU path always did. On 38 recorded proofs the median per-proof maximum severity fell from 4.1 to 1.78 sigma with every threshold unchanged (`protocol/RELEASE-STATUS.md`).
- **Candidate checks.** Candidate-support integrity checks look at the set of tokens the sampler could have chosen. An upstream "candidate band" rule rejects a 256-row window when at least 32 rows hold a mid-probability candidate and a few token identities account for at least 90% of the variance (`protocol/.upstream/docs/proof-v4.md`).
- **Detector.** A sealed detector bundle scores six independently computed views of the replay at each eligible position. Steps that exceed both published scores are counted, and counts by position band produce AMBER or RED (`protocol/.upstream/docs/detector-rule.md`). Under ruleset 2 the detector is advisory; it becomes mandatory under ruleset 4, which `noet-testnet-1` reaches at height 3.
- **Prompt gate.** A prompt check ("gibberish" gate) is fixed by the guarded launcher at a 14.0-bit threshold with 32 minimum transitions. It requires an operator to declare the exact calibrated model, revision and precision (`protocol/VERIFIER-RUNTIME.md`).

The verifier returns GREEN, AMBER or RED, or raises an abstention with no verdict when its own evidence is incomplete: missing height or ruleset, an undeclared model or precision, unscored detector positions, or infrastructure failure. Verdicts are HMAC-signed; altering the request identity, payload, type or ruleset changes the signature (`protocol/VALIDATION.md`, "Shared admission and required verdict signing"). The node connects a block only on an authenticated Full GREEN.

### 4.4 Determinism and replay

The design does not assume bit-exact reproduction across hardware. What the repository shows:

- Two CPU runs on the same machine produced identical tokens, sampling values, reported logits and commitments. An independent CPU replay of 17,920 logits differed from the producer by a mean of 0.020 and a maximum of 0.242, because the producer runs a BF16 GGUF with different kernels (`protocol/VALIDATION.md`, "Real CPU inference evidence").
- Kernel choice matters. With Flash Attention enabled by default, three proofs failed candidate-support integrity; with it disabled, runs passed. The miner defaults to Flash Attention off (`protocol/gpu/README.md`).
- Only compute capability 8.6 GPUs (RTX A5000, A40) have been validated against the verifier. The miner build targets other architectures, but none has been checked against the verifier (`protocol/miner/README.md`, `protocol/RELEASE-STATUS.md`).

### 4.5 Observed failure modes

| Observation | Outcome | Source |
|---|---|---|
| bf16 reference replay on GPU | A 34-block run ended when a proof reached 15.78 sigma against the 12-sigma cap. The verifier's own rounding was the main cause, and the fp32 change followed. | `protocol/RELEASE-STATUS.md`, `protocol/gpu/README.md` |
| After the fp32 change, RTX A5000 | 102 blocks accepted, 405 Full GREEN, no RED or AMBER. A fresh peer with its own verifier replayed all 102 blocks and matched the explorer. Both nodes shared one host. | `protocol/validation/gpu-acceptance.json` |
| Model directory with wrong owner | 26 requests abstained (no verdict) until ownership was fixed. No false RED resulted. | same |
| Detector input width | The sealed detector has 128 token slots per position. A constructed 142-token case was refused and the verdict withheld. One earlier unseeded replay also withheld a verdict at position 233, and its cause is unresolved. | `protocol/RELEASE-STATUS.md`, `protocol/VERIFIER-RUNTIME.md` |

The guiding rule in all of these is that missing or doubtful evidence yields no verdict rather than a guessed one, and no threshold was relaxed to make a run pass.

## 5. Economics

These figures are final for NOET Mainnet, which launched with them (*Tokenomics, working draft 1*). The single source of truth is `protocol/economics/tokenomics.json`; the C++ policy and website values are generated from it, and a test fails if a copy is stale. Block 1 and the subsidy are fixed in consensus, so changing a figure would take a consensus upgrade (`protocol/economics/README.md`). Nothing here is a price forecast or a promise of returns.

The development network did not use these figures. It ran the inherited laboratory schedule (an initial 715-coin reward with epoch decay), which is unrelated to NOET's economics (`protocol/realwork/README.md`, `protocol/deploy/HOSTED-NETWORK.md`).

### 5.1 Supply and emission

| Parameter | Value |
|---|---|
| Maximum supply | 100,000,000 NOET (1 NOET = 100,000,000 atoms) |
| Mined | 80,000,000 NOET (80%) |
| Allocations in block 1 | 20,000,000 NOET (20%) |
| Nominal block interval | 600 seconds |
| Epoch | 52,560 blocks (about one year at the nominal interval) |
| Release per epoch | Mainnet: 12.5% of the remaining mining reserve. The testnet ran 15% |
| Year-one subsidy | Mainnet 190.25875190 NOET per block; testnet 228.31050228 |
| Coinbase maturity | 100 blocks |

The exact rule: with R remaining mining atoms, each epoch's budget is `B = min(R, max(1, floor(15 * R / 100)))`. Block i of the epoch (0 to 52,559) gets `floor((i+1) * B / 52560) - floor(i * B / 52560)`, so the whole integer budget is paid with at most one atom of difference between blocks. A final dust rule exhausts the reserve exactly, with no tail issuance above the cap. This produces a 15% yearly reduction in the block reward, not 15% inflation. The reward's effective half-life is about 4.27 model years.

The schedule counts blocks, not time. If blocks arrive faster than the 600-second nominal interval, issuance runs ahead in calendar terms. The schedule's C++ arithmetic has been checked against an independent Python implementation for all 11,773,440 emission blocks, plus 937 boundary vectors, with sanitizers (`protocol/validation/candidate-policy.json`).

| End of model year | Mined that year | Total minted incl. locked | Unlocked upper bound |
|---:|---:|---:|---:|
| 1 | 12.000m | 32.000m | 18.200m |
| 2 | 10.200m | 42.200m | 29.600m |
| 5 | 6.264m | 64.504m | 55.504m |
| 10 | 2.779m | 84.250m | 81.250m |
| 13 | 1.707m | 90.328m | 90.328m |
| 20 | 0.547m | 96.899m | 96.899m |

The proposal itself warns that a declining reward does not mean low early dilution: the unlocked upper bound goes from 5m at launch to 18.2m after one year and 29.6m after two, as mining and vesting overlap.

### 5.2 Allocations and vesting enforced in block 1

| Class | NOET | Release | Outputs |
|---|---:|---|---:|
| Team and founders | 8,000,000 | 2,000,000 at launch, then 150 monthly installments | 151 |
| Protocol treasury | 8,000,000 | 1,000,000 at launch, then 150 monthly installments | 151 |
| Ecosystem grants | 4,000,000 | 2,000,000 at launch, then 150 monthly installments | 151 |
| **Total** | **20,000,000** | 5,000,000 at launch; 100,000 a month for 150 months | **453** |

A "month" is 4,380 blocks. Each installment is a P2WSH output with the script

```text
<lock height> OP_CHECKLOCKTIMEVERIFY OP_DROP OP_2 <k1> <k2> <k3> OP_3 OP_CHECKMULTISIG
```

The lock height is one less than the first block at which the installment may be spent; for example, the first installments are locked to height 4,379 and become spendable in block 4,380; the last unlock at block 657,000, about 12.5 years after launch. The three launch grants have no time lock. Installment amounts are whole atoms split by cumulative floor, so each class total is exact. Keys are sorted per BIP67 (`protocol/testnet/allocations.py`, `protocol/economics/monthly_vesting.py`).

The node enforces these outputs, in order, in block 1's coinbase; a block 1 without them is rejected with `bad-cb-allocation`. Real-node checks on the testnet's test variant confirmed, for the earlier 169-output schedule, exact payment of every output, conservation of supply, a 2-of-3 spend of the untimed grant and refusal of the first installment one block early (`protocol/testnet/README.md`). A laboratory test also rejected eleven early-spend and bypass cases, with the result holding through reorg, restart and reindex (`protocol/validation/vesting-locks.json`).

The time locks only set the earliest spend height. They cannot cancel unvested team grants, and two signers can move a matured installment anywhere. Mainnet's signer keys were set before launch with `protocol/testnet/set_allocation_keys.py --network mainnet`.

### 5.3 Fees and reward policy

The proposal recommends that the full mining subsidy and ordinary transaction fees go to the block producer, with no treasury tax, no staking yield, no fee burning and no investor allocation. It also says invalid or replayed work must earn nothing, and that self-purchased jobs must never unlock extra subsidy (`protocol/economics/README.md`). Its miner cost scenarios use assumed prices as stress inputs. They are not forecasts and do not show that mining will be profitable.

## 6. Networks

| Network | Status | Identity | Economics | Validation |
|---|---|---|---|---|
| Laboratory | Local only, disposable | `nlab1` addresses | Inherited lab rules | Explicit mock validation; integration fixtures only |
| `noet-private-work-v1` (development) | Hosted 12 to 16 October 2026, then archived and shut down | genesis `301587d5...`, `nwrk1` addresses | Inherited lab rules (715 initial reward) | Real: node needs an authenticated Full GREEN from a GPU verifier |
| `noet-testnet-1` (public testnet) | Ran 13 to 16 October 2026 (1,102 blocks), then archived and shut down | genesis `2da97e8d...cdaa`, `tnoet1` addresses, P2P port 33441 | Section 5, 15% release rate | Release build refuses mock validation; v4 proofs from height 1 |
| `noet-mainnet-1` (mainnet) | Live since 15 October 2026 | genesis `fc3e7390...5a54`, `noet1` addresses, P2P port 33440 | Section 5, 12.5% release rate | As the testnet |

### 6.1 Mainnet

Block 1 of NOET Mainnet was mined on 15 October 2026 at 16:41 UTC and carries the allocation outputs described in section 5. Difficulty follows ASERT from height 2 toward 600-second blocks with a 12-hour half-life (`protocol/mainnet/README.md`). One operator runs the node on Fly.io, with the public gateway and the explorer, and one GPU verifier and miner on a rented GPU host; the node and the verifier talk over the mutual-TLS relay. There are no independent peers yet, and the verifier runs the prompt gate in shadow (logging) mode.

The release build refuses `-validationapi=mock` and `-useextapi=0` at startup. Activation is fixed: proof v4 from height 1, history enforcement and the v3 penalty from height 2, and the v3 sunset (mandatory detector) from height 3 (`protocol/testnet/prepare.py`, shared by the testnet and mainnet).

### 6.2 Earlier networks

The development network accepted its first hosted blocks on 12 October 2026 (`protocol/validation/gpu-acceptance.json`), and `noet-testnet-1` launched on 13 October 2026 with the same supervisor and its own Fly app, volume and secret set (`protocol/deploy/HOSTED-NETWORK.md`, `protocol/testnet/README.md`). Both were archived and shut down on 16 October 2026, after mainnet launched. Their coins had no value.

## 7. Security model and threat analysis

### 7.1 Trust assumptions today

- **One operator.** The mainnet node, its verifier, the gateway and the explorer are all run by one party. There are no independent peers. Anyone who can change that node or verifier can change which blocks are accepted.
- **GPU verifier dependency.** The node accepts nothing without a Full verdict. Whoever runs the verifier decides block acceptance, and if the verifier is offline the chain stops.
- **Rented hosts.** The GPU host is a rented machine. The guards check model files at load time, but a privileged host administrator can replace mounts or memory afterward (`protocol/MINER-RUNTIME.md`, `protocol/VERIFIER-RUNTIME.md`).
- **Gateway and payout.** A work unit contains a header prefix but not the coinbase, so a miner cannot confirm from the job alone that it pays the miner's own address. It checks only the gateway's stated reward address, so it trusts the gateway operator here. (This is our reading of `protocol/noet/gateway.py` and `protocol/miner/noet_miner.py`, not a documented finding.)
- **No independent audit.** No part of the consensus changes, verifier, wallet or economics has had independent security review. It remains open (`protocol/MAINNET.md`).

### 7.2 What an attacker can and cannot do

| Attempt | Expected result | Evidence level |
|---|---|---|
| Submit junk or malformed proofs | Rejected by QuickVerify before GPU replay; bounded by gateway rate limits and a 2 MiB size cap | Implemented; malformed-envelope tests pass |
| Claim a block without running the model (falsified logits) | Full RED after replay | Tested on CPU and GPU with altered logits |
| Reuse a proof on another parent or chain | Context and VDF challenge binding fail in Quick, or Full returns RED before model load | Tested with six context mutations |
| Replay a prompt or continuation | Consensus rejection after history enforcement height | Upstream rule; NOET relies on it, adversarial test coverage on NOET networks not shown |
| Resubmit an accepted block | No change to height or balances | Tested |
| Substitute a cheaper or different model | Miner, gateway info and verifier pin one model revision and digest; unapproved model abstains | Tested on GPU replay |
| Submit on or redirect another miner's work | Needs that lease's secret; changing the coinbase invalidates the proof (but see the gateway trust point above) | Implemented |
| Forge or tamper with verdicts in transit | Mutual TLS with pinned certificates, plus a verdict HMAC | Tested on loopback, in use on mainnet; no independent review |
| Exhaust the gateway or relay | Per-client, per-group and global limits; relay admits four connections before TLS | Implemented; no sustained load test recorded |
| Grind sampling or steer outputs to cut cost | Detector, candidate-band and candidate-support rules aim to catch this | Not established: model-wide detector coverage and prompt calibration are unmeasured |
| Rent a majority of mining capacity | Not analyzed. The economics proposal lists purchased or rented majority capacity as an audit item | Open |
| Compromise the operator or GPU host | Full control of acceptance on mainnet | Open; inherent to the current setup |

## 8. Known limitations and open problems

- **Centralization.** The current network is not decentralized (section 7.1). Independent nodes and verifiers are a precondition for any claim otherwise.
- **Validating nodes need a GPU verifier.** A node that wants to check blocks itself needs its own Full verifier, because the release build refuses mock and disabled external validation. In practice that means a GPU, the approved model files and the guarded verifier image. Outsiders cannot run a validating node with an ordinary server today.
- **The CPU verifier is research only.** The CPU replay path accepted one real native block at height 1 in an isolated research run, using ruleset 2 with an advisory detector, and it has passed several offline proofs (`protocol/validation/private-realwork.json`, `protocol/validation/node-work-cpu.json`). It is not attached to any running network. It needed a 12 GiB container for short proofs, and the longest recorded prompt took about eight minutes on three cores within a 20 GiB cap.
- **Detector coverage.** The mandatory detector withheld a verdict on a previously passing proof (position 233), with the cause unresolved. Its 128-slot input contract can be exceeded. Widening it requires a new reviewed model release.
- **Prompt calibration.** No prompt-gate calibration is approved for Qwen3-0.6B. The guarded service refuses to start without one; mainnet's verifier runs the gate in shadow mode.
- **Hardware reproducibility.** Validated only on compute capability 8.6 GPUs with Flash Attention off (section 4.4). The release record says the standalone miner image's CUDA stages are written but not yet compiled.
- **Difficulty.** Retargeting for many independent miners has not been demonstrated (section 4.2).
- **Build reproducibility.** Source exports are deterministic, but OS package repositories are mutable, there is no complete SBOM and binaries are not signed (`protocol/SOURCE-BUILDS.md`).
- **Evidence gaps.** An earlier A40 run lost its final reports when remote access failed, so its 102-block result is not counted as acceptance (`protocol/RELEASE-STATUS.md`).
- **Vesting semantics.** Time locks are not cancellation rights. Legal review is outstanding.
- **Useful work is narrow.** The model is small and paid user jobs do not produce blocks.
- **Licensing.** The pinned upstream's license files say Apache-2.0 while its README says MIT; this is an open clarification item (`protocol/README.md`).

## 9. Roadmap

No dates are promised. Each step depends on evidence from the previous one. Open items are tracked in `protocol/MAINNET.md`.

1. **Mainnet.** Launched 15 October 2026 (section 6.1).
2. **Resilience.** Standby verifiers the relay switches to when the main verifier is down, off-site backups of the node's data, and more validated GPU architectures.
3. **Independent operation.** Externally operated nodes, each with its own verifier. Difficulty retargeting with many miners, and partition, reorg and recovery tests with real proofs. Approved prompt calibration and measured detector coverage.
4. **Larger models.** Candidate profiles for Qwen3 8B, 14B and 32B (`protocol/models/`). A switch is a consensus upgrade at an announced height, made only after verifier tolerances are measured on the new model.
5. **User jobs as work.** Design and test a proof that binds a paid job to the chain challenge, a privacy policy for what proofs reveal, and a measured cost of verification against what customers pay.

## 10. References

Repository paths are relative to the repository root.

NOET protocol documents
- `protocol/MAINNET.md`: mainnet status and open work
- `protocol/mainnet/README.md`: `noet-mainnet-1` identity, difficulty and launch record
- `protocol/README.md`: components, miner proof-source policy, model selection, licenses
- `protocol/NETWORK-PLAN.md`: architecture, network stages and the release gates set before launch
- `protocol/RELEASE-STATUS.md`: acceptance record up to launch, fp32 replay result
- `protocol/VALIDATION.md`: development validation evidence and limits
- `protocol/VERIFIER-RUNTIME.md`: guarded verifier, calibration, VDF boundary, challenge binding, transport limits
- `protocol/MINER-RUNTIME.md`: guarded miner launcher and model protection
- `protocol/MODEL-ARTIFACTS.md`: model manifest verification
- `protocol/SOURCE-BUILDS.md`: source provenance and its limits
- `protocol/economics/README.md`: economics proposal and assumptions
- `protocol/economics/tokenomics.json`: source of truth for current economic figures
- `protocol/economics/monthly_vesting.py`: installment heights and amounts
- `protocol/testnet/README.md`, `protocol/testnet/prepare.py`, `protocol/testnet/allocations.py`: `noet-testnet-1` identity, activation, block-1 outputs
- `protocol/noet/tasks.py`, `protocol/noet/open_dataset.py`: the public request queue and the open dataset miners answer
- `protocol/models/README.md`: candidate larger models
- `protocol/realwork/README.md`, `protocol/realwork/generate.py`: private real-work profile and the research prompt template used before launch
- `protocol/miner/README.md`, `protocol/miner/noet_miner.py`: standalone miner
- `protocol/gpu/README.md`: GPU acceptance, Flash Attention and replay precision findings
- `protocol/deploy/HOSTED-NETWORK.md`: hosted network topology
- `protocol/deploy/transport/README.md`: mutual-TLS relay
- `protocol/noet/gateway.py`: public mining gateway
- `protocol/upstream.lock.json`: pinned TensorCash v1.2.2 sources

Evidence records (`protocol/validation/`)
- `gpu-acceptance.json` (including `fp32ReplayAcceptance`), `replay-precision.json`, `private-realwork.json`, `native-work.json`, `native-vdf.json`, `cpu-verifier.json`, `node-work-cpu.json`, `detector-diagnostics.json`, `vesting-locks.json`, `candidate-policy.json`, `verdict-auth.json`, `relay-admission.json`

Pinned upstream (fetched by `protocol/bootstrap.py` into `protocol/.upstream/`)
- `Readme.md`: TensorCash architecture and the statistical verification approach
- `docs/proof-v4.md`: proof v4 nonces, sampling race, evidence pricing, reuse protection
- `docs/detector-rule.md`: detector AMBER and RED bands
