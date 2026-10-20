# Bounded GPU acceptance

These scripts build and exercise the pinned inference engine, actual full verifier and separate private-work native node on a temporary Linux CUDA host. They do not launch public mining or approve a model. The research receiver keeps unapproved prompt calibration in shadow mode; the guarded service still refuses that deployment.

The first host is an A40 with CUDA 12.8 and PyTorch 2.8, using the recorded official image digest in `../validation/gpu-acceptance.json`. There is one ephemeral host, no public node or RPC port, no network volume and no production wallet. The owner authorized at most $10 total for this test and removal afterward. Stopping an SSH process does not stop billing: delete the Pod and verify its removal.

## Inputs and build

Export credential-free source with the existing checked exporters:

1. `cpu/prepare_source.py` produces `engine.tar` from the locked engine and helper sources. Its historical CPU name does not prevent a CUDA build.
2. `prepare_verifier_cpu.py` produces `verifier.tar`. Its Dockerfile is not used here; `build.sh` builds the same native extensions for the actual host. Record any wrapper overlay separately.
3. `source_bundle.py` produces `native.tar` from the pinned native source and laboratory patch.
4. `realwork/prepare.py` produces `private-work.tar`, the reviewed two-file network overlay. The build checks the baseline and replacement hashes before using it.

Place them under `/workspace/noet-gpu`, with independently recorded `inputs.sha256` and `native-inputs.sha256`. Copy the exact candidate snapshot, its manifest and the verified BF16 GGUF conversion separately. Do not copy `.env` files, API keys, account credentials or existing wallets.

`build.sh` compiles CUDA architecture 86, the model verifier extensions and FlatBuffers bindings. It uses a virtual environment with the official image's PyTorch packages. `build_native.sh` builds the private node, CLI and native wallet. Both retain dependency and binary records; these are research builds, not reproducible or signed release binaries. Mutable OS package repositories and Python transitive dependency resolution remain release limitations.

The scripts expect `checks/fix_object_bindings.py` from `cpu/`, `checks/exercise.py` from `cpu/`, `checks/generate.py` and `checks/verify.py` from `realwork/`, and the Python files in this directory under `checks/`. Install their checked versions before starting a run; preserve file digests when recording results.

The native GPU test also needs `cpu/verify_current.py` under `checks/` for bounded numeric detector captures. Every computed Full request retains its random seed, raw native envelope, six-view metadata and exact feature inputs. These observations do not replace the sealed scorer or its verdict.

`test_response.py --source /workspace/noet-gpu/engine/llama --build /workspace/noet-gpu/engine/build` links `response_test.cpp` against the actual engine libraries. Nine cases cover native/OpenAI text responses, partial text and continued chat parsing. The original engine incorrectly parsed plain completions as chat; the regression fails before the response-routing patch and passes afterward. This is independent of proof verification.

## Candidate models

`stage.py --profile qwen3-8b` (any entry in `../models/profiles.json`) stages a host that fetches, converts and tests a larger candidate on its own private network; start it with `model_session.sh` instead of `session.sh`. `plan_host.py` first checks GPU memory and disk and, with two GPUs, gives the verifier its own (`NOET_VERIFIER_GPU`, `NOET_PRODUCER_GPU`; both default to 0). Without `--profile`, staging and every script behave as before. See [../models/README.md](../models/README.md).

## Restricted execution

The model tree and every ancestor must be root-owned and unwritable by the worker. Make source and runtime directories readable and executable, the GGUF readable, and only fresh output/cache directories writable by UID 10001. `run.sh` drops all capabilities, supplementary groups and the ability to gain privileges. It selects explicit `NOET_MODEL_PROTECTION=root-owned` because this host cannot create a read-only bind mount. Snapshot identity and complete file hashes are still enforced. The separate seven-case Linux permission smoke test includes attempted writes, chmod and rename.

`run.sh` clears inherited environment variables and requires absolute file paths. It never needs a Runpod API key. Only the control plane needs account authentication.

Invoke it with `bash /workspace/noet-gpu/run.sh`. If helper files are uploaded after the build, restore worker read permission on those credential-free files before running. Account credentials and native wallet directories must never be part of `checks/`.

## Acceptance and diagnosis

