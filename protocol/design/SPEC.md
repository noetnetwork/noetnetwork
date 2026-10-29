# Block validity, noet-mainnet-1

Specification version 0.1, 29 October 2026. It states what a node checks
before a block connects, and where each rule lives. Where a rule is inherited
from the pinned upstream and not changed by NOET, it says so and points to the
upstream document instead of restating it. This is a description of the code,
not a second implementation: when they differ, the code is right and this
file is wrong, and the difference is a bug in this file.

Version rule: raise the minor version for a clarification, the major version
for any change to what is valid. Every consensus change gets a new major
version with its activation height (see `GOVERNANCE.md`).

## 1. Parameters

| Parameter | Value | Source |
|---|---|---|
| Profile | `noet-mainnet-1`, node chain `tensor-reg` | `mainnet/network.json`, `deploy/fly/noet_node/supervise.py` |
| Genesis | `fc3e7390…5a54`, time 1791849600, nonce 1 | `mainnet/network.json` |
| Addresses | bech32 `noet1…`; base58 prefixes 0x3C, 0x7C, 0xBC | `mainnet/README.md` |
| P2P | port 33440, magic `4e 4d 4e 31` | `mainnet/network.json` |
| Block target | 600 s, ASERT from height 2 with a 12-hour half-life | `mainnet/README.md`, `testnet/asert_check.py` |
| Subsidy | 12.5% of the remaining mining reserve per epoch of 52,560 blocks | `network.json` `releasePercent`, `economics/tokenomics.json` |
| Supply cap | 100,000,000 NOET (10^16 atoms) | `economics/tokenomics.json` |
| Coinbase maturity | 100 blocks | inherited |
| Model, heights 1 to 3,499 | `Qwen/Qwen3-0.6B@c1899de2…`, difficulty normaliser 1,000,000 | node model registry, `miner/noet_miner.py` |
| Model, from height 3,500 | `Qwen/Qwen3-14B@40c06982…`, one-off target reset [2, 1] | `network.json` `modelSwitch`, `design/MODEL-SWITCH.md` |

## 2. Structure

A block is a header and transactions as in Bitcoin, plus a useful-work proof
(at most 2 MiB). Transaction rules, scripts, segregated witness and the
coinbase format are inherited unchanged.

Block 1 carries the allocation outputs with their vesting locks, fixed by
`economics/tokenomics.json` and the allocation keys
(`mainnet/allocation-keys.json`). See `WHITEPAPER.md` section 5.2.

## 3. Proof binding (Quick checks, node)

The node runs these on every submitted block before any GPU work. A failure
rejects the block outright.

1. **Envelope.** The proof parses and respects its size limits.
2. **Chain position.** The proof's 76-byte header prefix equals the block's
   header prefix, and its parent hash equals the block's parent.
3. **Payout.** The header's merkle root commits to the coinbase, so the proof
   is bound to the reward script the template paid.
4. **Time-lock.** A chiavdf proof over the parent hash (1024-bit
   discriminant, 10,000 iterations) verifies.
5. **Target.** The header hash meets the compact target, with the upstream
   evidence-pricing factor and the model's normaliser (upstream
   `docs/proof-v4.md`). Block 3,500 takes once `floor(T(3,499) * 2 / 1)`
   and ASERT continues from a fresh anchor there (`targetScale` [2, 1],
   phase C in `MODEL-SWITCH.md`).
6. **Model.** The proof names the model the height requires, by name and
   immutable revision. A 0.6B proof at or above 3,500, or a 14B proof below
   it, is invalid (`bad-model-for-height`, `validation.cpp`
   `CheckBlockModelRegistered`). Challenges against either scheduled model
   resolve as failed, so neither can be banned (`MODEL-SWITCH.md`).
7. **Prompt copying.** A proof whose output copies its prompt is refused at
   submit by the anti-parrot rule (`bad-prompt-scaffold`).

## 4. Replay (Full verdict, verifier)

The node asks an external verifier over the mutual-TLS relay and connects the
block only on an authenticated **Full GREEN**. The verifier:

- loads the approved weights from a read-only, hash-checked snapshot;
- replays the generated tokens with an fp32 reference forward pass and
  compares the miner's reported logits in sigma units, capped at 12 sigma;
- runs the candidate-support, candidate-band and detector rules of the
  ruleset the node names for that height (upstream `docs/proof-v4.md`,
  `docs/detector-rule.md`);
- answers GREEN, AMBER or RED, or abstains with no verdict when its own
  evidence is incomplete.

AMBER, RED and abstention all leave the block unconnected. Verdicts carry an
HMAC with the secret the node and verifier share.

## 5. History rules (upstream)

From the history-enforcement height, a v4 proof that reuses a prompt or a
protected 64-token continuation slice is invalid, checked against the
block's own branch (upstream `docs/proof-v4.md`).

## 6. What this does not yet specify

- The exact byte layout of the proof envelope and commitment fields: they are
  upstream's and documented there.
- Mempool and relay policy, which are not consensus (`FEES.md`).
- Fork choice. It is most cumulative work, inherited. Reorganisation limits
  are not enforced in consensus; the monitor alerts on any replaced block.
- Test vectors. A conformance suite (see `GOVERNANCE.md`, "Engine builds")
  should ship valid and invalid blocks for every numbered rule above.
