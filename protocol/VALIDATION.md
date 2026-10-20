# Development validation — 11 October 2026

This evidence covers the isolated NOET laboratory integration. It does not establish production consensus security, economic soundness or real inference-proof mining.

## Passing checks

- Website: TypeScript, ESLint, 96 tests and production/Vercel build. Thirteen database-dependent tests were skipped because no test database was configured.
- Python: 17 tests covering exact amounts, atomic block application, duplicate spends, reorganization rollback, genesis isolation, reward maturity, RPC transport restrictions and the authenticated HTTP boundary.
- Actual two-node ARM64 container test: 102 blocks; matching pinned genesis; `nlab1` addresses; prepare/sign/finalize/mempool-check/broadcast of a 1.25 test-NOET PSBT; receiving node confirmation; explorer balance; rollback and restoration; encrypted wallet backup restoration; clean restart; full reindex. See `validation/lab-smoke.json`.
- Native targeted C++ suites: 70 cases / 1,344 assertions passed after fixture and helper corrections (address I/O, model challenges, co-signing).
- Native source build: daemon, CLI, descriptor wallet and C++ test binary compiled from pinned sources. The runtime includes its required event libraries and the upstream co-sign test helper.
- Brand package: 82 ZIP entries verified; all 54 current asset checksums verified. No public brand-guide or ZIP route remains.
- Browser: explorer's unavailable state and invalid-search handling checked, including narrow layout. No synthetic blocks or balances are substituted for a disconnected node.

## Full native suite and targeted rerun

The initial 2,119-case run completed: 2,075 passed, one passed with a warning, and 43 failed. All failures were confined to address-prefix/model-challenge fixtures and the co-sign suite. After correcting the fixtures and packaging the already-built mock helper, all 70 cases in those three suites passed (1,344 assertions). The remaining suites were not rerun because their code and fixtures were unchanged. This is combined evidence, not a claim that a second full-suite run completed. The external `script_assets_test` dataset was unavailable; that upstream test warns and skips its dataset checks.

## Review scope and fixes

Reviewed NOET's wrapper code, HTTP adapter, indexer, wallet command flow, network patch and container configuration. Fixed a missing runtime library, collision between RPC and the node's adjacent peer-listener port, incorrect persistent-data mount path, and the explorer image's writable data directory. The source bootstrap rejects unreviewed tracked changes in pinned dependencies.

The lab identity changes require updated test address and deterministic genesis fixtures. No proof validation or production consensus rule was disabled to make tests pass. The existing mock-verification mode is explicit, isolated and limited to laboratory testing. An upstream co-sign test used the argument manager's network string instead of the active chain parameters; its fixture now selects the wrong network against the same active parameters the RPC checks.

## Evidence still required before public mining

Real GPU inference proofs and independent full verification; invalid/replayed work rejection; fixed model hashes and licensing; multi-host partitions and recovery; consumer-wallet recovery/signing review; approved economics; release reproducibility and independent security review. A mocked local chain cannot satisfy these gates.

At this point the GPU cloud account had a zero balance and no capacity was purchased. The service was not running on cloud infrastructure, and the public explorer was not connected to a chain.

## Native Qt development build — 11 October 2026

`Wallet.Dockerfile` built successfully on Linux ARM64 with Qt 6.4.2. The isolated launcher’s two tests pass; the Python suite now passes 19 tests (including the loopback HTTP boundary test).

The initial Qt run failed four stale/selection-related checks and aborted during shutdown. The reviewed wallet patch corrects right-click transaction selection, updates the asset fixtures, and keeps display labels separate from internal asset identifiers. All 14 Qt suites now pass: 88 reported test results including initialization and cleanup, with a successful process exit.

Valgrind traced the shutdown failure to `BitcoinApplication::notify`: the shutdown window emitted events after the event-context string had already been destroyed, causing an invalid write and double free. The window is now destroyed explicitly while that state is alive. A subsequent full memory run confirmed those errors were gone and identified three leaked recipient-layout items (264 bytes); the send dialog now deletes those items after removing them. The final full Valgrind run exits successfully with zero errors, zero definite/indirect/possible lost bytes and no suppressed errors. It retains 48,123 bytes reachable by the runtime. Qt regex JIT is disabled only for instrumentation; the normal suite also passes with its default JIT setting. Exact evidence is recorded in `validation/wallet-check.json`.

The GUI stall watchdog remains enabled in normal application builds. Tests disable it because synchronous block generation under Valgrind takes several minutes. Laboratory chains do not automatically start the inherited external Tor bridge. No consensus or proof checks were disabled by these GUI changes.

## Miner configuration and build inputs