- `replay.py` checks a captured native envelope against its recorded SHA-256. The positive case requires CUDA model placement, Quick success, Full GREEN and all six scored detector views. Altered parent, VDF and logits must receive RED; missing height/ruleset and an unapproved model must abstain. Each case saves its result even when acceptance fails.
- `native.py` gets a live work lease, generates evidence, submits it and waits for both native Full GREEN and canonical connection. It checks that a duplicate submission cannot change height or wallet balance. A generated proof receiving RED or AMBER ends the run as a failure. It never substitutes mock validation or lowers verification thresholds.
- `--blocks 3` tests the fixed private activation transition. `--blocks 101 --spend` additionally requires actual coinbase maturity, signs a transaction from the encrypted native wallet and mines its confirmation. Use `--check-indexer --restart` to compare explorer balances and preserve the confirmed payment across a clean node restart. These options are test capabilities, not evidence that they have passed.
- `--continue-after-rejection` is a separate progress test. A RED proof remains rejected and recorded, and the next attempt obtains a new native work lease. It never changes a verifier seed to retry the same rejected envelope. The run can finish mining and payment checks, but any rejected proof keeps its overall acceptance result failed. AMBER and other fatal errors still stop the run. The default remains stop on rejection.
- `--accelerator cpu` explicitly disables device and operation offload. `--flash-attention auto|on|off` allows controlled kernel comparisons. The native runner defaults to `off`: the default auto-enabled path failed, while explicit `off` passed all three private activation heights. A longer `off` run later failed at height 35 with a 15.78-sigma logit-severity violation against the unchanged 12-sigma cap; this setting is not a reliability fix. Record the selected implementation and its result, including failures.
- `compare_logits.py` measures unpadded captured evidence against independent BF16 and FP32 reference calculations. It has no pass threshold and cannot award a verdict or reward.
- `audit.py --run RUN_DIRECTORY --out NEW_REPORT --require-captures` examines every recorded Full result, including failed requests and exact-envelope retries. It checks consecutive accepted heights, mandatory detector coverage, envelope/capture hashes and duplicate submission accounting. A final success flag cannot hide an earlier failed or incomplete request. Ten focused audit cases cover tampered inputs, fabricated retries and interrupted runs.
- `summarize.py` retains every computed Full result and summarizes measured generation speed, verification time and memory. Rates include the sequential research harness and per-attempt startup; they are not production performance or miner-revenue estimates.
- `peer.py --source COMPLETED_RUN --out NEW_DIRECTORY --seconds 7200` starts the stopped source chain and a fresh follower on distinct loopback ports. Each has a separate real verifier. The check requires matching tips, independently computed Full evidence for every mined block, and explorer agreement. It shares one physical host and is not a test of independent operators or hostile networks. Do not overlap it with `native.py`, which uses the same seed-node ports. The downloaded first peer result passed 34 blocks. The later 102-block peer run returned exit 2; its detailed report was not recovered, so it is not accepted.

Keep all failed runs. Never replace an unfavorable replay seed with a favorable one and call that calibration. A working captured-proof replay is not equivalent to a working GPU miner, reliable mandatory detection, approved production economics or an externally operated network.

Fresh native runs retain their temporary laboratory wallet password in a mode-0600 file inside the private `node/` directory so an interrupted test can be recovered. This holds only synthetic private-chain funds. It is not a consumer wallet storage design. The evidence collector must exclude the entire native directory, including this password and wallet files; deleting the temporary host destroys them.

Before deleting the host, collect synthetic proof envelopes, reports, binary/input hashes and logs. Exclude native data directories, RPC cookies and wallets. Confirm no billable Pod or volume remains, and remove only the temporary client key created for this test.

## Recorded outcome and evidence collection failure

The progress run reported height 102 and a verified reward payment, while retaining an overall failed acceptance because generated proofs had returned RED. The wallet harness subsequently returned exit 0 and the 102-block peer harness returned exit 2. Final detailed reports and captures were not downloaded before remote access failed. See `../validation/gpu-acceptance.json`; do not promote terminal completion fields into a complete audit.

A browser-tool stall outlasted the SSH service timeout. Automatic restart then failed at an unconditional host-key creation command. Future startup must reuse an existing host key, results must be copied continuously into durable private storage, and a separate control-plane watchdog must enforce cost limits. A timeout on SSH alone does not stop Pod billing. The temporary Pod was deleted and its absence verified.

`wallet_acceptance.py` exercises native send and backup restoration against the completed private chain with test approval callbacks. `diagnose_candidates.py` is a prepared diagnostic for fixed recorded failures; it was not executed in this run and cannot supply a verdict or repair claim.

## Replay precision (12 October 2026)

The A40 logit-severity REDs were dominated by the verifier's own arithmetic: on GPU it replayed bf16 proofs with a bf16 forward pass, rounding activations at every layer, while the CPU path always replayed in fp32. The verifier patch now computes the reference forward pass in fp32 for bf16/fp16 proofs on every device (`_replay_compute_dtype`, `NOET_REPLAY_COMPUTE=declared` restores the old behaviour for diagnosis). Declared-precision grids, ULP-derived sigma and every gate constant are unchanged. Measured on 38 recorded llama.cpp proofs (`cpu/replay_precision.py`, `../validation/replay-precision.json`): median per-proof maximum severity 4.1 sigma with a bf16 reference versus 1.78 with fp32, maximum 8.35 versus 3.82, and no step at or above 6 sigma with fp32. GPU confirmation is recorded separately in `../validation/gpu-acceptance.json`.

## Control plane and durable evidence

`control.py` replaces interactive Pod handling. `create` pins the image by digest, records a spending cap and deadline, can restrict hosts to a driver CUDA level (`--cuda 12.8`) and opens extra relay ports. Its start command reuses SSH host keys, runs `sshd` without a session timeout and asks the provider to stop the Pod at the deadline even if the operator machine is offline. `watchdog` and `sync` run as detached local processes: the watchdog deletes the Pod at 90 % of the cap or at the deadline (forcing deletion if collection fails, because billing control outranks evidence), and sync copies `output/` every two minutes while excluding node data, wallets, cookies and passwords. `delete` refuses to remove a reachable Pod until every collected file matches a manifest computed on the Pod. The provider API key is read from a private file and never sent to the Pod.

`stage.py` rebuilds every input archive from the reviewed exporters and verifies the model snapshot and GGUF digests. `session.sh` runs build, the plain-completion regression, the 101-block maturity/spend/indexer/restart run, the evidence audit, the summary, wallet acceptance and the fresh-peer replay as one detached sequence, recording each stage's exit code.

Hosts matter: one community host exposed the GPU to `nvidia-smi` but not to CUDA (`cuInit` 999 on a 580 driver), and secure-cloud network volumes refuse `chown`, which the root-owned model protection needs. Use `--cuda 12.8 --cuda 12.9` and `--volume 0` for these runs.

## Live operation

`miner.py` and `live.py` run the GPU half of the hosted development network; see `../deploy/HOSTED-NETWORK.md`.
