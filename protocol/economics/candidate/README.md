# Monetary-policy candidate

This is executable arithmetic for the existing NOET proposal, not activated consensus. No running node imports this header. It must not be used to claim that supply or vesting is enforced on a live network.

`policy.h` defines a 100 million NOET cap, 80 million mining reserve, 8 million team allocation, 8 million treasury allocation and 4 million ecosystem allocation (generated from `../tokenomics.json`). One NOET contains 100 million atoms. Height zero has no mining subsidy; it accounts for the proposed 20 million non-mining genesis allocation, with 5 million initially unlocked (2 million team, 1 million treasury, 2 million ecosystem).

Mining starts at height one. Each 52,560-block epoch distributes 15% of the remaining mining reserve, rounded down in atoms. When that rounds to zero, one remaining atom is assigned per epoch until the reserve is exhausted. Within an epoch, cumulative-floor allocation distributes the exact budget, with block rewards differing by at most one atom. The reserve finishes after 224 epochs. These are block-based model years, not guaranteed calendar durations.

Each class unlocks its initial amount at issuance and the rest linearly over 150 model months (657,000 blocks), with no cliff; in aggregate 100,000 NOET a month. The unlocked figure is an upper bound: it does not subtract immature mining rewards, unspent allocations or lost coins.

Integer multiplication is decomposed before division so intermediate values stay in range. Height inputs through the maximum unsigned 64-bit value clamp to fully issued or vested totals. There is no floating-point arithmetic in the candidate.

## Acceptance

```sh
python3 protocol/economics/check_candidate.py --out /tmp/noet-policy-acceptance.json
```

Requires Clang with AddressSanitizer, UndefinedBehaviorSanitizer and unsigned-overflow instrumentation. It builds in a temporary directory, checks every one of the 11,773,440 emission blocks for cumulative mining, supply cap and per-epoch reward bounds, then compares 937 boundary/sample heights against Python's arbitrary-precision calculations. The report records source digests. No network, GPU or wallet data is used.

The checked-in evidence is `protocol/validation/candidate-policy.json`.

## Before consensus integration

The 100 million proposal exceeds the existing laboratory's 21 million monetary range. Audit amount parsing, transaction validation, wallet balances, fee logic, RPC serialization and all aggregate-money checks before changing network constants. Integrate the subsidy only into a separately identified real-work network, then test node agreement, reorgs and recovery.

Supply-accounting functions do not lock coins. Implement and independently review the actual genesis outputs and their spend restrictions, with exact destination scripts and vesting milestones. Ordinary multisig ownership is not chain-enforced vesting. No allocation keys, beneficiary addresses, genesis issuance, consensus switch or live minting is created by these files.

The native integration audit is in [INTEGRATION.md](INTEGRATION.md). It identifies the unspendable genesis coinbase, monetary-range callers, allocation-size limits and the release tests needed before activating this proposal.