The pinned miner used `bool(os.getenv("TEST_MODE", True))`, which treated the string `false` as true and enabled synthetic jobs. `noet-miner.patch` replaces this with strict boolean parsing: unset is false, explicit true enables test jobs, and invalid values reject startup. Four regression tests import the actual pinned configuration and its arithmetic dependency without running a miner. Two additional tests verify patch idempotency and rejection of unrelated tracked source changes. The Python suite passes 25 tests with no skips after bootstrap.

The bootstrap now checks reviewed changes in both the umbrella miner repository and the native core. This is build-input and configuration validation, not evidence of real-work mining. GPU proof generation and independent verification remain open.
The presentation patch changes selected display strings and the icon only. Many inherited upstream screens still need a dedicated NOET product/security review. The lab launcher does not enable useful-work mining. Production miner validation still requires the separately identified real-work network, pinned model artifacts and independent GPU verification described in NETWORK-PLAN.md.

## Native CLI transaction review, 11 October 2026

All 34 Python protocol tests pass, including the loopback HTTP boundary. Nine new tests cover unsigned PSBT review, restricted signature modes and scripts, live unspent input matching, mining maturity, total and rate fee limits, private bounded file handling, refusal before unlocking, relocking after signing errors, and the transaction ID output without broadcast. Review performs no unlock or signing operation.

The actual two-node integration passed again with transaction review before unlocking: 102 blocks, an external 1.25 test NOET recipient, encrypted backup restoration, indexer rollback, restart and full reindex. This remains a mock-proof laboratory test, not evidence of useful-work mining. The CLI supports ordinary native P2WPKH transfers only and still requires an independent security review before a production release.

The Runpod connection can now list Pods; the latest check returned no Pods. No paid capacity was created. The public development explorer remains disconnected from a chain.

## Explorer history pagination, 11 October 2026

The protocol suite passes 36 tests. A 206-entry address history is read across three pages without gaps or duplicates, including a 205-transaction block. The test also covers a newly appended block, a wrong anchor block and a removed anchor after rollback. The authenticated HTTP service returns 409 for an invalidated anchor. Pagination rejects duplicate, empty, conflicting and malformed query parameters.

The website gateway has six passing tests, including cursor forwarding and preservation of the reorganization response. Address details now offer earlier and latest transactions, with a restart action after a failed history page. TypeScript, targeted ESLint and the production build pass. Live populated history still requires the hosted native chain; no live-chain pagination or visual acceptance is claimed by these tests.

## Miner proof-source isolation, 11 October 2026

All 41 protocol tests pass with no skips. Nine exercise the actual pinned miner configuration and VDF service, including strict test-mode parsing, disabled remote proof fallback, rejection of unverified-proof settings, URL redaction in configuration errors, refusal to start without the local prover, and local-only worker startup. The thread-routing test stubs prover availability and thread creation; it does not generate or verify a real proof.

The expanded patch applied cleanly to pristine copies of both pinned source files. Reverse application checks, exact patched-source comparison and the repeated-bootstrap source check passed. No consensus rule was changed. Real GPU inference proofs, independent verification and multi-host operation still require separate testing.

## Concurrent miner state updates, 11 October 2026

All 44 protocol tests and the pinned miner's ten existing context tests pass. Two new regression tests deliberately pause one proof update while another writer changes the block or advances the proof tick. Both fail against the original pinned context and pass with serialized read-modify-write updates. A third test checks immutable snapshots, per-job mode resets and rejection of an inconsistent proof mode.

The miner retains immutable snapshot reads. A writer lock now protects the complete state update and associated readiness flags; atomic reference assignment alone did not protect against stale concurrent writers. No proof algorithm or consensus rule changed. The expanded three-file patch passed pristine-source application, reverse checks, exact source comparison and repeated-bootstrap validation. These are concurrency and integration checks, not real GPU proof validation.

## Registered miner model selection, 11 October 2026

All 51 protocol tests pass without skips. Seven new tests exercise the pinned miner's model helpers and actual request-injection method: exact identity preservation, missing registry entries, ambiguous revisions, incomplete records, mismatched names/hashes/pins, and registry outages. Request-injection tests execute the method extracted from the actual source with dependency stubs, verifying that rejected requests are not rewritten; they do not run the full inference service or GPU engine.

The four-file miner patch applies to pristine pinned sources and passes reverse-application, byte-equivalence, syntax and repeated-bootstrap checks. Ordinary mining no longer silently selects a built-in fallback or the newest of several registered revisions. Genesis generation remains a separate explicit path. Model artifact hashes, licensing, registry deployment and end-to-end GPU proof checks remain open.

## Complete local acceptance pass, 11 October 2026

