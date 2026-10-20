# NOET delivery and release status

Acceptance record up to 12 October 2026, before launch. Current status: [MAINNET.md](MAINNET.md).

## Public mining path and testnet (12 October 2026)

- **Public mining gateway is live** on the development network at `https://noet-chain.fly.dev:9443` (`noet/gateway.py`).
  - Anyone may lease work that pays their own address. The reward script comes from that address and is bound into the node's template.
  - Submitting needs the lease's secret, and limits apply per client and globally. Only mining RPCs reach the node, and the node's QuickVerify, including the proof-of-work target check, screens proofs before the GPU verifier.
  - Not yet proven: a block mined through the gateway by an outside address. This is pending the GPU host switch.
- **Standalone miner** (`miner/`): Docker image or native Ubuntu setup, with the client in `noet_miner.py`.
  - The model stage rebuilds the pinned GGUF from the pinned Hugging Face revision byte for byte (verified on arm64). No weights are redistributed.
  - The CUDA stages are written but not yet compiled.
  - Only compute capability 8.6 has been validated against the verifier.
- **`noet-testnet-1`** (`testnet/README.md`), genesis `2da97e8d…cdaa`, with the proposed economics:
  - Real-node checks pass:
    - Block 1 pays exactly 453 allocation outputs (working draft 1).
    - The subsidy follows the schedule, and supply is conserved.
    - Two of three keyholders can spend the ecosystem grant.
    - Vesting time locks are enforced.
    - A block 1 without the allocation outputs is rejected.
  - The release build refuses mock validation.
- **Testnet hosting and website:** `deploy/launch-testnet.sh` (with its own secret set) and the website switch `VITE_NOET_NETWORK=test` are ready. **Not yet launched.** The allocation signers are testnet-only keys, and the economics await final figures from the owner.

## Hosted development network (12 October 2026)

The private-work chain (`noet-private-work-v1`, genesis `301587d5…`) now runs persistently on one Fly.io machine with its indexer, explorer API and test-coin faucet; www.noet.network reads it through the existing read-only gateway. The node accepts blocks only after an authenticated Full verdict from a real verifier, which runs on a separately rented GPU host connected over three mutual-TLS links. While no GPU host is attached the network stays online and produces no blocks. Topology, secrets and costs: `deploy/HOSTED-NETWORK.md`.

This is one operator's development network: no independent peers, test coins only, unapproved prompt calibration (shadow) and inherited laboratory economics. It is not a public mining launch.

Products on the live network: a self-custody web and desktop wallet that signs locally (see `../wallets/DELIVERY.md`), a rate-limited test-coin faucet, and an explorer with per-block proof and verifier verdicts, pending transactions, top holders, block-interval and transaction charts and a public `/healthz/chain` endpoint for uptime monitors.

GPU replay precision: the verifier now computes bf16/fp16 replays with an fp32 reference forward pass, as the CPU path always did. On 38 recorded proofs the median maximum logit severity falls from 4.1 to 1.78 sigma with every threshold unchanged (`validation/replay-precision.json`). On an RTX A5000 the change was then confirmed: 102 blocks accepted with zero rejected proofs (405 Full GREEN, no RED or AMBER, all fp32 replays), a mature reward spent and confirmed, wallet acceptance passed, and a fresh peer independently verified all 102 blocks and matched the explorer. The earlier unexplained 102-block peer failure was an out-of-range harness bound (argparse exit 2). Twenty-six early requests abstained while the model directory had the wrong owner; the evidence audit records them. See `validation/gpu-acceptance.json` (`fp32ReplayAcceptance`).

## Implemented and verified

The prior combined acceptance and subsequent focused wallet and miner checks completed successfully: 219 protocol tests, 114 website tests with MySQL, 20 wallet module tests, ten economics tests, TypeScript, production build and native recovery. The latest protocol suite passes 258 tests; actual native and GPU research results are described below. Earlier packaged startup and CPU detector checks remain recorded; unchanged website, wallet and native work-issuance results refer to their earlier acceptance runs.

