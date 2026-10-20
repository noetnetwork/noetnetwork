# NOET native network: implementation and release plan

Written 11 October 2026, before launch, and kept for its architecture and release gates. Current status: [MAINNET.md](MAINNET.md).

## Architecture

```text
Customer -> Vercel AI portal/API -> inference gateway -> proof-capable workers
                                                        |
                                                   block candidate
                                                        |
Independent peers <-> NOET consensus nodes <-> full model verification
                             |
                       canonical blocks
                             |
                 read-only indexer -> explorer API -> Vercel explorer

User's native wallet -> locally signed transaction -> private node or broadcast gateway
Privy -> application identity only
```

Only the website and request-oriented API run on Vercel. Nodes, indexing, model serving and verification need persistent cloud processes. MySQL stores application data; it does not implement consensus or the chain ledger. The explorer uses a rebuildable SQLite index for the initial single-writer deployment. Scale to a dedicated indexed database and replicas after measuring the workload.

## Network stages

1. **Laboratory (this integration):** isolated peers, `nlab1` addresses, explicit mock proof validation, no monetary value. Exercise native transactions, wallets, block propagation, rollback and recovery.
2. **Private real-work devnet:** same operational interfaces but a separately identified network with fully enforced inference verification and approved model registry. No public rewards or financial claims. Benchmark verification costs and adversarial failures.
3. **Public testnet:** fresh reviewed genesis, stable test address prefixes, explicit proof/difficulty activation schedule, faucet, signed binaries, external participants and documented resets.
4. **Mainnet:** independent review, approved economic rules, audited wallet/signing paths, multisite operations and incident playbooks. Never reuse test keys or quietly promote the lab chain.

## Hosting we rent

- Persistent Linux machines for bootstrapping peers and an archival/indexing node. Initial sizing hypothesis: 4–8 vCPU, 16–32 GB RAM and 200+ GB persistent SSD per node; revise after profiling.
- GPU hosts for miners and full verification. GPU memory requirements depend on the exact approved model, precision, sequence length and batching; do not promise a 24 GB card will run every workload.
- Persistent storage for model weights and node data; encrypted, restoration-tested wallet backups kept separately.
- HTTPS ingress for public read APIs, private RPC, restricted administration and infrastructure alerts.
- At least two independently operated nodes for a meaningful external test; two containers on one laptop demonstrate integration only.

Runpod integration version 1.7.1 is installed and OAuth sign-in succeeded. The initial control-plane inventory returned no Pods. The owner approved a total **$10 acceptance-test limit on 11 October 2026**, conditional on funding the account, for one temporary GPU Pod that must be removed after testing. This does not authorize continuous hosting. The owner confirmed $10 of account credits later that day. One temporary A40 test Pod was provisioned in EU-SE-1 at the observed $0.59/hour compute rate; its acceptance result and confirmed removal must be recorded before marking cloud testing complete. Recheck capacity and actual billed usage during the run. Prepare and check the test artifacts before starting the billing clock, include storage in the limit, and verify removal afterward.

Read-only catalog observation on that date: A40, 48 GB, $0.59/hour; L4, 24 GB, $0.59/hour; RTX 4090, 24 GB, $0.89/hour. These were live console quotes, not bookings; availability, storage charges and final checkout pricing must be rechecked. Do not rent continuous capacity before agreeing a spending cap and validating provider permission for the mining workload. Hetzner's published terms prohibit cryptocurrency mining, so it is not the default mining host.

## Additional systems required

| System | Release acceptance evidence |
|---|---|
| Economics | Approved supply/emission schedule, reward maturity, fees, allocation and difficulty rules; exact subsidy tests |
| Useful-work security | Modified-output, replay, substituted-model, grinding and invalid-proof rejection; verifier outage fails closed |
| Model governance | Pinned weight hashes, reproducible runtime, license review, model registration/update policy |
| Chain operations | Multi-host synchronization, partitions, reorgs, crash recovery, reindex and snapshot verification |
| Consumer wallet | Native address validation, secure key recovery, correct custom transaction signing, fee review and external audit |
| Explorer | Real-node comparisons, complete address history, pending transactions, pagination, index restart/reorg consistency |
| Mining distribution | Reproducible installation, hardware benchmarks, worker authentication, pool share validation and payout accounting |
| Public testnet | Faucet rate limits, no-value labels, network/version compatibility, reset process |
| APIs | Quotas, caching, DDoS controls, private node RPC, restricted service credentials |
| Releases | Pinned dependency graph, SBOM, source attribution, build provenance, signing and update strategy |
| Reliability | Disk/GPU/peer/lag alerts, on-call ownership, rollback procedures and restoration drills |
| Product/privacy | Customer workload consent, retention policy, prompt confidentiality and separation of public proof data |

## Public launch gates (set before launch; open items are tracked in MAINNET.md)

No mock validation; no external upstream verification fallback; no upstream peers; approved NOET genesis and economics; model revision fixed; all nodes agree on proof and difficulty rules; invalid work earns nothing; independent full-verifier runs; wallet backup restored successfully; real transaction visible in explorer; crash/reindex/reorg tests pass; cloud spending capped; security findings resolved.

Upstream's `feature_daav2_asert_tensorreg.py` comments describe a historically broken tensor-reg reindex path. The 102-block isolated NOET integration test passed a full reindex on 11 October 2026, without changing verification logic. This is bounded evidence for mock laboratory blocks only; repeat recovery testing with real inference proofs before any public launch.

## Research provenance

- https://git.tensorcash.org/tensorcash/tensorcash/src/branch/main/Readme.md
- https://git.tensorcash.org/tensorcash/bcore/src/branch/main/README.md
- https://tensorcash.org/blog/how-to-run-a-verifier
- https://tensorcash.org/blog/how-to-mine
- https://git.tensorcash.org/tensorcash/tensorcash/releases
- https://docs.runpod.io/agent-setup.md
- https://www.runpod.io/legal/terms-of-service
- https://www.hetzner.com/legal/terms-and-conditions/