The native laboratory now passes competing-branch partition recovery and forced-process crash recovery at height 105, in addition to the transfer, full reindex and rollback checks. The restored encrypted wallet signs a new transaction that passes mempool policy without broadcasting it. Wrong-passphrase unlocking and corrupted-backup restoration are rejected. See `validation/lab-smoke.json`.

The combined `protocol/validate.py --mysql --native` acceptance command completed successfully: 58 protocol tests, six economics tests, ten companion tests, TypeScript, the web build and native recovery. All 114 website tests passed against disposable MySQL with the four checked-in migrations, including all 13 previously skipped database integration tests. The runner uses a pinned official MySQL image, random dedicated database and credentials, loopback-only port, and removes its container and volumes. It never uses the hosted database URL. Ten companion tests and six economics tests also pass.

Seven model-artifact tests cover deterministic manifests, immutable revision requirements, exact inventory/digests, trusted manifest pinning, tampering, missing/extra files, symlinks/special files, unsafe paths, duplicate fields and CLI create/verify/no-overwrite behavior. This validates a file-integrity utility, not actual model weights, licensing or GPU proofs.

The public website/API/database check passed, anonymous workspace access was denied, and one provider was listed without an API-key field. The native chain remains unavailable. The shipped companion opened without captured console errors. No Chrome surface was connected and native app control reported the Mac locked; actual extension loading and physical mobile acceptance remain unverified. See `RELEASE-STATUS.md` for the full outstanding engineering and external dependencies.

## Native extension bridge acceptance, 11 October 2026

70 protocol tests and five new JavaScript module tests pass. A separate laboratory extension requests status, receiving addresses, review and signing through a bounded native host. Tests cover invalid requests, exact caller identity, private configuration, framing limits, changed-review rejection, cancellation, relocking on errors, deterministic packaging and launcher paths containing spaces and quotes. No browser password or automatic broadcast path exists.

The two-node recovery test exercised the actual bridge handler against a restored encrypted native wallet. Cancellation kept it locked; signing returned a mempool-valid PSBT, relocked the wallet and left the mempool empty. Partition, reindex and crash recovery passed again at height 105. Approval was supplied through a test-only Python callback, not Chrome or the desktop window. Actual extension installation and native UI are unverified; this Python lacks Tk and the desktop is locked. No native host was registered.

## Source-exported node and release preparation, 11 October 2026

85 protocol tests pass, including model revision/digest validation, source export and RPC transport checks. Mutable model revisions, abbreviated hashes and unscoped model names are refused. The source exporter validates pinned commits and exact reviewed patches, reconstructs from Git objects, and excludes untracked or ignored checkout data. Two final exports have identical SHA-256. The archive built successfully on Linux ARM64 using the pinned Ubuntu digest. See `validation/source-build.json` and `validation/node-image.json` for artifact identities, 138 installed packages and four executable hashes. No signed or reproducible-binary claim is made.

The rebuilt image passed the complete native recovery test at height 105. The runner now resolves and records the immutable local image ID before creating nodes, and never implicitly pulls an image. Native bridge signatures remained separate from broadcasting. The actual Linux Tk approval window also passed three headless tests for approve, cancel and close; its review is read-only, password entry is masked, and empty approval does not close it. Actual Chrome/macOS testing remains outstanding.

The inactive manual-only GitHub workflow template was parsed locally and uses official action commit pins. It has not run on GitHub. Existing website/MySQL/build evidence is unchanged because this pass does not change the website.

## Guarded model launcher, 11 October 2026

100 protocol tests pass. Thirteen new runtime tests exercise exact artifact identity, mount checks, offline child environment, distinct explicit credentials, the actual patched supervisor's local-model arguments, bounded authenticated HTTP control, refusal to restore or switch unapproved models, and child shutdown/reaping. Two package tests verify immutable parent references and deterministic reconstruction of the reviewed supervisor without including weights or credentials.

Five additional Docker checks passed using actual Linux read-only mounts: the approved fixture is accepted; writable root, writable nested mount, wrong digest and changed weight bytes are rejected. See `validation/model-mounts.json`. Tiny fixtures contain no usable model weights, no GPU was used and no inference/proof validation is claimed. The prepared wrapper still requires an independently reviewed GPU parent image and licensed model snapshot. The independent verifier has not been integrated with this new guard.


## Guarded verifier artifact boundary, 11 October 2026

The full protocol suite passes 115 tests after adding 15 verifier checks. All eleven Transformers calls in the patched proof verifier/model auditor route through the local artifact guard. Tests cover rejected loader overrides, failed artifact checks before loading or tensor creation, cached reuse, disabled IPFS, local safetensors inspection with bounded headers, startup refusal of synthetic mode, and two identical verifier context exports. Methods are executed from the actual patched source with heavy dependencies stubbed; no GPU inference or proof correctness is claimed. See [VERIFIER-RUNTIME.md](VERIFIER-RUNTIME.md) for the one-model scope and remaining acceptance work.