| Area | Evidence |
| --- | --- |
| Hosted website and app API | Primary-domain health check reports reachable API and MySQL; unauthenticated studio access returns 401; public provider catalog contains one provider and no API-key field |
| Application persistence | All 114 website tests pass with a disposable MySQL database and all four migrations, including concurrency, ownership, worker authorization, quotas and key revocation; no database tests skipped |
| Native laboratory chain | Two isolated nodes converge after competing branches; forced-process crash recovery, full reindex and index rollback pass at 105 blocks |
| Native wallet CLI | Input/recipient/fee review before unlocking, restricted signatures, separate broadcast, private bounded files, and relocking on failure |
| Native extension bridge | Separate desktop interface and Chrome bridge with balances, receive, history, preparation, review, sign-only, explicitly approved send and encrypted backups. Passphrases stay in the native approval process. Both development genesis identities are pinned. Six actual approval-window and six desktop-window checks pass under Linux/Xvfb; those UI checks use synthetic responses. The real-chain wallet harness returned exit 0, but its detailed report was lost. Installed Chrome and macOS interaction remain unverified. |
| Native host distribution | Deterministic, allowlisted host ZIP with per-file hashes; extracted host runs independently of the checkout and rejects unauthorized origins. Runtime bundling, signing and installed-browser acceptance remain open. |
| Native wallet recovery | Encrypted backup restores spend authority; a restored wallet signs a mempool-valid transaction without broadcasting; wrong passphrase and corrupted backup are rejected |
| Native Qt development build | Prior Linux ARM64 Qt tests and memory checks passed; see VALIDATION.md for exact scope |
| Miner source | Explicit synthetic-test opt-in, no remote/unverified VDF fallback, local prover requirement, serialized block/proof updates and exact registered model selection with full immutable revisions and digests |
| CPU inference evidence | Pinned Qwen3-0.6B weights downloaded and verified; two isolated CPU runs emitted identical 256-token legacy-v2 evidence fields. Independent Transformers replay measured 17,920 logits. A fresh record with a valid VDF and synthetic header passes actual Quick and Full under both original and compact replay. Falsified logits return RED after model loading. Six changed-context/VDF cases are rejected. This remains legacy-only research, without current-rule or native block acceptance. Scripts and candidate hashes are in `cpu/`. |
| Complete CPU verifier image | Actual compiled parser, VDF and credit/sunset arithmetic plus full model dependencies build successfully. 132 malformed envelopes and 202 invalid VDFs are rejected. The original 6 GiB VM was insufficient; successful replay used a 12 GiB container in a temporary 16 GiB VM. Source, image, dependency and test identities are recorded in `validation/cpu-verifier.json`. |
| Version-4 CPU research | The six-view gap is fixed in both CPU replay implementations. One real v4 record now passes Quick and Full with the required sealed detector: six independently computed views, 105 eligible positions scored, no violation. The second record remains Quick_Fail / Full RED for candidate concentration. Four context rejection/abstention checks pass. This is synthetic-context acceptance of one proof, not calibrated model coverage or native block acceptance; see `validation/cpu-six-views.json`. The initial incomplete run remains in `validation/cpu-v4.json`. |
| Required detector runtime | The CPU image selects the sealed detector bundle, verifies its hashes, cold-loads its weights and requires worker startup readiness during build. Five altered-artifact cases are rejected before executing changed weights/code. Detector startup readiness alone does not establish supported replay or model coverage. |
| Native VDF cryptography | Pinned native source generated a 10,000-iteration proof, accepted it, and rejected 204 invalid cases under address/undefined-behavior sanitizers on macOS ARM64. This validates the VDF component, not AI inference proofs. |
| VDF input boundary | Native challenge/proof lengths and uint64 iterations checked before cryptography; malformed hex, oversized input, native exceptions and non-boolean success values fail closed. Eight focused checks pass; these do not substitute for real proof vectors. |
| Private node RPC | Direct connections without system/environment proxies, private bounded regular cookie files, cookie rotation and response identity checks |
| Source and image provenance | Two identical source exports, successful Linux ARM64 node build with pinned Ubuntu base, 138-package image inventory and four executable digests; rebuilt image passed signing and recovery acceptance |
| Guarded miner runtime | Actual supervisor tests, fixed local model/tokenizer, no remote-code loading or saved model override, distinct credentials, bounded control requests and child shutdown checks; five real Linux mount checks pass. Prepared image context is deterministic; no GPU parent image or inference acceptance is claimed. |
| Verifier service boundary | Remote verdict delegation disabled; private IPv4 endpoints, socket/envelope limits and exact request-hash length enforced. Shared admission caps queued/active envelopes at 16 requests and 128 MiB, including mirrored checks and retries. Total process/GPU memory limits remain separate. |
| Verdict authentication | Guarded verifier requires a 32-byte hex secret. Actual pinned Python broker matches shared node vectors; ten native-node verdict cases pass 732 assertions; altered identity, payload, type and ruleset change the signature. The separate mutual-TLS relay supplies peer authentication and confidentiality; integrated multi-host deployment remains unverified. |
| Private TLS relay | Eighteen real TLS cases pass on macOS and Linux Python 3.12. Real ZeroMQ multipart transfer and reconnect pass on macOS. Both peers require CA validation and exact certificate pins; plaintext verifier endpoints must remain on loopback. A shared four-connection limit applies before TLS handshakes. Four deployment templates and a credential-free CPU image context are prepared. |
| Guarded verifier preparation | The launcher also requires immediate VDF challenge binding and refuses disable/shadow configuration; four focused tests cover startup and early rejection. Eleven loader call sites use approved local artifacts; proof input and cached reuse are checked, IPFS fallback is disabled, synthetic startup is refused, and deterministic packaging passes. Fifteen focused tests do not constitute GPU proof acceptance. |
| Model artifacts | Deterministic candidate inventories and verification against an independently pinned digest; modified/missing/extra files and unsafe paths rejected; no model code executed |
| Explorer | Dedicated explorer workspace, deep links, block and transaction pagination, exact balances, native-wallet coinbase maturity, UTXOs, spend links, observed activity, exports and genesis-bound saved addresses; stale status expires locally even when refresh is paused. Atomic indexed reads and orphaned cursor rejection remain enforced. |
| Web/mobile companion | Watch-only public-address app for both pinned development networks and deterministic Chrome development ZIP v0.2.0. All 24 companion/native JavaScript checks pass; the mobile companion does not create keys or sign. |
| Allocation lock primitive | A real laboratory node accepts all three valid 2-of-3 signer pairs at the CLTV boundary and rejects eleven early-spend/signature/bypass cases. Reorg, restart and full reindex preserve the expected spend state. A 169-output monthly alternative passes four arithmetic/boundary tests but remains unapproved and unactivated. |
| Economics | Ten simulation and monthly-candidate tests pass. Unactivated C++ monetary arithmetic passes all 11,773,440 emission blocks, 937 cross-language vectors and memory/undefined/unsigned-overflow sanitizers. Actual consensus integration and enforceable vesting outputs remain open. |

