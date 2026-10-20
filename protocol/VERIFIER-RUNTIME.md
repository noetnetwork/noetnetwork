# Guarded development verifier

This verifier deployment accepts one independently approved model identity and revision. It uses the same protected snapshot and manifest contract as the [miner runtime](MINER-RUNTIME.md). It is not an accepted production verifier or a public mining release.

## Artifact boundary

The startup entry checks the manifest, exact model identity, full revision, file inventory, hashes and read-only mounts before importing the verification service. The default protection is a read-only mount. The explicit Linux `root-owned` alternative and its privilege checks are described in the miner runtime document. Synthetic proof mode is refused. Hub offline flags are set before application imports and Hub account tokens are removed from the process environment.

All eleven Transformers loading calls in the proof verifier and model auditor use the approved local snapshot. Tokenizer, model configuration, generation configuration and weights cannot fall back to a Hub download. Weight loaders require safetensors, remote model code is disabled, and the IPFS fallback raises an error. Dtype inspection uses approved local safetensors only, with a bounded header read. Proof input and cached-model reuse paths also check the artifact boundary.

Registration auditing is deliberately restricted to this deployment's approved model. This is not a general permissionless model-registration service. Supporting several models needs a reviewed registry of independently approved manifests and explicit routing; do not loosen the guard to accept arbitrary on-chain repository names.

## Prepare an image context

First obtain an independently reviewed verifier parent image built from the locked source and dependencies, with its application at `/app/src`. An arbitrary image digest is not evidence of a trustworthy parent.

```sh
python3 protocol/prepare_verifier.py \
  --base-image 'your-reviewed-verifier@sha256:YOUR_REVIEWED_64_CHARACTER_DIGEST' \
  --out /tmp/noet-guarded-verifier.tar
```

The command validates the pinned source and complete reviewed patch, then exports a deterministic context containing the guarded source files and helper modules. It does not download weights, build an image or provision a GPU. It refuses an existing output file.

After reviewing the parent and context:

```sh
docker build -t noet-verifier:candidate - < /tmp/noet-guarded-verifier.tar
```

Set `MODEL_NAME`, `MODEL_COMMIT`, `NOET_MODEL_ROOT`, `NOET_MODEL_MANIFEST` and `NOET_MODEL_MANIFEST_SHA256` using the same independently approved values as the miner. Mount the snapshot read-only and supply a separate writable cache and proof-data directory owned by UID 1000. The image runs without root. Keep broker and node access on a restricted private network. Offline library flags do not enforce network isolation.

## Acceptance scope

Fifteen focused tests cover guarded loader arguments, refusal before loading, proof-input and cached-model checks, disabled IPFS fallback, tokenizer routing, local dtype inspection, bounded header handling, deterministic packaging and startup refusal before application import. Tests execute extracted methods from the actual patched verifier and stub heavy GPU dependencies. They do not execute Transformers inference or establish proof correctness.

The artifact verifier currently hashes the full snapshot at each boundary, including proof input. This is intentionally conservative development behavior and has not been throughput-tested. Large snapshots will require a separately reviewed immutable-artifact lifecycle before production use. Do not silently substitute a path-only or timestamp-only cache.

The CPU research image includes the sealed detector bundle and validates its worker at build time. Both replay implementations now provide all six required views. One recorded v4 proof passed Quick and Full under the required detector on this host; see `validation/cpu-six-views.json`. Model-wide detector coverage is still unmeasured. Research harnesses deliberately override the service entrypoint, so their success is not approval to run the guarded service.

GPU numerical compatibility, broad adversarial coverage, memory/throughput limits, accepted native blocks and multi-host operation remain unverified. A privileged host can replace mounts or memory after verification. These guards do not replace independent protocol review.

## Mandatory prompt calibration

The guarded launcher now requires `POW_PROMPT_GIBBERISH_CALIBRATED` to list the exact approved `organization/model@40-character-revision:precision` pairs. Entries must match `MODEL_NAME` and `MODEL_COMMIT`. Wildcards, model-wide precision approval, duplicate entries, implicit source defaults and unknown precisions are refused. Do not add the research candidate merely to make startup pass. This declaration records an operator's reviewed calibration scope; the code cannot establish whether the underlying empirical evidence is adequate.

The launcher fixes prompt checking to immediate `enforce` mode, a 14.0-bit threshold, 32 minimum transitions and a 256-transition tail. Explicit off/shadow modes, delayed activation, threshold changes and disabling the required-calibration flag are refused. Changing these rules requires a coordinated, reviewed protocol release, not a deployment workaround. Short-prompt applicability remains the existing protocol rule.

The actual proof loader rechecks the model/precision declaration before allocating tensors or loading weights. The verdict path checks again before cached results or off-mode handling. Missing context, unreviewed precision or configuration drift raises the existing infrastructure-abstention exception: no GREEN or RED verdict is supplied. Malformed prompt evidence still receives RED; scoring infrastructure failure still abstains. No prompt calibration is approved for the 0.6B research candidate by this change.

Ten focused regression tests exercise these boundaries using the actual extracted verifier methods and synthetic score fixtures. Eight actual packaged-entrypoint refusal tests run with no model or credentials mounted; see `validation/calibration-startup.json`. Neither test set measures real prompt distributions or establishes calibration. Research replay remains explicitly separate and can still log the uncalibrated gate in shadow mode.