The subsequent VDF input-boundary pass brings the full protocol suite to 123 passing tests. Eight added cases cover exact native wire sizes, unchanged byte order, malformed/oversized hex, uint64 bounds, unsupported discriminants, exceptions and strict boolean results. The size check is tied to the pinned native source. Native cryptography is stubbed in these new boundary tests; no new cryptographic or GPU acceptance is claimed.


## Real native VDF acceptance, 11 October 2026

`tests/native_vdf_smoke.py` exports only the locked native VDF Git objects, builds a temporary test executable with Clang and GMP, and runs with AddressSanitizer and UndefinedBehaviorSanitizer configured to halt on findings. No network, GPU, cloud service or production data is used. The macOS ARM64 run generated a 10,000-iteration, 200-byte proof and accepted it. All 204 invalid cases were rejected: a different challenge, a different iteration count, a one-bit mutation at each of 200 byte positions, a truncated proof and an extended proof. No sanitizer finding occurred. The temporary executable and source export are removed after the run.

The record is `validation/native-vdf.json`, including the proof vector, pinned source commit, compiler, executable digest and test-source digest. The linked system GMP library is not sanitizer-instrumented. This is bounded native VDF acceptance, not a full cryptographic audit, proof of AI inference, or cross-platform release certification.

Repeat on this Mac with:

```sh
python3 protocol/tests/native_vdf_smoke.py --include-dir /opt/homebrew/include --library-dir /opt/homebrew/lib --out /tmp/noet-vdf-new-record.json
```

On Linux with Clang and GMP development headers installed, omit the include/library overrides. The general acceptance command accepts `--native-vdf` and an optional `--gmp-prefix`. The inactive CI template includes the check, but has not run on GitHub.


## Relocatable native host package

Three packaging checks pass: deterministic allowlisted files and manifest hashes, rejection of symlink inputs before output creation, and an extracted bundle running from a directory with spaces in isolated Python mode. The generated manifest grants one exact Chrome origin, configuration files are mode 0600, and the launcher is mode 0700. An unauthorized origin returns a framed, generic error with no stderr or local paths. No browser registration or node connection was made. The full protocol suite now contains 126 passing tests.


Seven verifier-service boundary checks bring the protocol suite to 133 passing tests. They cover private endpoint selection, invalid ports, envelope bounds before parsing, exact request-hash length, remote-delegation refusal, and socket limits before binding in the actual extracted constructor. This does not claim authenticated network transport, global memory limits or real multi-host acceptance. The prepared verifier image now includes the patched service entry point and transport helper.


## Unactivated monetary arithmetic

`economics/check_candidate.py` compiled the proposed C++ schedule with address, undefined-behavior and unsigned-overflow instrumentation. All 11,773,440 blocks across 224 emission epochs passed supply and reward-distribution checks. Another 937 height vectors, including epoch/cliff boundaries and maximum uint64 height, matched independent arbitrary-precision calculations. No sanitizer finding occurred. `validation/candidate-policy.json` records source hashes and the compiler. This is arithmetic acceptance only: chain integration, monetary-range audit and enforceable vesting outputs remain unimplemented.


## Shared admission and required verdict signing

The protocol suite passes 145 tests. Eight new admission checks cover aggregate byte/count limits, concurrent saturation, shared ownership across mirrored checks and retries, rejection before parsing without emitting an invalid-proof verdict, and release before a worker waits for its next request. The limits are 16 queued/active envelopes and 128 MiB, with the existing 32 MiB per-message limit. These bounds do not include decoded tensors, retained caches, status metadata, socket buffers or total process/GPU memory.

Four signing checks exercise the actual pinned Python broker against the shared native-node vectors, mutation binding and unsigned startup refusal. The guarded verifier now requires `VALIDATOR_SHARED_SECRET` to be exactly 64 hex characters representing a separately generated 32-byte secret, including on loopback. Operators must supply the same secret privately to the node. No secret is generated, stored in the repository or printed by the entry point. Deterministic packaging includes the pinned signing broker rather than inheriting an unspecified broker from the parent image.

This HMAC format authenticates outbound verdicts. It does not encrypt connections or authenticate incoming verification requests; remote deployment still requires an authenticated private transport and operational resource limits.