The CPU engine now preserves optional node-issued job heights and rejects malformed values. Actual engine acceptance covered 22 malformed requests, 14 valid or absent heights, and two v4 windows across slot reuse (534 generated tokens). The shared GPU snapshot function passes three source-level tests for the same contract, but its image has not been rebuilt or run. Both observed CPU windows missed the priced target; no anti-parrot rejection or native block acceptance is claimed. See `validation/miner-height.json`.

## First native real-work block

The separate `noet-private-work-v1` profile now has an accepted native block at height 1. The node issued the work against its registered pinned Qwen3-0.6B model and difficulty, received the real proof, accepted authenticated Quick and Full GREEN responses and credited an immature 715-unit laboratory reward to the native wallet. Two preceding attempts produced no qualifying evidence. The run had zero peers and no published ports, and refused both mock and desktop verification modes. See `validation/private-realwork.json`.

This does not establish spendable rewards, production emissions or public mining. Height 1 is ruleset 2, with advisory detector scoring; prompt calibration remains explicitly unapproved/shadow. A separate A40 run also accepted heights 1, 2 and 3. Its height-3 verdict includes the required detector, 159 eligible positions and NO_VIOLATION. Duplicate submissions leave the chain and reward balances unchanged. The GPU model runs in BF16; this remains single-host research with unapproved prompt calibration.

