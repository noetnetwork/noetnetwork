# Fees and mempool policy

Draft, 29 October 2026. Fees are relay policy, not consensus, except that a
block's coinbase may claim no more than subsidy plus fees.

## What runs today

- **Who gets fees.** The block producer takes the full subsidy and every fee.
  There is no treasury tax, no burn and no staking yield
  (`economics/README.md`).
- **Node.** The hosted node runs the inherited relay policy with
  `-maxmempool=100` (MB) and `-fallbackfee=0.0002`
  (`deploy/fly/noet_node/supervise.py`). Its minimum relay fee and dust
  threshold are the inherited defaults; they have not been measured on
  mainnet.
- **Wallet.** Companion offers 1, 2 and 5 atoms per virtual byte (economy,
  standard, priority), never more than 100, treats outputs under 1,000 atoms
  as dust, and signals opt-in replace-by-fee on every input (BIP125, sequence
  `0xfffffffd`). "Speed up" rebuilds a stuck payment at a higher rate
  (`web/src/wallet/core.ts`, `WalletApp.tsx`).

## Policy

1. **Replace-by-fee.** Opt-in BIP125 as inherited: a replacement must pay a
   higher absolute fee and at least the incremental relay rate on its own
   size. Full RBF is not enabled.
2. **Minimum rate.** Keep 1 atom per virtual byte. Blocks are far from full,
   so a higher floor would only make small payments dearer.
3. **Mempool size.** 100 MB, which holds months of today's traffic.
   Eviction is by fee rate as inherited.
4. **Package relay.** Not needed until fees matter; revisit with the node
   upstream.
5. **Fee estimates.** With nearly empty blocks the inherited estimator has
   too little data. The wallet's fixed tiers stay until blocks are regularly
   more than half full; then the wallet should read `estimatesmartfee`
   through the explorer.

## Not established

- The live minimum relay fee and dust threshold of the hosted node.
- Behaviour of replacements under load; no load test of the mempool has been
  run.
- Whether fees will ever fund security at the current subsidy schedule
  (`economics/README.md` says a hard cap requires fee-funded security in the
  long run).