The existing Linux ARM64 node image also passed all ten `validationapi_stale_verdict_guard_tests/verdict_*` cases: 732 assertions, with the rest of the native suite deliberately excluded. Cases cover shared vectors, wrong keys, unsigned/shape errors, malformed payloads, outstanding request/type matching, pending model reviews, ruleset mismatch, legacy behavior and receive-budget drops. The image ran without networking, with a read-only root and bounded temporary storage/memory. `validation/verdict-auth.json` records its immutable image ID and case counts. This is the existing node's behavior, including its legacy unsigned mode; the new verifier entry refuses that mode. The container was removed and temporary VM stopped afterward.


## Private mutual-TLS transport

The full protocol suite now passes 163 tests. Fifteen relay cases use actual temporary TLS certificates and sockets on macOS ARM64, and pass again on Linux ARM64 with Python 3.12.3/OpenSSL 3.0.13, uid 10001, a read-only container, 512 MiB memory and no external network. They cover binary streaming, missing/untrusted/wrong-purpose client certificates, wrong peer pins, wrong server names, modified certificate signatures, TLS 1.2 refusal, connection admission, idle/session/handshake timeouts and cleanup. Review found and fixed a shutdown race: cancellation during graceful closure could retain a connection slot. Tests now assert its release.

The guarded verifier refuses non-loopback plaintext endpoints before loading models or importing the service. Two packaging tests verify a deterministic CPU-only allowlist, source hashes, absence of configuration/credentials, non-root container entry, immutable parent references and symlink refusal. `deploy/transport/README.md` documents the four relay processes and the remaining operational boundaries.

A disposable environment with pyzmq 27.1.0/libzmq 4.3.5 passed real ZeroMQ multipart framing and reconnection across the TLS link: two messages, six frames and 2,097,220 payload bytes, including an empty frame. This does not run the full verification service or certify its separately pinned dependencies. The Linux fixture initially lacked the OpenSSL command-line utility; a separate disposable test image added it, and all 15 cases then passed. No production image or hosted service was changed.

`validation/secure-transport.json` records source and log hashes, versions, image identity and test scope. These are loopback tests, not cross-host acceptance or GPU proof verification. Handshakes precede the active relay limit; host firewall/resource limits remain required. Certificate issuance, rotation, private routing and independent review remain deployment work. The temporary VM was stopped after acceptance.


## Native wallet approval review

The protocol suite passes 166 tests and the wallet JavaScript modules pass 16 tests. The native host now renders exact recipient amounts, wallet-owned outputs, network fee, total leaving the wallet, all addresses and the full fingerprint as readable text. The extension uses the same labels and exact integer arithmetic. Three new Python checks plus one JavaScript case verify exact totals, self-transfers, 100-output completeness and malformed-review refusal.

The real Linux/Xvfb approval window passes all three interaction cases: explicit approval, cancellation and window-close. Signing is disabled when either the passphrase or review checkbox is missing. The entry remains masked; cancellation and window-close return no passphrase even after disposable test text is entered. Existing native host cancellation, transaction recheck, signing/relock and no-broadcast tests remain green. The extracted host package still runs without the source checkout and now includes its separate presentation module.

This is a tested development signing workflow, not a signed consumer installer or a production wallet. macOS/Chrome installed acceptance remains blocked by desktop availability; no action bypassed that boundary. The Linux test container was removed and VM stopped afterward.

## Native transfer preparation: 11 October 2026

All 171 protocol tests and 20 wallet JavaScript tests pass. Five new Python cases check decimal precision, refused browser overrides, recipient validation and unexpected output rejection. Three controller cases execute the actual extension module with DOM/native-message fixtures: preparation, mismatched recipient refusal and a delayed file read that cannot overwrite a fresh draft.

The two-node smoke check now obtains its initial 1.25 test-NOET PSBT through the native bridge's prepare operation and asserts that the encrypted wallet remains locked. Signing, restored spend authority, wrong-passphrase/corrupt-backup rejection, partition/crash recovery, reindex and rollback pass at height 105 using the pinned native image. The node needs public derivation metadata to estimate unsigned witness size; preparation includes it locally without exposing any private key. The initial attempt without that metadata was correctly refused because the fee rate could not be checked. The corrected run passed. See `validation/wallet-preparation.json`. This uses mock laboratory proofs and a test approval callback, not Chrome or useful-work mining.

## Admission before TLS: 11 October 2026

The relay now reserves a connection slot immediately after accepting TCP, before creating an asyncio stream or TLS protocol. Incomplete handshakes and authenticated sessions share a four-connection limit. Excess sockets are closed directly; the accept loop yields to existing sessions even while excess connections arrive. Shutdown cancels handshake tasks, closes their sockets and releases the listener.

