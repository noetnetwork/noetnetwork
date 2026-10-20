# Monetary integration audit

The candidate arithmetic is not deployed monetary policy. These findings refer to the pinned native source and the current NOET laboratory patch. Preserve the existing laboratory identity and test balances while developing a separately identified real-work network.

## Consensus changes required

1. **Monetary range.** `src/consensus/amount.h` defines a consensus-critical range of 21 million coins. The proposal needs 100 million. `MoneyRange` is used by transaction validation, input/fee checks, transaction totals, wallet balances and RPC parsing. Raising one constant without checking these callers and preserving the old network's rules is insufficient. The proposal's 10 quadrillion atoms fit signed 64-bit integers but exceed JavaScript's exact integer range. Public amounts must continue using decimal strings or BigInt.

2. **Genesis spendability.** `ConnectBlock` explicitly skips the genesis transactions and leaves its coinbase unspendable. Merely putting 20 million allocation coins in a genesis coinbase would not create spendable allocations. The new network must specify and test how the allocation outputs enter the UTXO set. Reindex and snapshot restoration must produce the identical state. The laboratory's genesis exception must not silently change.

3. **Subsidy enforcement.** `GetBlockSubsidy` still uses the inherited schedule. The coinbase rule checks an upper limit of fees plus subsidy. It does not require treasury/team destinations or the candidate schedule. The production policy must define whether miners may forgo rewards, how unclaimed amounts affect actual supply, and whether reserved allocations are issued at genesis or later. Height-based issued-budget arithmetic is an upper bound if underpayment remains valid.

4. **Enforceable vesting.** The proposal currently models release at every block. Separate timelocked outputs for every installment would require 735,840 locked outputs plus the initial ecosystem output. Even the optimistic lower bound of 31 bytes per output exceeds 22.8 MB, before transaction overhead. The current block size and weight limits are both five million in their respective units; this cannot fit in one ordinary block. Actual script-locked outputs are larger. Ordinary multisig does not solve vesting.

5. **Beneficiaries and custody.** No production destination scripts, beneficiary public keys, allocation signers or cancellation authority have been supplied or established. Disposable test keys cannot be promoted into allocation keys. A release must bind the exact destination scripts into its reviewed genesis or allocation rules and demonstrate independent custody and recovery.

## A practical vesting option for review

Monthly height-based installments need 150 outputs per class plus one untimed launch output per class: 453 outputs in total under working draft 1 (169 under the earlier proposal). One model month is exactly 4,380 blocks. That supports ordinary timelocked scripts and keeps epoch-end totals aligned with the existing yearly model, but it changes the within-month release cadence. It is a proposed simplification, not an implemented or approved change to the paper. Cancellable unvested team grants also need a specifically reviewed script and authority model.

An exact continuous schedule instead needs an enforceable contract or a different issuance mechanism. It must prevent accelerated withdrawal, duplicated claims, lock removal through change outputs and additional issuance on reorgs. Choosing a new opcode or covenant solely to preserve an illustrative graph would substantially enlarge the consensus and wallet audit.

## Required acceptance

- Native money-range and RPC boundary cases at the old and proposed limits, including one-atom errors and JavaScript round trips.
- Every emission epoch and rounding remainder, zero subsidy at height zero, final reserve exhaustion and fee-only blocks afterward.
- Exact allocation scripts and amounts, early-spend rejection, boundary-height spends and insufficient-signature rejection.
- Supply recomputation from the UTXO set, including locked and immature outputs, fees, underpaid coinbases and unspendable outputs.
- Reorg across allocation and emission boundaries, restart, reindex and restore from independent snapshots with matching state.
- Independent consensus and custody review before public testnet claims or backer material describes these rules as enforced.

No supply cap, allocation key, reward, vesting rule or running network is changed by this audit.

## Native script evidence and reviewable monthly candidate

The actual laboratory node now passes `tests/vesting_smoke.py`: a time-locked two-of-three witness output rejects early spending and eleven invalid boundary/signature/bypass cases, accepts each valid signer pair, and preserves spend state through reorg, restart and reindex. See `validation/vesting-locks.json`. This demonstrates the existing script mechanism, not a production allocation. It does not implement team-grant cancellation.

Run `python3 protocol/economics/monthly_vesting.py` to inspect all 453 proposed installments. Every amount is serialized as an atom string, and the totals conserve exactly 20 million coins. For first eligible block H, the candidate sets the script lock to H minus one: transaction finality requires the containing block height to exceed nLockTime. An issuance transaction, coinbase maturity or missing signatures can delay actual spending further. The initial ecosystem amount means eligible at issuance, not a spendable genesis coinbase. Four tests cover every monthly boundary and yearly agreement with the continuous proposal. No beneficiary key or destination is selected and no transaction is created.

Script semantics reference: [BIP65](https://github.com/bitcoin/bips/blob/master/bip-0065.mediawiki). The candidate remains separate from the published continuous model until the cadence and custody rules are approved.
