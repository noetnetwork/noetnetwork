# Guarded mining backend preparation

The reviewed launcher now loads one approved local model snapshot. It refuses writable model mounts, changed files, mismatched manifest digests and identity changes. It no longer restores a model from saved runtime state or enables remote model code. This is deployment preparation, not a working public mining release.

## What is enforced

Before spawning vLLM, the launcher checks the complete model inventory against `NOET_MODEL_MANIFEST_SHA256`, confirms the requested organization/model and immutable revision, and verifies read-only filesystem flags for the model root and every file. Checking each file also catches a writable mount nested inside a read-only root.

Restricted Linux container hosts can explicitly set `NOET_MODEL_PROTECTION=root-owned` when read-only mounts are unavailable. Run the loader with a nonzero UID, all real/effective/saved/filesystem UIDs equal, zero effective/permitted/ambient capabilities and `NoNewPrivs=1`. Every model file and every ancestor directory must be owned by root, must not be group/other writable, must not be a symlink and must deny write access to the loader (including ACLs). Keep the privileged setup process separate from the worker. Unknown modes, root loaders and privilege-regain configurations are refused. This protects against modification by the loader, not by the trusted host administrator. Snapshot identity and complete hashes are still checked at every boundary. Seven actual Linux permission cases passed on the temporary GPU host, including failed file-write, chmod and directory-rename attempts.

Both model and tokenizer load from that verified local directory. The served model name remains the registered identity. Loading uses safetensors, offline Hugging Face/Transformers settings and no remote-code flag. Hub account tokens and the administrative key are removed from the child environment. The inference key is supplied through `VLLM_API_KEY`, not a command-line argument.

Inference and administrative credentials must be distinct, separately generated secrets of at least 32 ASCII characters. There are no default keys. Both listeners default to loopback. An explicitly configured private interface still needs network policy; never expose the supervisor publicly.

The control endpoint accepts an idempotent request for the existing model. Any other model requires a reviewed deployment restart and is refused without stopping the current worker. Requests are bounded, authentication compares secrets in constant time, and errors do not return internal paths. A dead backend reports HTTP 503. Failed child startup ends promptly; a stubborn child is killed and reaped during shutdown. If stopping fails, the supervisor retains its process reference.

The reviewed shared GPU sampler source now rejects malformed job heights instead of silently dropping them. Present heights must be unsigned 32-bit JSON integers; zero is retained and a missing key remains absent. Its actual pure snapshot function is tested without GPU imports. The parent GPU image must be built from this patched source and tested on supported hardware. The launcher-only image wrapper does not retrofit sampler changes into an older parent image.

## Prepare the image context

First build and review the pinned vLLM v0.16 fork, its PoW extensions and dependencies. That GPU parent image has not been built or accepted in this pass. Do not run its inherited entrypoint directly: the NOET launcher requires the helper modules supplied by this wrapper.

Use the independently recorded parent image digest:

```sh
python3 protocol/prepare_miner.py \
  --base-image registry.example/noet-engine@sha256:REVIEWED_PARENT_DIGEST \
  --out /tmp/noet-guarded-miner.tar
```

The command checks the locked source and exact reviewed patch, reconstructs the supervisor from Git plus that patch, and creates a deterministic context. It does not build, pull an image, download model weights, register a model, provision a server or spend GPU funds. The context contains no credentials or model weights. Its Dockerfile runs as UID 1000 and replaces the parent's entrypoint.

After the parent and context are reviewed, build with:

```sh
docker build -t noet-miner:candidate - < /tmp/noet-guarded-miner.tar
```

Required runtime settings:

| Setting | Value supplied by the deployment owner |
| --- | --- |
| `MODEL_NAME` | Exact approved organization/model identity |
| `MODEL_COMMIT` | Full lowercase 40-character immutable revision |
| `NOET_MODEL_ROOT` | Absolute container path of the read-only snapshot |
| `NOET_MODEL_MANIFEST` | Absolute path of the manifest, mounted separately |
| `NOET_MODEL_MANIFEST_SHA256` | Independently approved digest, not one supplied by a worker |
| `API_KEY` | Secret for the inference endpoint |
| `VLLM_ADMIN_KEY` | A different secret for the local/private control endpoint |

Keep model weights read-only. Supply a separate writable runtime cache for the engine and compiler, scoped to the worker. Allow only required private node/broker traffic; offline library flags are not a network firewall. The manifest and container image must come from a trusted release process. A privileged host can replace mounts or memory after verification, which this guard cannot prevent.

## Evidence and limits

Tests exercise the actual supervisor source, argument construction, credential handling, rejected model switches, HTTP request limits, child lifecycle and artifact checks. Five additional container checks use real Linux mounts: valid read-only files pass, while a writable root, writable nested mount, wrong manifest digest and changed weights fail. Fixtures are tiny non-model files. No inference is executed.

The parent GPU image, usable licensed model snapshot, proof generation, independent verification, network identity, throughput and memory benchmarks, and adversarial mining acceptance remain outstanding. A passed artifact check does not establish useful-work correctness or production mining readiness.

## Separate native GPU research

The A40 acceptance in `gpu/` builds the pinned native inference engine directly, not the vLLM parent described above. Explicit Flash Attention off passed three real private-work blocks, including the mandatory-detector transition; auto-enabled Flash Attention failed candidate-support checks. This establishes a bounded compatible research configuration, not acceptance of this guarded vLLM image. Both paths still require approved model calibration and release review.