Eighteen real TLS cases pass on both macOS ARM64 and the existing isolated Linux Python 3.12 test image. New cases saturate handshake slots, confirm 16 excess sockets never enter TLS, verify valid traffic after freeing capacity, reject 12 malformed handshakes without leaking slots, and rebind after shutdown with handshakes in progress. Actual ZeroMQ multipart transfer and reconnection pass again. The full protocol suite passes 174 tests. This bounds admitted relay connections, not total operating-system/process memory or network traffic. Firewall and resource limits remain required. See `validation/relay-admission.json`.

## Real CPU inference evidence: 11 October 2026

The pinned CPU engine was assembled from Git objects and compiled in an existing isolated Linux ARM64 compiler image with no network. Two independent source exports have identical SHA-256. A pinned Qwen3-0.6B snapshot totaling 1,519,209,243 bytes was downloaded from its public vendor repository, checked against vendor Git/LFS identities, and inventoried with SHA-256. The repository includes its Apache-2.0 license; this is a candidate, not production approval.

Two network-disabled CPU runs each completed 260 generated tokens and emitted a legacy version 2 MiningResponse with 256 chosen tokens. Token, sampling, reported-logit and commitment fields match exactly between runs. Independent CPU Transformers replay compared all 17,920 reported logits with the original safetensors model. Mean absolute difference: 0.020228; 99th percentile: 0.075579; maximum: 0.242342. These measured numerical differences are not converted into a fabricated pass threshold. The request intentionally has artificial block context and no valid VDF. No full consensus verifier, current proof-version rule, valid block, GPU or public mining acceptance is claimed.

The reproducible experiment scripts live in `cpu/`; source/model/engine/evidence digests are in `validation/cpu-inference.json`. Four additional tests reject corrupted, truncated and oversized model downloads, unsafe paths, existing files/links, insecure redirects and unrelated hosts. The full protocol suite passes 178 tests. The preceding combined run passed all 114 web tests with disposable MySQL, 20 wallet tests, six economics tests, TypeScript and the production build. Temporary model weights, binaries and development environments are excluded from Git.

## Native allocation-lock acceptance, 11 October 2026

`tests/vesting_smoke.py` creates one disposable internal-network laboratory node and imports transaction/signature test helpers from the exact pinned Git commit. A P2WSH output combines a block-height CLTV lock with two-of-three signatures. The keys are public test scalars, never production beneficiaries.

All three valid signer pairs passed at the boundary. Eleven rejection cases passed: early height, the preceding block, lowered transaction locktime, final input sequence, one signature, no signatures, an unrelated signer, a duplicated signer, reversed signatures, a removed script lock and reorg back before eligibility. The valid spend entered block 111 for locktime 110. Restart and full reindex retained the spent funding output and exact remaining amount. See `validation/vesting-locks.json`.

The node still uses mock laboratory proof validation. This tests the script primitive, not issuance, a beneficiary ceremony, cancellation policy or deployed tokenomics. `economics/monthly_vesting.py` supplies a separate 169-output monthly candidate. Its four tests check exact totals, all monthly boundaries, cliff, yearly agreement and decimal-string JSON. Ten economics tests now pass. The 178 protocol tests also pass on Homebrew Python 3.14.8.

The Mac was unlocked, but the native desktop tool denied access and Chrome was not connected to browser control. A disposable sample window was closed without claiming visual or installed-browser acceptance. Homebrew Tk support is installed; the real Mac approval interaction remains outstanding.

## Guarded verifier challenge binding, 11 October 2026

The prepared verifier now requires immediate VDF challenge binding for every supported request mode, rather than allowing legacy requests to use the default-off operational setting. Disabled or delayed activation fails before model loading. Four focused tests pass, including the actual extracted full-verifier entry rejecting a mismatch before initialization. The parser/context/heavy dependencies in that test are fixtures; no GPU replay or complete proof acceptance is claimed. All 182 protocol tests pass. See `VERIFIER-RUNTIME.md` for the compatibility limits and `validation/verifier-binding.json` for file digests.
## Complete CPU verifier acceptance, 11 October 2026

The isolated CPU research image now builds the actual parser, VDF library, credit/sunset kernels and full model replay dependencies. A newly generated 256-token legacy-v2 record contains a real 10,000-iteration VDF bound to a synthetic 76-byte header. Both original and compact full-verifier paths return GREEN, and Quick returns Quick_OK. Falsified reported logits return RED after actual model loading.

The native runtime also rejects 132 malformed envelopes, a missing proof and 202 invalid VDFs. Six altered proof/context cases fail Quick; the four parent/header changes also return RED directly from Full without loading a model. No heavy dependency was replaced with a fixture in these container checks. The host protocol suite passes 188 tests.

