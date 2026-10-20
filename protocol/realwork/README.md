# Private useful-work integration

This profile is a disposable integration network. It cannot be used as a public launch or a production monetary policy.

The source overlay gives the existing native implementation a separate genesis, network magic, port and `nwrk` address family. It registers exactly the pinned Qwen3-0.6B research candidate at the native difficulty normalizer. The binary refuses mock/desktop verification and requires external real verification. It does not alter proof validation thresholds or shorten coinbase maturity. Its inherited laboratory reward schedule is unrelated to the proposed mainnet tokenomics.

The fixed activation schedule starts v4 at height 1, proof-history enforcement and the transition penalty at height 2, and the v3 sunset at height 3. Operator flags cannot override those fields in this profile. The engine receives the live native height, target, parent, coinbase commitment and registered model. The first accepted block passed the native node's own Quick checks and an authenticated actual Full GREEN verdict. Its reward was immature. Evidence is in `../validation/private-realwork.json`. The subsequent A40 run passed heights 1, 2 and 3 with explicit Flash Attention off; height 3 includes mandatory detector acceptance. See `../gpu/README.md` and `../validation/gpu-acceptance.json`.

The research receiver explicitly uses unapproved prompt calibration in shadow mode. Height 1 is ruleset 2 and its detector is advisory. These limitations remain even when a block is accepted. The guarded service continues to refuse uncalibrated deployment.

## Reproduce a bounded local run

Prepare the reviewed native sources first. Export the two-file overlay with an independently reviewed native compiler image pinned by digest:

```sh
python3 protocol/realwork/prepare.py --builder registry.example/native-builder@sha256:REVIEWED_DIGEST --out /tmp/private-work.tar
```

The source exporter verifies the baseline commit and laboratory patch. Its Dockerfile checks the base source hashes before applying the two files and forces recompilation despite normalized archive timestamps. Keep the built image ID, the source archive hash and the image's profile manifest together. A local tag substitution requires verifying its image ID before and after building; a tag alone is not provenance.

The runner requires the existing pinned CPU engine, converted GGUF, full verifier image and verified candidate snapshot described in `../cpu/README.md`. Those large artifacts are deliberately absent from Git. Supply the three actual image IDs and a fresh output directory:

```sh
python3 protocol/realwork/check.py --node-image sha256:NODE_IMAGE_ID --producer-image sha256:PRODUCER_IMAGE_ID --verifier-image sha256:VERIFIER_IMAGE_ID --out /absolute/private/output --blocks 3 --attempts 12
```

The node has no network interface, published ports or peers. Its verifier shares only that isolated network namespace. The runner uses an ephemeral transport key, streams large proofs through CLI standard input, waits for native Full GREEN and canonical connection, records failed attempts, and removes only its own containers. The key file is deleted on exit. An accepted duplicate submission is recorded as an idempotent replay, not described as a rejection.

The receiver may see retries. It reuses only a completed Full verdict for the exact same envelope bytes, with a bounded 32-entry cache. Changed proofs, model identities, parent context or rulesets require fresh validation. Exceptions and incomplete detector evidence never produce a fabricated verdict. A native genesis request without height/ruleset context is recorded and receives no response.
