# NOET Mainnet status

Updated 24 October 2026.

## Running

- `noet-mainnet-1` has been live since 15 October 2026. Block 1 (16:41 UTC)
  carries the allocation outputs of *Tokenomics, working draft 1*; mining
  releases 12.5% of the remaining reserve per epoch.
- One hosted node on Fly.io, with the public mining gateway
  (`https://noet-mainnet.fly.dev:9443`) and the explorer behind
  www.noet.network.
- One GPU verifier and miner on a rented GPU host, connected to the node over
  the mutual-TLS relay. While the verifier is offline, no block is accepted.
- The development network and the public testnet were archived and shut down
  on 16 October 2026.

## Open work

| # | Item | Needs |
|---|---|---|
| 1 | Independent security review of the consensus overlay, vesting outputs, verifier and wallet | outside reviewers |
| 2 | Verification that does not depend on one operator: standby and independent verifiers, and the CPU path in `design/CPU-VALIDATION.md` | code, outside operators |
| 3 | Several independently operated nodes, with their names added to `seeds` in `mainnet/network.json` | outside operators |
| 4 | Switch to Qwen3-14B at an announced height (`design/MODEL-SWITCH.md`): node, verifier and miner support is in place and rehearsed on GPUs (`validation/model-switch-rehearsal.json`); height 3,500 and target scale 2 are set, and the switch-aware miner and node have run on mainnet since 22 October; a verifier host with an 80 GB GPU must serve before block 3,500 | operator |
| 5 | Validation of GPU architectures beyond compute capability 8.6 | GPU tests |
| 6 | A prebuilt miner image (the CUDA stages need an x86 build) | code |
| 7 | Signed desktop installers and store-ready mobile apps | code, accounts |
| 8 | Relay certificate rotation every 30 days (`deploy/rotate.py`) | operator |
