# noet-testnet-1

Public test network with the NOET economics at a 15% release rate. It ran from
13 to 16 October 2026 (1,102 blocks) and was then archived and shut down; NOET
Mainnet (`../mainnet/README.md`) runs the same consensus code. Test coins had no
value.

| | |
|---|---|
| Genesis | `2da97e8dadb7fe27645263d3e732211cb67650adbc02a016102cc7390785cdaa` (time 1791763200, nonce 4) |
| Addresses | `tnoet1…` (bech32), base58 prefixes 0x41 / 0x81 / 0xC1 |
| P2P port, magic | 33441, `4e 54 53 31` |
| Model | `Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca`, normalizer 1,000,000 |
| Supply cap | 100,000,000 NOET |
| Mining | 80,000,000 NOET; each 52,560-block epoch releases 15% of the remaining reserve, spread evenly per block (228.31050228 NOET per block in year one) |
| Difficulty | ASERT from height 1,500: the target halves or doubles for every 12 hours the chain runs ahead of or behind 600-second blocks, starting from the minimum difficulty. Heights 1–1,499 ran without retargeting (the inherited regtest rule), about one block every 47 s; `asert_check.py` checks the rule on a real node |
| Allocations | 20,000,000 NOET in block 1: 453 P2WSH outputs, 2-of-3 multisig (team, treasury, ecosystem). 5,000,000 untimed at launch, then 150 time-locked monthly installments of 100,000 NOET in aggregate |
| Useful work | Each mining attempt answers a public request queued at the gateway (`POST /tasks`, or www.noet.network/#ask), else the next item of the open dataset (`noet/open_dataset.py`); the miner returns the answer (`POST /mining/answer`) and answers are public (`GET /tasks`). Experimental: the 0.6B model at the proof's fixed sampling (temperature 1.0) often answers wrongly. Block 830 was the first mined this way |

The allocation signers in `allocation-keys.json` are **testnet-only keys**.
A network that carries value must use keys held by the allocation owners.
To hand allocations to real signers, each signer copies "Your cosigner key"
from the wallet's Shared vaults panel; then, before launch:

    python3 protocol/testnet/set_allocation_keys.py --treasury K,K,K --team K,K,K --ecosystem K,K,K

This rewrites the public keys and the wallet's allocation list
(`web/public/allocations/noet-testnet-1.json`). Rebuild the node image and
rerun the integration test afterwards. Signers then see their installments
under "Allocations you co-sign" and claim each one once its block height is
reached.

## Files

- `economics.py` — independent Python emission schedule (compared against the C++ policy).
- `allocations.py` — exact block-1 outputs and the generated C++ table.
- `prepare.py` — the source overlay. `release` variant: real external
  verification only, proof formats active from height 1. `test` variant: mock
  validation and laboratory proof heights, for local consensus tests only;
  economics and identity are identical.

## Verified on a real node (12 October 2026)

Test variant (`web/src/wallet/testnet.integration.test.ts`):

- Block 1 pays all 453 allocation outputs (working draft 1) exactly, in order,
  and the miner the scheduled subsidy; blocks 2, 50 and 102 pay the subsidy from
  `economics.py`. Rerun on a fresh build the same evening; the earlier
  169-output schedule passed the same checks.
- UTXO supply equals mined subsidies plus 20,000,000 NOET.
- An untimed launch grant is spent with two of its three keys through the
  wallet vault code.
- The first installment (height 4,379) is refused as `non-final` one block
  early; a transaction whose lock time is below the script's height fails
  `OP_CHECKLOCKTIMEVERIFY`; at height 4,379 two keyholders claim it.
- A node whose block assembly omits the allocations cannot produce block 1:
  validation rejects it with `bad-cb-allocation`.

ASERT (13 October 2026, `asert_check.py`): a test-variant node built with this
overlay, ASERT from height 20, mined 280 blocks on a mocked clock (140 at 60 s,
then 120 at 3,000 s). Every block's nBits matched the independent Python port
of upstream's aserti3-2d; difficulty rose 3.4x over the fast blocks (1.75
half-lives ahead) and eased back to the minimum over the slow ones.

Release variant: refuses `-validationapi=mock` or `-useextapi=0` at startup and
starts with real external verification on the same genesis.