## A40 mining and verification

The pinned CUDA engine and real full verifier built and ran on a temporary Runpod A40. A captured native proof passed Quick and Full with all six detector views; six altered-context, model, VDF and logit cases were rejected or correctly withheld a verdict. All seven Linux model-protection cases pass.

The default Flash Attention path emitted three observed proofs that failed candidate-support integrity. With Flash Attention disabled, the first three-block run passed. A longer run accepted 34 blocks and then rejected the height-35 proof: candidate logit severity reached 15.78 sigma against the unchanged cap of 12. A fresh follower separately replayed all 34 accepted blocks with its own Full verifier and reached the same tip. Both nodes ran on one physical host.

A separate progress run continued obtaining new work after rejected proofs. Its completion output reports height 102 and a verified mature-reward payment. At least five proofs had already returned RED during the observed portion of this run, so completion is not a clean mining acceptance. The subsequent native-wallet harness exited successfully; it includes the upgraded explorer comparison, explicit send, relocking and encrypted backup restoration. The second fresh-peer test, targeting all 102 blocks, exited with failure.

Evidence collection was incomplete. A download on the operator's machine stalled for over five hours; during it the SSH service timed out, and an existing-host-key bug prevented automatic restart. Direct SSH and the provider terminal could not recover access. The final detailed reports and full proof-capture archive were not downloaded before the temporary Pod was deleted. Earlier downloaded reports and the sequencer's completion output remain available. The 102-block and wallet observations must be repeated with durable evidence collection before release acceptance. No verdict threshold was relaxed. See `validation/gpu-acceptance.json` for the exact evidence boundary and cleanup record.

The test also exposed a plain-completion response bug: generated text was parsed as a chat message after the proof was saved. The patch keeps plain completions out of the chat parser. Nine regression cases linked against the actual C++ engine fail before the fix and pass afterward. The full protocol suite passes 258 tests. The current product checks pass 123 website tests and 24 wallet JavaScript tests, TypeScript, targeted ESLint and the production build. Thirteen database tests were skipped in this pass; the earlier disposable-MySQL acceptance above is historical.

## Remaining work, with dependencies

The latest mandatory-detector regression withheld a verdict for a previously passing proof because eligible position 233 was not scored. That failure is preserved in `validation/node-work-cpu.json`. Earlier successful replays remain valid observations, but dependable mandatory-detector acceptance is not established. The release needs reproducible feature coverage and adversarial acceptance before mining activation; the guard must continue to abstain on incomplete evidence.

Follow-up replays at seeds 0 through 6 all passed, with up to 128 distinct tokens at eligible positions. They do not reproduce or erase the earlier unseeded failure. A deliberately recombined fixture made from recorded views reached 142 tokens at position 233 and was refused by the sealed 128-slot feature contract. The actual backend and gate preserved the no-verdict outcome, now with the precise refusal reason. This demonstrates a coverage limit, not the proven cause of the lost original inputs. See `validation/detector-diagnostics.json`. Broadening the input contract needs a separately identified and validated model release; changing the width or dropping tokens is not an accepted deployment fix.

| Release item | What is still needed |
| --- | --- |
| Real-work private network | The separate private profile now accepts a real CPU-generated block and credits its immature reward. A40 replay and negative cases are recorded separately. The GPU progress run reached 102 blocks and reported a confirmed reward payment, with unresolved numerical rejections. Repeat maturity/wallet/peer acceptance with durable evidence, resolve numerical compatibility, approve model calibration and validate packaged guarded deployment. The temporary GPU was removed; continuous hosting is not provisioned. |
| Public mining | Real-work adversarial tests, externally operated peers, difficulty/reward acceptance, operational monitoring, incident ownership and independent review. Recovery tests still use mock validation and test coins. The progress run reached 102 blocks; fresh-peer replay passed only the earlier 34-block chain, and the 102-block peer check failed. These observations do not establish multi-peer security or production recovery. |
| Mainnet tokenomics | Approve the proposal, implement monetary and allocation rules in the production chain, test exact supply/vesting invariants and independently review them. The paper is not deployed monetary policy. |
| Consumer signing wallets | Native Windows/macOS packaging, mobile device-backed key storage/signing/recovery, consumer extension packaging and actual Chrome/native approval acceptance, cross-platform recovery and independent security review. These remain implementation work, not merely store submissions. |
| Platform acceptance | Signed/notarized installers, store developer accounts and real device testing. Chrome extension loading and physical mobile installation are not verified; the Mac is now unlocked and Chrome is running, but Chrome has not connected to the browser tools and desktop control was denied. |
| Cloud operations | Funded persistent nodes/GPU capacity, provider workload permission, backup service on a plan that supports it, domain setup for the dedicated app host and monitoring workflow authorization. |
| Model approval | Trusted source, weight/tokenizer/config hashes, license review, read-only deployment mount and tested runtime. The integrity utility does not supply an approved model or validate proof correctness. |

