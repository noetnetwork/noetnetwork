# Verdict attestations, light clients and penalties

Draft, 29 October 2026.

## 1. Signed verdict attestations

Today a verdict carries an HMAC under the secret the node and verifier share.
That proves to the node that its verifier answered, but nobody else can check
it, and every verifier holds the same secret.

Proposed: each verifier also holds its own Ed25519 key and signs an
attestation for every Full verdict:

```text
attestation = {
  "v": 1,
  "network": "noet-mainnet-1",
  "height": 2453, "block": "<hash>", "powHash": "<proof hash>",
  "model": "Qwen/Qwen3-0.6B@c1899de…", "ruleset": 2,
  "verdict": "green", "peakSigma": 1.78,
  "verifier": "<public key>", "at": 1793000000
}
signature = Ed25519(key, canonical JSON of attestation)
```

- The node stores attestations next to its verdict records; the explorer
  serves them at `/block/<hash>/attestation`, and the block page links them.
- Verifier public keys are published in `mainnet/verifiers.json` with the
  dates each was in service, so an auditor can check every verdict without
  trusting the explorer.
- The signature is extra evidence, not a consensus rule: the node still
  connects a block on its own verifier's authenticated GREEN.

This also gives the verdict attribution `RESILIENCE.md` asks for.

## 2. Light clients

A wallet that does not trust the API needs, per block: the header, enough
headers to check cumulative work, and an attestation from a verifier key it
trusts. With attestations in place:

1. The wallet keeps the header chain (80 bytes per block, about 4 MB a
   year at 600 s) and checks proof-of-work targets and ASERT itself.
2. For its own transactions it asks for merkle proofs (as in BIP37 or
   compact block filters, BIP157/158, which the node inherits).
3. It accepts a block as final only with a valid attestation from a key in
   its pinned verifier list.

What it cannot check is the replay itself; it trusts at least one listed
verifier. With several independent verifiers it can require two.

## 3. Penalties for wrong proofs

NOET has no stake, deposit or bond from miners, so there is nothing to slash.
A wrong proof already costs its author the attempt: it earns nothing, and the
GPU time is spent. Adding a deposit would make mining permissioned in
practice and adds a way for a faulty verifier to take miners' money.

Recommendation: no slashing for miners. Keep the costs that exist (rate
limits per address range, the 2 MiB size cap, Quick checks before replay)
and add, if spam appears, a per-address cool-down at the gateway after RED
verdicts. For verifiers, the remedy for a wrong verdict is removing its key
from `verifiers.json`; signed attestations make such a verdict provable.

## Not established

- Cost of signing and storing one attestation per block (expected to be
  negligible, not measured).
- Whether the inherited node still serves BIP157/158 filters on this chain.