This is not current-rule, GPU, block, reward or public mining acceptance. Registered difficulty is zero, context is synthetic, and the candidate model's prompt-gibberish calibration is absent. The first 6 GiB VM could not complete replay; successful tests used a 12 GiB container inside a temporary 16 GiB VM. See [the evidence record](validation/cpu-verifier.json) and [repeat instructions](cpu/README.md#complete-cpu-verifier-experiment). The earlier unbound CPU experiment remains a separate historical result.

## Version-4 acceptance attempt, 11 October 2026

Eight native CPU work windows (2,052 generated tokens) emitted two v4 records. With explicit synthetic height/ruleset context and enforced ruleset 4, one passes Quick but Full returns `DetectorUnsupported`: the CPU replay does not supply the required six-view detector input. The other returns Quick_Fail and Full RED for candidate concentration. No full v4 or native block acceptance is claimed.

The sealed detector bundle validates and cold-loads, its scorer starts ready with the correct rule identity, and the final image requires this startup check during build. Five artifact mutations fail before changed code/weights execute. Four actual v4 context cases reject or abstain before loading the language model: altered parent, missing height, missing ruleset and unsupported ruleset. The protocol suite passes 189 tests. Evidence, exact build identities and the failed acceptance reports are in [cpu-v4.json](validation/cpu-v4.json).


## Mining job-height transport

On 11 October 2026, the compiled CPU engine passed 22 malformed-height rejections and 14 valid/absent-height checks across both supported request envelopes and slot reuse. Two further v4 windows generated 520 tokens: the first reached classification with the supplied height; the next produced the missing-height diagnostic, confirming that height did not leak across jobs. Total generation was 534 tokens. Both raw-target hits missed the unchanged priced boundary. No anti-parrot rejection, accepted proof, block or reward is inferred from this run.

The new engine patch is included in deterministic CPU source exports and checked on bootstrap. The shared GPU snapshot function now uses the same uint32 validation; three tests compile and execute that actual pure function without importing GPU libraries. This is source-level evidence only, and the launcher wrapper does not retrofit it into an older parent GPU image. All 193 protocol tests pass. Detailed identities and observations are in `validation/miner-height.json`.


## Six-view CPU verifier acceptance

The CPU candidate-set reduction is now conditional: requests with an attached detector compute batches 2, 5, 10 and 20 plus baseline and SPDA, while legacy requests without that detector keep their prior smaller set. Both original and streamed replay use the same rule. The CPU noise setting, detector models, thresholds and rule digest are unchanged. The diagnostic harness observes the actual scorer and restores that observer on cleanup; it never substitutes an observation or verdict.

On 11 October 2026, the previously unsupported v4 record returned Quick_OK and Full GREEN in both implementations. Each scored all six views and 105 eligible positions under the required sealed detector, with no violation. The second candidate remained Quick_Fail / Full RED. Wrong-parent, missing-height, missing-ruleset and unsupported-ruleset cases still failed before model loading. The new image also rejected 132 malformed envelopes, 202 invalid VDF variants, a missing proof and five modified detector-artifact cases. All 193 protocol tests passed. Exact identities and results are in `validation/cpu-six-views.json`.

These are real model forwards and detector scores over synthetic height/ruleset/header context. The model-version coverage check explicitly remains unmeasured, and the candidate has no approved prompt-gibberish calibration. GPU execution, trusted node-issued work, accepted native blocks, rewards, reorgs and production network activation are not established. The two replays ran on the same host and are not independent-worker confirmation.


## Guarded calibration and native job issuance, 11 October 2026

All 210 protocol regression tests pass. The guarded verifier now requires explicit model/revision/precision calibration declarations and immediate prompt enforcement with fixed policy values. The actual proof loader refuses unreviewed context before model allocation; the verdict path rechecks before cache reuse. Synthetic score fixtures cover threshold boundaries, malformed prompts, scoring outages, missing context, precision mismatch and configuration drift. They are not empirical calibration data.

Eight offline runs through the final image's actual entrypoint refused missing/overbroad calibration, logging-only or disabled checks, delayed activation, changed thresholds and a disabled guard. The previously passing real v4 record also produced `PromptGibberishScoringError` with no model loaded and no verdict when guarded mode lacked calibration. See `validation/calibration-startup.json` for the exact image, source-context digest and observed result. No candidate calibration was added.

The current source-built native image also passed a new work-issuance runner on two isolated, disposable lab nodes, with real verification selected and external validation enabled. Pre-v4 and active-v4 profiles each issued four distinct work units. Parent byte order, compact target decoding, height, flags and unique coinbase roots passed; nine invalid/capacity cases per profile were refused. A batch request exceeding remaining capacity returned a bounded partial allocation. Both nodes stayed at genesis with zero peers and were removed. There was no attached verifier, accepted block, registered research model or reward. See `validation/native-work.json`. The runner is included in `validate.py --native`.

