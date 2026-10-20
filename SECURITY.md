# Security policy

NOET Mainnet (`noet-mainnet-1`) launched in October 2026. The code has not had
an independent security audit.

## Reporting a vulnerability

Please report privately through GitHub: open the repository's **Security** tab
and choose **Report a vulnerability**. Do not open a public issue for anything
that could let someone:

- get a block accepted without valid useful-work and VDF proofs, or a valid
  block rejected;
- take or redirect mining rewards, allocation outputs or testnet faucet funds;
- reach the node's RPC, wallet or keys through the gateway, explorer or faucet;
- break the relay TLS between the node and the GPU verifier;
- make the public services unavailable cheaply.

Include the commit, the steps to reproduce and what you observed. We aim to
acknowledge within three working days. Please give us a reasonable time to fix
an issue before disclosing it.

## Scope

In scope: everything under `protocol/` and the services it deploys. Upstream
components (TensorCash, bcore, llama.cpp, chiavdf) should also be reported to
their maintainers when the flaw is theirs.
