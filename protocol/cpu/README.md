# CPU inference-evidence experiment

These experiments use real model weights and inference. The first generates legacy version 2 evidence and compares the reported logits with an independent Transformers replay. A second now generates a real VDF bound to a synthetic header and runs the actual quick and full verifier. Neither validates a native block, awards coins or establishes public mining readiness. Both use deliberately artificial block context and an easy target. Do not send their output to a network.

The candidate is [Qwen3-0.6B at a fixed revision](https://huggingface.co/Qwen/Qwen3-0.6B/tree/c1899de289a04d12100db370d81485cdf75e47ca). Its pinned repository includes an Apache-2.0 license. Ten files total 1,519,209,243 bytes. Every downloaded file was checked against the vendor's Git object or LFS content identity, then included in `qwen3-06b.candidate.json` with a SHA-256 digest. The manifest SHA-256 is `c18e242c1f4c2273d89a27a226856ad1a6623a7a0c22839495b45371eefa3ae4`. This is an experiment candidate, not an approved production model or a promise of GPU compatibility.

## Observed result

Two isolated Linux ARM64 runs each completed 260 generated tokens and emitted a 256-token evidence record. The chosen tokens, sampling values, reported logits and commitment fields matched exactly between runs. Timestamp-dependent wire bytes differ. The independent CPU replay checked 17,920 reported logits against the original safetensors model using float32 arithmetic. Mean absolute difference was 0.020228, the 99th percentile was 0.075579 and the maximum was 0.242342.

Those differences are measurements, not an acceptance threshold. The producer uses a BF16 GGUF conversion and different numerical kernels. Full-verifier numerical compatibility, current proof-version rules, replay rejection and adversarial testing remain necessary. A successful text completion or matching repeated evidence does not satisfy those requirements. The experiment intentionally does not print a consensus pass.

See `../validation/cpu-inference.json` for source, model, conversion, engine and evidence digests. No weights, compiled binaries, private keys or running service are included in this directory.

## Repeat the experiment

Use a dedicated directory visible to your Docker engine. On macOS, the test VM may not share `/tmp`; use an ignored directory under the checkout. The following commands are local development steps, not a hosted deployment.

Prepare pinned sources and model files:

```sh
python3 protocol/bootstrap.py --engines
python3 protocol/cpu/prepare_source.py --out /tmp/noet-cpu-source.tar
mkdir -p protocol/.upstream/.cpu-work/source
NOET_CPU_WORK="$PWD/protocol/.upstream/.cpu-work"
tar -xf /tmp/noet-cpu-source.tar -C "$NOET_CPU_WORK/source"
python3 protocol/cpu/fetch_model.py --out "$NOET_CPU_WORK/model"
```

The downloader has a fixed model origin and manifest digest, verifies every byte, refuses overwrites and rejects insecure or unrelated redirects. It does not use Hub credentials or execute model files. Incomplete directories retain `.partial` files and fail snapshot verification. Use a new output directory after a failed attempt, or inspect and remove only the incomplete experiment output yourself.

Create an isolated Python 3.12 environment. The conversion/replay acceptance used Torch 2.8.0, Transformers 4.57.1, NumPy 1.26.4, FlatBuffers 25.2.10, SentencePiece 0.2.1, protobuf 4.25.8, PyYAML 6.0.2 and tqdm 4.67.1. Install these from binary wheels in that environment. Set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` for conversion and replay. The conversion uses the pinned engine's bundled GGUF module:

```sh
python "$NOET_CPU_WORK/source/llama/convert_hf_to_gguf.py" \
  "$NOET_CPU_WORK/model" --outtype bf16 --outfile "$NOET_CPU_WORK/model-bf16.gguf"
```

Compile the assembled source using the existing native-node build environment from `../docker/Node.Dockerfile`. That environment supplies CMake, Ninja, OpenSSL, FlatBuffers, ZeroMQ and Argon2. Record its immutable image ID; do not substitute a moving tag in acceptance records. The compiler options used were:

```sh
cmake -S /work/source/llama -B /work/build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF -DGGML_CUDA=OFF \
  -DGGML_METAL=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_OPENSSL=OFF -DLLAMA_CURL=OFF -DLLAMA_SERVER_SSL=OFF -DBUILD_SHARED_LIBS=OFF
cmake --build /work/build --target llama-server -j3
```

Run the build container with `--network none`, a 4 GiB memory limit, three CPUs, no capabilities, no new privileges, read-only root and only the experiment directory writable. Build logs must show successful Argon2, FlatBuffers and ZeroMQ discovery. The source export is deterministic; the OS packages and binary have not been independently reproduced.

Run `exercise.py` inside a fresh container using that same compiler image, with no network, read-only root, a 128 MiB temporary filesystem, 5 GiB memory, three CPUs, no capabilities and no new privileges. Mount the compiled engine and GGUF read-only and a separate output directory writable. Use your current non-root UID so generated files stay accessible. Do not publish ports or attach node/wallet directories.

```sh
python3 /checks/exercise.py --engine /work/build/bin/llama-server \
  --gguf /work/model-bf16.gguf --out /output/first
python3 /checks/exercise.py --engine /work/build/bin/llama-server \
  --gguf /work/model-bf16.gguf --out /output/second
```

The harness starts only a container-loopback HTTP listener and always terminates its child. It has a ten-minute inference timeout and does not inherit service credentials. `generation.json` names the generated MiningResponse file. Use those exact paths for comparison and replay:

```sh
python protocol/cpu/compare.py --first FIRST_RECORD --second SECOND_RECORD \
  --schemas "$NOET_CPU_WORK/source"
python protocol/cpu/replay.py --proof FIRST_RECORD --schemas "$NOET_CPU_WORK/source" \
  --model "$NOET_CPU_WORK/model" --out /tmp/noet-cpu-replay.json
```

Replay verifies the entire source-model manifest before loading safetensors, disables remote code, requires bounded record dimensions and reports measured differences. It uses no GPU and intentionally does not replace the production verifier. Stop the temporary build VM after testing. No cloud capacity is provisioned by these scripts.

## Complete CPU verifier experiment

`prepare_verifier_cpu.py` builds a separate Linux ARM64 research image from the locked verifier, schema, VDF and arithmetic sources. It includes actual PyTorch/Transformers dependencies and compiled libraries; no inference or cryptography is replaced with a fixture. The source export only includes an explicit list of NOET helper modules. The builder tag is checked against its recorded immutable local image ID before and after building, and cannot be overwritten by the output tag.

```sh
python3 protocol/prepare_verifier_cpu.py \
  --builder sha256:YOUR_REVIEWED_NATIVE_BUILDER_IMAGE_ID \
  --builder-tag noet-core:build-test --build-image noet-verifier:cpu-research \
  --docker-context YOUR_TEST_CONTEXT --out /tmp/noet-verifier-cpu.tar
```

Use Python 3.12 or later. The context is deterministic for identical inputs. Package versions resolved in the observed image are recorded in `../validation/cpu-verifier-packages.txt`; this is an inventory, not a hash-locked dependency set or reproducible-binary claim. OS packages and transitive wheels still require independent review and reproducible pinning before release.

Run every experiment in an isolated container with no published ports, `--network none`, read-only root, `--cap-drop ALL`, `--security-opt no-new-privileges`, `--pids-limit 256`, three CPUs and a 12 GiB memory ceiling. The observed test VM needed more than its original 6 GiB. Both original and compact full-verifier paths passed with 12 GiB containers inside a temporary 16 GiB VM. A killed process or missing report is a failed/incomplete run, never acceptance.

Mount this directory read-only at `/checks`, the verified model snapshot read-only at `/model`, its candidate manifest read-only at `/model-manifest.json`, and a separate experiment output directory writable at `/out`. Run as your non-root UID. Provide writable temporary filesystems at `/tmp` and `/data/pow_proofs`, with the latter accessible to that UID. Use the image ID returned by the build, not a mutable tag. Set:

```text
NOET_MODEL_ROOT=/model
NOET_MODEL_MANIFEST=/model-manifest.json
NOET_MODEL_MANIFEST_SHA256=c18e242c1f4c2273d89a27a226856ad1a6623a7a0c22839495b45371eefa3ae4
POW_V4_STRICT_MODE=enforce-requested
```

Override the image entry point with `timeout` and run these commands with a 600-second deadline. Each output path must be new:

```sh
python /checks/runtime_check.py --out /out/runtime.json
python /checks/bound_context.py --out /out/bound-context.json
```

Run the existing CPU producer with `exercise.py --context /out/bound-context.json` added to its engine, GGUF and output arguments. Keep the model and engine read-only. Use its resulting MiningResponse path below:

```sh
python /checks/reject_mutations.py --proof /out/bound/proofs/RECORD.bin --out /out/mutations.json
python /checks/verify_full.py --proof /out/bound/proofs/RECORD.bin --out /out/full-original.json
```

Repeat the last command in a fresh container with `POW_V2_STREAMED=1` and a new output path. To check false reported model outputs, add `--mutation corrupt-logits` with another new output path. That negative test only succeeds when the model actually loads and full verification returns RED. Positive runs require Quick_OK and full GREEN, with no caught exception. The full harness always requires the valid VDF and immediate parent/header binding.

The actual rejection harness changes the proof parent, header parent, header length, outer request parent, VDF and iteration count. All six must fail Quick; the four context mutations must also return RED directly from Full before loading weights. This demonstrates rejection of changed context, not duplicate-proof accounting or network replay protection.

These are **legacy version 2** requests with zero registered difficulty and synthetic block context. The native packer used here does not carry the newer height/ruleset fields. The model's prompt-gibberish calibration is absent and that gate reports shadow mode. No threshold was changed to obtain a pass. Current-rule proofs, calibration, registered difficulty, native block acceptance, rewards, duplicate submissions, GPU compatibility and independent protocol review remain open.

See `../validation/cpu-verifier.json` for exact evidence and build identities. The normal image entry point remains the guarded service launcher; these diagnostics explicitly override it and never connect to a node or service broker.

## Version-4 research profile

The generator also accepts `--proof-version 4 --tokens 2052`. Use `bound_context.py --priced` to create its synthetic context. This fixes the job target to the header's compact target `0x207fffff`, uses the protocol's temperature/top-k/top-p profile, and asks the existing engine for StepBind/v4 work. It never disables the producer's price or validity checks. Eight completed work windows can produce zero, one or several eligible records; a completed text response alone is not a mining success. The harness preserves the last 128 KiB of the sampler log on failure.

This remains a synthetic job, with a research difficulty of 60,000,000. It is not a node-issued job or an approved network/model difficulty. This generator leaves the job height absent. The separate height-transport check below exercises the patched producer, but does not provide trusted node context to this experiment.

The complete CPU image now selects the sealed detector bundle already present in the pinned source and validates/cold-loads it during the build. Its manifest hash is `e738325f205c681be1f00aeaff04a16bb6ea6270366158052c828efa6ad7f9c8`. Full ruleset-4 verification requires this detector; an unset bundle correctly produces no verdict. No rule, threshold or advisory-only override is used to obtain acceptance.

The legacy native request packer omits height/ruleset, so `verify_current.py` uses generated object-API bindings instead. Generate these from the same locked schemas, using the reviewed native builder's `flatc`:

```sh
flatc --python --gen-object-api -o /bindings validation.fbs blockheader.fbs proof.fbs
python3 protocol/cpu/fix_object_bindings.py --bindings YOUR_GENERATED_BINDINGS_DIRECTORY
```

The narrow repair resolves the generated `ProofT._UnPack` parameter shadowing the `proof` package in three nested-array factory calls. It changes no fields or schema types and refuses an unexpected generator output. The verifier harness checks that the entire decoded proof survives serialization unchanged, including array values.

Mount the bindings read-only at `/schemas`, in addition to the model/manifest/check/output mounts above. Select the detector-enabled image ID returned by the latest build. Set `POW_V4_STRICT_MODE=enforce` and `POW_V2_STREAMED=1`. For the six-view experiment, use a 20 GiB container memory ceiling inside a temporary 24 GiB VM, three CPUs and a 900-second deadline, while retaining the offline network, read-only mounts and other isolation settings. These are test limits, not minimum hardware requirements:

```sh
python /checks/verify_current.py --proof /out/v4/proofs/RECORD.bin --out /out/v4-full.json
```

The request explicitly carries synthetic height 1, ruleset 4, the registered-difficulty test value, header bits and parent. The harness checks presence after serialization. Separate `--context-case` values `wrong-parent`, `missing-height`, `missing-ruleset` and `unsupported-ruleset` require rejection or the specific no-verdict error before model loading. Each run needs a new output path. Their exit success means the requested negative test passed, not that the altered proof was accepted.

This can test ruleset dispatch and complete replay in isolation. A NOET network still needs its own reviewed identity/activation configuration, trusted node-issued work, model registration, native block/reward/reorg tests, calibrated detector/model coverage, GPU acceptance and independently operated peers.

Initial observation on 11 October, before the six-view fix: eight windows produced two records. One passed Quick but Full returned `DetectorUnsupported` because the CPU replay's reduced candidate set does not supply the sealed detector's required six-view input. The second returned Quick_Fail and Full RED for candidate concentration (`bad-candidate-band`). The detector process itself started ready with the correct rule and bundle digests. Missing/changed context checks passed, and five altered bundle cases were rejected before executing changed code or weights. **Neither record received full v4 acceptance in that initial run.** See `../validation/cpu-v4.json` for the unchanged historical record.

The reviewed CPU replay now retains candidate batches 2, 5, 10 and 20 whenever the detector attaches, alongside the baseline and SPDA views. Every view comes from its own actual forward computation; no observations are duplicated. The CPU noise setting, detector weights, rule digests and thresholds remain unchanged. Legacy requests without an attached detector retain the previous smaller candidate set. The harness records the real scorer's view names, support checks, counted positions and verdict without modifying its inputs or result, and restores the observer on shutdown. A positive exit requires all six named views and a required detector result in addition to Quick_OK and Full GREEN.

The follow-up run on 11 October passed Quick and Full for the same v4 record under both original and streamed replay. Each delivered all six real views and scored 105 eligible positions with no detector violation. See `../validation/cpu-six-views.json` for the image, source, proof and detector identities.

A successful scored result does not establish model-wide detector coverage: the sealed bundle explicitly reports that model-version coverage is unmeasured. The candidate also lacks approved prompt-gibberish calibration; that separate gate remains in its original shadow mode during this research. Neither setting is represented as production calibration.

## Job-height propagation check

The reviewed engine patch preserves the optional height supplied by the mining proxy. It accepts only JSON integers in the native header's unsigned 32-bit range. An omitted height stays absent, including when a slot previously ran a job with a height. Zero remains distinct from absence. This does not authenticate a caller or select a production activation schedule; the engine still needs the private, trusted job path.

After preparing sources with `bootstrap.py --engines`, export and compile them as above. The CPU source manifest records the engine patch digest. Run `height_check.py` in the same isolated offline container with the pinned model and a priced synthetic context:

```sh
python3 /checks/height_check.py --engine /work/build/bin/llama-server \
  --gguf /model/model-bf16.gguf --context /input/v4-context.json \
  --out /output/height-check
```

The harness checks both request envelopes, malformed heights, the uint32 boundaries and slot reuse. It also generates a v4 window using the pinned source's existing activation schedule as a laboratory fixture, then a window with no height. A passing result requires a real v4 classification with the height present, followed by the coordinator's missing-height diagnostic on the next job. The first assertion is only checked after a raw-target hit reaches the code that reads the height. It tries at most eight windows for each case because a window need not meet the unchanged raw target. If either required observation is missing, the run fails rather than inferring success. Any actual anti-parrot rejections are counted separately; zero such rejections is not evidence for or against that rule's correctness. It never changes acceptance thresholds, fabricates a height for an absent field, submits a block or claims production mining readiness. No NOET production activation is established by this test.


## Recorded native work

`protocol/tests/mining_work_smoke.py --out /tmp/native-work.json` runs two disposable, network-isolated nodes with real verification selected and external validation enabled. Set `NOET_NODE_IMAGE` to the immutable source-built image ID and `NOET_DOCKER_CONTEXT` to the test Docker context. No verifier is attached. The runner checks pre-v4 and active-v4 jobs, parent byte order, target encoding, unique coinbase commitments, invalid requests and tracker capacity. Both nodes remain at genesis and are removed afterward. Activation overrides and the OP_TRUE payout script are fixtures, not a network release configuration.

`node_work.py` replays one recorded active-v4 work unit offline. It preserves the node's request ID, height, target, header and flags, and generates a real VDF for that header's parent bytes. The model and 60,000,000 difficulty remain research inputs, not registered node values. The original node and work lease are no longer live. Eight windows are attempted without weakening the producer's checks. If no proof is emitted, the experiment fails.

The combined research image needs the compiled miner's dynamic libraries. Build `docker/CPUWork.Dockerfile` on the recorded CPU verifier image; check the local parent tag's immutable ID before and after building. This adds `libargon2-1` and `libzmq5` through the OS package manager, so it is not a reproducible dependency lock. Run it offline, read-only, with only the reviewed engine, pinned GGUF, test record and output directory mounted:

```sh
python /checks/node_work.py --record /node-work.json \
  --engine /engine/bin/llama-server --gguf /model.gguf \
  --out /output/recorded-node-work
```

The replay harness accepts `verify_current.py --node-record /node-work.json`. It rejects a changed header or request ID and constructs a validation envelope with the recorded height and the test node's strict-v4 profile (ruleset 2). It does not receive the native node's own serialized validation request, establish model registration, or submit a block. Research prompt checks remain uncalibrated and explicitly separate from the guarded service's required calibration.


For the recorded node fixture, ruleset 2 makes detector scoring advisory. A successful diagnostic requires Quick OK, Full GREEN, all six real views and a scored detector record, but it does not claim required-detector acceptance. Ruleset-4 diagnostics still require the mandatory detector result. Earlier raw runs that exited 2 despite Full GREEN used the old ruleset-4-only diagnostic assertion; the recorded verifier verdict is unchanged by correcting that assertion.

## CPU projection parity

`projection_check.py` exercises the actual replay method and the pinned model weights with the long recorded prompt. It compares the original full-vocabulary projection against selecting only the candidate prediction positions. Retained logits must be bitwise equal; full attention context and baseline prompt-scoring rows must remain intact. It does not calibrate MCA noise or establish detector coverage. The candidate-forward optimization only applies to CPU h0 models explicitly supporting `logits_to_keep`; other models, GPU and h1/h2 paths retain their existing behavior.

The recorded acceptance in `validation/node-work-cpu.json` covers five emitted v4 proofs from one native work unit, including a 1,563-token prompt. All five complete replay in a 20 GiB container. It preserves the preceding OOM and the two original diagnostic exit codes, and distinguishes early-profile advisory scoring from the separate required-detector regression. These are offline research results; no native block or reward was accepted.

For controlled diagnostics, `verify_current.py --replay-seed 7` fixes Python, NumPy and Torch randomness. Combine it with `NOET_CPU_FULL_LOGITS=1` to compare the original projection against the selected projection under the same seed. This changes the research harness only. Never choose a favorable seed as production acceptance policy or discard a failing unseeded run. The harness records the seed, projection setting and detector feature refusals alongside the verdict.

## Detector feature diagnostics

New runs without `--replay-seed` generate and record a random diagnostic seed before model initialization, so an unexpected failure can be repeated. This is a research harness choice, not a production randomness policy.

Add `--capture-detector /out/capture.npz` to save the bounded numeric detector inputs. Output is exclusive, object arrays are refused, and capture failures are reported independently of the verdict. Keep captures private to the experiment and outside Git. Within the pinned offline verifier image:

```sh
python /checks/detector_diagnostics.py --capture /out/capture.npz \
  --report /out/replay.json --out /out/feature-diagnosis.json
python /checks/capture_check.py --out /out/capture-check.json
```

The diagnostic checks the capture digest and archive bounds, disables pickle, validates the sealed bundle and reruns its exact feature builder for each eligible row. It reports token-union sizes and refusals; it never supplies a verdict or changes the detector.

`detector_width_probe.py --directory /out --out /out/width-stress.json` is a separate adversarial fixture using the recorded `diagnostic-seed0` through `diagnostic-seed5` files. It deliberately combines views from different audits and supplies constructed identity-sampling metadata. Its 142-token union exceeds the sealed contract's 128 slots. The real backend/gate must abstain with the width reason. IPC readiness is a fixture, and this must never be represented as a natural replay or native block test. Results and limits are recorded in `validation/detector-diagnostics.json`.
