# noet-mainnet-1

NOET Mainnet, live since 15 October 2026: block 1, with the allocation
outputs, was mined at 16:41 UTC. It runs the testnet's consensus code with its
own identity. The genesis (13 October 2026 00:00 UTC, nonce 1, hash
`fc3e7390…5a54`) is pinned in the wallet, miner and explorer.

| | |
|---|---|
| Addresses | `noet1…` (bech32), base58 prefixes 0x3C / 0x7C / 0xBC |
| P2P port, magic | 33440, `4e 4d 4e 31` |
| Difficulty | ASERT from height 2 (`asertHeight`), 12-hour half-life toward 600-second blocks, starting from the minimum difficulty. Height 2, not 1: the schedule is measured from the block before it, and block 1 is mined at launch, not at the genesis time |
| Seeds | `seeds` in `network.json`: DNS names compiled in as the chain's seeds; each must resolve to nodes listening on 33440. Starts with the hosted node, `noet-mainnet.fly.dev` (needs a dedicated IPv4, as the testnet has). Add other operators' names as they join; hosted nodes with seeds also dial out |
| Genesis | `fc3e73901e2831df8457ea56f56cf92cd23018453439a0c1d402cbab61f45a54` (time 1791849600, nonce 1) |
| Economics | `protocol/economics/tokenomics.json` (cap, allocations, vesting), with mainnet's own release rate: 12.5% of the remaining reserve per epoch (`releasePercent` in `network.json`; the testnet runs 15%) |

## Launch steps

Kept as the record of the launch; every step is done.

1. Final economics: the rate is in `network.json` (12.5%, checked with
   `economics/check_candidate.py --percent 12.5`); everything else is in
   `economics/tokenomics.json` (`tokenomics.py --write`, then the tests).
   `tokenomics.py --write` also generates the website's figures for both
   rates; a build with `VITE_NOET_NETWORK=main` shows mainnet's.
2. Real signers: each allocation holder copies "Your cosigner key" from the
   wallet, then
   `python3 protocol/testnet/set_allocation_keys.py --network mainnet --team K,K,K --treasury K,K,K --ecosystem K,K,K`.
3. Genesis: done. `genesis.py --time` fixed it and wrote the hash, which
   `test_mainnet.py` checks against the lab and testnet nodes' own hashes
   (`--pin` recomputes it). It is pinned in `web/src/wallet/core.ts` (`main`),
   `protocol/miner/noet_miner.py` and `protocol/noet/indexer.py`. The time
   cannot change now without re-pinning all of them.
4. Build the test variant, run `web/src/wallet/testnet.integration.test.ts`
   against it (block 1 outputs, vesting locks), and confirm `getblockhash 0`
   equals `genesisHash`.
5. Deploy the release variant (`deploy/fly/prepare.py --profile mainnet`,
   app `noet-mainnet`, no faucet: the node refuses faucet secrets) and attach
   verifiers, as for the testnet. The GPU miner's reward address must be a
   wallet you control; there is no faucet address on mainnet.
6. Website: build with `VITE_NOET_NETWORK=main`. It shows mainnet's figures,
   pins its genesis, gives miners `--network mainnet`, and has no faucet (the
   faucet route stays off on a mainnet build). Live mining figures stay
   hidden until `launched` is set in `web/src/pages/mine/config.ts`.

Status and open work: `protocol/MAINNET.md`.