## Recorded native work and long-prompt CPU replay

One authentic work unit from the active-v4 fixture was replayed offline through the compiled CPU miner with a real 10,000-iteration VDF. Eight windows generated 2,052 tokens and emitted five v4 records. The original node and lease were no longer live; model selection and difficulty remained unregistered research inputs. The replay preserved the recorded header, height, request ID, target and activation flags.

All five records returned Quick OK and Full GREEN with all six actual detector views scored. Prompt lengths were 27, 795, 1,051, 1,307 and 1,563 tokens. The early-v4 node fixture uses ruleset 2, whose detector is advisory. Its validation envelope was constructed by the harness, not emitted by the node. Two raw processes initially exited 2 because the diagnostic incorrectly expected a ruleset-4 mandatory detector; the corrected profile assertion passed over their unchanged reports. The original exit codes are preserved in the evidence.

The first 1,051-token replay in the preceding image was killed for exhausting its 20 GiB container. CPU h0 candidate forwards now select only prediction positions for vocabulary projection; all attention rows, MCA draws, candidate batch sizes and baseline prompt-scoring positions remain. An actual model comparison at batch size 2 returned bitwise-identical retained logits, with zero maximum difference. All five complete replays subsequently ran without OOM under the same 20 GiB cap. The longest took about eight minutes on three CPU cores. This demonstrates the tested memory improvement, not a general capacity guarantee.

See `validation/node-work-cpu.json` for image and source identities, original failure, projection comparison, complete replay results and final mandatory-detector/invalid-candidate regressions. Prompt calibration and model-version detector coverage remain unestablished. There was no GPU execution, native block acceptance, reward or multi-host confirmation. The guarded service refuses uncalibrated deployment; the offline research harness does not activate it.

The final unseeded ruleset-4 replay of the previously passing synthetic proof did not pass: Quick was OK, but Full raised `DetectorEvidenceIncomplete` for unscored eligible position 233. The verifier correctly supplied no verdict. The known invalid candidate still returned Quick Fail and Full RED. The evidence retains both outcomes. A fixed-seed comparison of the original and selected projections is recorded separately to investigate this failure without changing thresholds, eligibility or refusal behavior. One successful seed cannot erase the unseeded failure or establish stable detector coverage.

With diagnostic seed 7, both the original full projection and the selected projection returned Quick OK and Full GREEN under the required detector. Their joint decision records matched exactly: 106 eligible positions, no violation and no counted positions. The selected run reported no feature refusals. This comparison supports projection compatibility for that seed and proof. It does not explain away the unseeded gap; broader deterministic replay and feature-coverage analysis remains required.

## Reproducible detector diagnostics, 11 October 2026

Seven predetermined seeds (0 through 6) each returned Quick OK and Full GREEN under the required detector. The exact sealed feature construction was rerun offline on each hash-checked numeric capture. All eligible rows built successfully; the maximum token unions were 126, 128, 127, 126, 127, 125 and 119 respectively. These are repeated observations of one proof on one host, not broad accuracy or independent-worker acceptance. The original unseeded failure remains unresolved.

A separate adversarial fixture takes complete view observations from those captures at position 233, using seed indices `[0, 2, 3, 3, 0, 4]` for the six views. This creates a 142-token union. The real sealed backend refuses that position under its 128-slot contract, and the real gate raises `DetectorEvidenceIncomplete` with the exact feature reason. The sampling metadata and ruleset context are constructed and IPC readiness is stubbed; the features, trained models and gate predicate are unchanged. This test demonstrates the input-width failure mechanism. It does not establish that a natural replay produced this combination or prove the cause of the original lost inputs.

The rebuilt image passes the known valid seed-0 regression, still rejects the invalid candidate with Quick Fail / Full RED, and refuses all eight unsafe calibration startup configurations. The host suite passes all 219 tests. Five runtime capture checks cover numeric/metadata round-trip, exclusive output, object-array refusal, size refusal and SHA-256. New archive tests reject hash mismatch, duplicate/path-escaped members, excessive members and decompression expansion. See `validation/detector-diagnostics.json` for identities and results.

New research runs record an explicit random seed even when one was not requested; all randomness is fixed before model initialization. Optional capture failures are reported separately and cannot change the verifier's result. Captures are local, bounded, numeric and read with pickle disabled. Raw proofs, captures and model weights remain excluded from Git. None of these changes approves a model, changes detector policy, accepts a native block or enables public mining.