The native explorer is still disconnected publicly. The public mobile/web/Chrome companion cannot create keys or sign. A separate unpublished native desktop wallet and bridge can prepare, sign and send test-coin payments after native approval; they are not consumer signing releases. Homebrew Python 3.14.8 now has Tk 9.1 installed. The approval window passed headless Linux tests, but desktop-control permission was denied for Mac interaction and installed Chrome acceptance remains open. Hosted API inference is functioning application infrastructure; it does not establish native useful-work mining.

## Native work issuance and calibration guards

Five v4 proofs generated from one recorded native work unit now pass actual CPU Quick and Full replay with all six detector views scored. Their prompts range from 27 to 1,563 tokens. The recorded node uses early-v4 ruleset 2, where detector scoring is advisory; the envelope is constructed offline with an unregistered research model and difficulty. These results do not establish live submission, native block acceptance or rewards. See `validation/node-work-cpu.json`.

The first long-prompt replay exhausted a 20 GiB container. CPU candidate forwards now project only the prediction positions while retaining the full attention context and baseline prompt scores. The actual model's retained logits were bitwise equal in the before/after comparison. All five proofs subsequently completed within the same memory cap. GPU behavior, detector thresholds and candidate batch sizes are unchanged.

Two disposable, network-isolated native nodes were tested with real validation selected and external verification enabled. They issued eight authentic work units across pre-v4 and active-v4 profiles. Header parent byte order, compact targets, heights, activation flags, distinct coinbase commitments, malformed request rejection and bounded tracker capacity passed. No verifier was connected, no blocks were accepted and no rewards were earned. See `validation/native-work.json`.

The guarded verifier now refuses missing, wildcard or model-wide calibration declarations, off/shadow mode, delayed activation and changed prompt thresholds. It also abstains before model allocation for a proof outside the declared model/precision scope. Eight actual image-startup rejection cases pass. This closes silent shadow fallback in the guarded deployment; it does not calibrate the research model. See `VERIFIER-RUNTIME.md` and `validation/calibration-startup.json`.

## Repeatable validation

Prepare the pinned source and have Docker running with the existing `noet-core:lab` image. Pull the pinned MySQL image documented in the website setup checklist. Then:

```sh
NOET_DOCKER_CONTEXT=your-context python3 protocol/validate.py --mysql --native
```

This runs protocol, economics and companion tests, TypeScript, all web tests, the production build and native recovery acceptance. The native test creates only its own disposable internal network and containers. The database runner creates only a random test database, never uses the production database URL, and removes its container and volumes. Neither provisions cloud resources or runs paid inference.

Read-only public deployment check:

```sh
cd web
npm run check:public
```

A reachable chain is not proof of production readiness. The public check reports mining readiness as false rather than converting HTTP success into a mining claim.

## Prepared but not executed on hosted runners

The inactive GitHub acceptance workflow template has pinned action commits, read-only repository permissions, no production credentials, and an optional native build/recovery/window test. YAML and action pins were checked locally; the workflow was not dispatched and no hosted-runner success is claimed. It is not installed: GitHub rejected the workflow path because the current OAuth login lacks workflow permission. The rest of the work can be pushed without expanding account access.

The source inventory is not a complete SBOM or a reproducible-binary proof. OS package snapshots, source-built transitive dependency inventory, signed releases and independent rebuild/review still remain.
