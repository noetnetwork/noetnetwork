# Security program

Draft, 29 October 2026. No part of NOET has had an independent security
review (`MAINNET.md`, item 1). This file sets the scope for one, the rules of
a bug bounty, and how releases are signed.

## 1. External audit: scope

In priority order, with the files that hold each part:

| # | Area | Why it matters | Where |
|---|---|---|---|
| 1 | Consensus overlay: model per height, model protection, difficulty reset, vesting outputs in block 1 | a flaw forks or stops the chain, or unlocks allocations early | `testnet/prepare.py` (node patches), `mainnet/`, `economics/` |
| 2 | Verdict path: relay, mutual TLS, verdict HMAC, node acceptance only on Full GREEN | a forged verdict connects an invalid block | `deploy/transport/`, `noet/verifier_transport.py`, `noet/verifier_entry.py` |
| 3 | Verifier replay and guards: model manifests, challenge binding, abstention rules | a lenient replay accepts fake work | `noet/model_runtime.py`, `noet/admission.py`, `VERIFIER-RUNTIME.md` |
| 4 | Mining gateway: limits, leases, task queue | denial of service, redirected rewards | `noet/gateway.py`, `noet/tasks.py` |
| 5 | Wallet core: BIP39 and BIP84 keys, BIP143 signing, vaults, encrypted storage | loss or theft of funds | Companion wallet core (`wallet/core.ts`, `vault.ts` in the website source, not yet in this repository) |
| 6 | Miner: payout check from the node's coinbase, no key handling | a hostile gateway steering rewards | `miner/noet_miner.py` |

Out of scope for the first audit: the website's marketing pages, the
developer workspace, and the upstream components (bcore, llama.cpp,
chiavdf), which should be reported to their maintainers.

Deliverables asked of the auditor: a report with findings by severity, a
retest after fixes, and permission to publish the report in `validation/`.

## 2. Bug bounty

Starts only after private vulnerability reporting is switched on for the
public repository (`SECURITY.md`). Payouts are the owner's decision and are
not set here; the tiers say what qualifies:

| Severity | Examples |
|---|---|
| Critical | a block connects without valid useful-work and VDF proofs; allocation outputs spend before their locks; a verdict is forged |
| High | rewards redirected away from the miner's address; the gateway or relay stopped cheaply from outside; wallet keys exposed |
| Medium | a limit in the gateway bypassed at scale; a verifier abstention forced on honest proofs |
| Low | information leaks with no direct loss |

Rules: test only against your own node or a private chain, never mainnet's
hosted services beyond normal use; report through GitHub's private advisory
form; give us time to fix before disclosure (`SECURITY.md`). The first
reporter of an issue is the one rewarded.

## 3. Signed releases and SBOMs

- **Miner image.** The "NOET miner image" workflow signs each pushed image
  with Sigstore (keyless, tied to the workflow) and attaches an SPDX SBOM as
  a signed attestation:

  ```sh
  cosign verify <image>@sha256:... \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    --certificate-identity-regexp '^https://github.com/<owner>/<repo>/.github/workflows/miner-image.yml@'
  cosign verify-attestation --type spdxjson <image>@sha256:... (same identity flags)
  ```

- **Source exports.** The export manifest lists the SHA-256 of every file
  (`release/open_source.py`). Signing the manifest the same way, in a release
  workflow, is the next step.
- **SDKs, wallet and desktop builds.** Not signed yet. npm and PyPI releases
  should use trusted publishing from a workflow, with provenance; desktop
  installers need platform code-signing certificates (an owner purchase).
- **Dependencies.** Dependabot opens weekly pull requests for the pinned
  GitHub Actions and the miner image base; each must pass the tests
  workflow before merge.

## Not established

- No audit has been commissioned and no bounty amount is set.
- The SBOM step has not yet run; it runs on the next image push.