## VDF input boundary

The verifier accepts a 32-byte hex challenge and exactly 200 bytes of hex proof for the pinned 1024-bit, recursion-zero native implementation. The size follows the checked-in native form-size formula and two-form proof contract. Iterations must be an integer in the native uint64 range; booleans, floats, strings and overflow are refused. Hex prefixes and whitespace are not normalized. Bytes reach the native verifier without an endian conversion. Native exceptions and anything other than boolean `True` fail verification.

Eight focused tests check the service integration, byte order, malformed inputs, maximum lengths, iteration bounds, native failures and agreement with the pinned native source. Their native verifier is a test double. A separate native acceptance run now generates a real VDF proof and rejects 204 invalid cases under sanitizers; see [VALIDATION.md](VALIDATION.md). These boundary tests alone do not establish cryptographic correctness.


## Private development transport

Remote HTTP verdict delegation is disabled in the patched service and refused at startup if requested. The guarded image includes this service entry point, not just model-loading patches. `NOET_VERIFIER_BIND` defaults to `127.0.0.1`; `ZMQ_VERIFY_PUSH_HOST` defaults to `127.0.0.1` through the guarded launcher. The guarded launcher requires IPv4 loopback for both plaintext endpoints. Separate hosts must use the authenticated TLS relay; the service-level RFC1918 parser does not override this launcher restriction. Wildcard, public, metadata/link-local, DNS and IPv6 addresses are refused.

The inbound socket sets a 32 MiB maximum message size, a four-message receive high-water mark, and zero close linger before binding. The application checks the envelope size again before FlatBuffer parsing and requires a 32-byte request hash. Seven focused tests exercise these checks and the actual service constructor up to socket binding.

A shared admission budget now caps queued/active raw envelopes at 16 requests and 128 MiB, including mirrored checks and retries. The mutual-TLS relay limits connections before handshake allocation. These limits do not cap total process/GPU memory or kernel buffers. Private network policy, complete schema validation, process limits and sustained overload testing remain required before any exposed deployment.

## Mandatory challenge binding in the guarded launcher

The launcher now sets `POW_VDF_CHALLENGE_BIND_ENFORCE=1` and `POW_VDF_CHALLENGE_BIND_ACTIVATION_TIME=0` before importing the service. Explicit disable values or delayed activation are refused before model verification/loading. The existing verifier requires the proof's parent hash to match the 76-byte header prefix and, for context-bound node requests, the trusted outer parent. These checks now also apply to older proof modes inside this new guarded deployment. Current strict request rules already require binding independently of these switches.

This is deliberately unsuitable for replaying an older network's pre-activation history under legacy rules. It changes the prepared verifier deployment policy, not a running chain or node consensus. The new network and its node must use matching reviewed rules. Proof-only requests still lack an authenticated outer parent and must never be treated as block acceptance merely because their internal hashes agree.

Four focused tests exercise startup refusal, rulesets zero through four, malformed prefixes, parent mismatches and the actual extracted full-verifier method returning RED before model initialization. Parsing, request context and heavy inference dependencies are fixtures in that last test; it is not full model replay or accepted work. That pass recorded 182 protocol tests; current totals are in RELEASE-STATUS.md.


## CPU candidate projection memory

Long prompts exposed a transient allocation problem: the full-vocabulary output for every prompt position and every candidate row was materialized before selecting the 256 prediction positions. A 1,051-token recorded-job prompt was killed at the 20 GiB container limit before a Full verdict.

For CPU h0 candidate forwards, the replay now uses the model's explicit `logits_to_keep` argument when supported. It supplies the exact prediction positions after the complete attention body. Every batch row, attention context, candidate batch size, MCA context and draw order is preserved. Baseline prompt scoring still receives every prompt position; h1/h2 and GPU paths are unchanged. Models without the explicit argument retain their previous behavior. `NOET_CPU_FULL_LOGITS=1` selects the old projection for comparison; it is not a consensus switch.

The actual replay method with pinned weights produced bitwise-identical retained logits for a batch of two and a 1,051-token prompt, with all baseline scoring rows intact. This is projection equivalence evidence, not proof of model-wide detector coverage or calibration. Full recorded-job outcomes and the initial OOM remain documented separately.

## Incomplete detector evidence

The gate now includes bounded feature-refusal reasons for the missing eligible positions in its no-verdict exception. It converts the backend's zero-based step index to the public one-based position and retains at most eight reasons of 240 characters each. Unrelated or malformed entries are ignored. Required checks still abstain; advisory checks still return no detector outcome; completed decisions are unchanged. The guarded image exporter now carries this patched gate explicitly.

The current sealed input contract has 128 token slots. A stress fixture combining already observed views produced 142 distinct tokens at position 233; the actual feature builder refused it and the gate withheld a verdict. This fixture is constructed across audits, not a naturally emitted proof. Seven ordinary fixed-seed replays passed, so the previous unseeded failure's exact cause remains unresolved. No checkpoint, threshold, eligibility rule, layout identity or bundle hash was changed. Wider coverage requires a new reviewed serving contract and supporting calibration evidence.
