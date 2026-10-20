# NOET economics proposal

11 October 2026; allocations and vesting updated 12 October 2026 to *Tokenomics, working draft 1*, whose figures NOET Mainnet uses with a 12.5% release rate (`../mainnet/network.json`). Not a price forecast.

## Changing the numbers

`tokenomics.json` is the single source for the cap, mining reserve, epoch
release rate, block time and every allocation class (amount, initial unlock,
cliff and duration in months). Python reads it directly; the C++ policy and the
website get generated copies:

    python3 protocol/economics/tokenomics.py --write        # regenerate policy.h and the Mine page values
    python3 protocol/testnet/allocations.py --write-manifest
    python3 protocol/economics/check_candidate.py --out /tmp/check.json   # C++ vs Python, with sanitizers

`tests/test_tokenomics.py` fails while a copy is stale. A change alters block
rewards or block 1, so it needs a new testnet genesis. Adding or renaming an
allocation class also needs signer keys for it (`testnet/set_allocation_keys.py`).
The tables below describe the current proposal; `simulate.py` regenerates them.

## Recommendation

Use a maximum issuance of **100,000,000 NOET**, eight decimal places, with **80% earned through mining**. The denomination is chosen for readability; 100 million coins has no inherent economic advantage over 10 million coins priced ten times higher. The monetary budget, distribution, work security and actual customer demand matter.

| Allocation | NOET | Share | Release |
|---|---:|---:|---|
| Useful-work miners | 80,000,000 | 80% | Newly issued over the emission schedule below |
| Team and founders | 8,000,000 | 8% | 2,000,000 at launch; 6,000,000 over 150 monthly installments (40,000 a month) |
| Protocol treasury | 8,000,000 | 8% | 1,000,000 at launch; 7,000,000 over 150 monthly installments (about 46,667 a month) |
| Ecosystem grants | 4,000,000 | 4% | 2,000,000 at launch; 2,000,000 over 150 monthly installments (about 13,333 a month) |
| Private sale / investor reserve | 0 | 0% | No separate allocation in this proposal |

20 million non-mining coins are minted into chain-enforced vesting outputs in block 1. 5,000,000 are unlocked at launch; the other 15,000,000 release at 100,000 NOET a month in aggregate, the first installment one month (4,380 blocks) after launch and the last after 150 months (block 656,999, about 12.5 model years). There is no cliff. Installment amounts are whole atoms: each class's total is split by cumulative floor, so a month's aggregate differs from 100,000 NOET by at most one atom and every class total is exact. The testnet implements these outputs; they have not been independently audited. Do not describe ordinary multisig custody as enforceable vesting. Mining begins with the first post-genesis block. Existing lab balances have no conversion rights.

Treasury and grants should fund audits, independent verification, open-source maintenance, integrations and measurable customer adoption. Publish recipients, addresses, expenses and unlocks quarterly. Use at least a 3-of-5 multisig with independent signers for unlocked treasury funds and delayed material transfers. Team grants require per-recipient disclosure and enforceable unvested cancellation terms. These controls require implementation and legal review, not just this document.

No guaranteed investor allocation, exchange listing, token-backed yield or protocol buyback. If financing is later needed, negotiate from existing disclosed allocations or equity rather than silently expanding supply. Launch grants are not a promise of market liquidity: a pool also needs real external capital. The initial float is small and may produce large price swings; do not infer valuation from thin trades.

## Emissions

**Decision, 13 October 2026: mainnet releases 12.5% of the remaining reserve per epoch** (190.25875190 NOET per block in year one, a reward half-life of about 5.2 model years, 271 epochs until the reserve is exhausted). It is set in `mainnet/network.json`; the testnet keeps the 15% in `tokenomics.json`, and the tables below describe 15% unless they say otherwise.

The original proposal: target **15% of the remaining 80 million mining reserve per 52,560-block epoch**, with the existing nominal 600-second block interval retained for this model. That is approximately one year at target cadence, not a wall-clock promise. It creates a 15% annual reduction in the reward, not 15% inflation of total supply. Effective reward half-life is about 4.27 model years. Faster blocks are not assumed before proof verification and propagation benchmarks.

Let R be remaining mining atoms (1 NOET = 100,000,000 atoms). Each epoch budget B is `min(R, max(1, floor(15*R/100)))` when R is positive, otherwise zero. For block offset i from 0 through 52,559, subsidy is `floor((i+1)*B/52560) - floor(i*B/52560)`. This distributes the whole integer budget with at most one atom difference between blocks. After an epoch, subtract B from R. The final dust rule eventually exhausts the reserve exactly; no tail issuance above the cap. Block 1 uses epoch 0, offset 0. Protocol implementation must enforce canonical height and exact cap arithmetic through reorgs.

| End of model year | Mining issued that year | Total minted, including locked coins | Unlocked upper bound |
|---:|---:|---:|---:|
| 1 | 12.000m | 32.000m | 18.200m |
| 2 | 10.200m | 42.200m | 29.600m |
| 3 | 8.670m | 50.870m | 39.470m |
| 5 | 6.264m | 64.504m | 55.504m |
| 8 | 3.847m | 78.201m | 72.801m |
| 10 | 2.779m | 84.250m | 81.250m |
| 12 | 2.008m | 88.621m | 88.021m |
| 13 | 1.707m | 90.328m | 90.328m |
| 20 | 0.547m | 96.899m | 96.899m |
| 30 | 0.108m | 99.390m | 99.390m |

Year-two mining issuance equals 31.875% of the end-of-year-one minted supply. The unlocked upper bound rises from 5m at launch to 18.2m after year one and 29.6m after year two as mining and vesting overlap. A declining block reward does not mean low early dilution. All non-mining coins are unlocked during year 13.

These are not circulating-supply forecasts. Unspent treasury balances, lost coins and miner reward maturity reduce actual availability. The upper bound ignores the last 100 blocks of immature subsidy. Recommend retaining at least the existing 100-block maturity (about 16.7 hours at target), subject to reorg testing. Vesting should use block heights corresponding to these model durations and disclose that actual elapsed time varies.

Four alternatives were simulated with identical allocations (the rate may have up to two decimals; it is applied in basis points):

| Annual remaining-reserve release | First-year mining | Mined by year 5 | Year-20 mining |
|---|---:|---:|---:|
| 10% | 8.000m | 32.761m | 1.081m |
| 12.5% | 10.000m | 38.967m | 0.791m |
| **15%** | **12.000m** | **44.504m** | **0.547m** |
| 20% | 16.000m | 53.786m | 0.231m |

15% is a provisional tradeoff: more bootstrap funding than 10%, less early dilution and more later subsidy than 20%. It is not a mathematically proven optimum. If fees and customer demand develop slowly, 10% may be preferable. A hard cap ultimately requires fee-funded security; it cannot simultaneously guarantee a permanent subsidy. At year 20, subsidy is only 4.56% of year one's level. Do not treat token-price appreciation as the security plan.

## Miner model

We simulated 960 combinations: years 1/5/10/20; 100/1,000/10,000 equally productive workers; NOET prices $0.01/$0.05/$0.10/$0.25/$1; hourly all-in costs $0.18/$0.64/$0.94/$1.84; paid utilization 0/25/65/90%. Prices are stress inputs, not forecasts. At $0.10, fully diluted value would be $10m; that does not mean there is $10m of liquidity to fund sellers.

Public Runpod rental quotes observed 11 October 2026: RTX 4090 $0.89/hour, A40 $0.59/hour, A100 PCIe $1.79/hour. Rental scenarios add an assumed $0.05/hour overhead. These are cost sensitivities, not claims those different GPUs earn equal rewards. Owned equipment at $0.18/hour assumes $0.07 power/cooling + $0.08 depreciation + $0.03 maintenance/network. For example $0.08/hour depreciation corresponds to $2,102 over three continuously running years. Regional electricity, hardware cost, financing and resale can change this substantially. Taxes are excluded.

Base paid-work assumptions: 95% availability, 15% throughput lost to proof/operating overhead, 65% utilization of remaining capacity, $1.20 per billable equivalent GPU-hour, 90% paid to the worker. The other 10% is gross service revenue to cover verification, routing, disputes and operations, not profit. Pool fee of 2% applies only to mining subsidy. No transaction fee income is assumed.

Daily net = (annual mining issuance / 365 / equal-worker count * 0.98 * token price) + (24 * 0.95 * 0.85 * paid utilization * customer hourly price * 0.90) - (24 * all-in hourly cost).

The network count is active, equally productive eligible workers; it is not registered accounts or GPU model names. Equal availability/proof losses cancel in relative subsidy shares. The model assumes difficulty keeps the block cadence on target and sufficient valid work exists. If not, daily emissions also fall. A worker twice as productive must be modeled by work share, not one equal slot. This is expected pool-style revenue; individual solo outcomes vary.

At year 1, 1,000 workers and $0.10/NOET:

| Setup | Paid utilization | Subsidy/day | Paid revenue/day | Cost/day | Net/day | Break-even token price |
|---|---:|---:|---:|---:|---:|---:|
| Owned, assumed | 0% | $3.22 | $0 | $4.32 | -$1.10 | $0.134 |
| Owned, assumed | 65% | $3.22 | $13.60 | $4.32 | +$12.51 | $0 |
| Rental, assumed | 0% | $3.22 | $0 | $22.56 | -$19.34 | $0.700 |
| Rental, assumed | 65% | $3.22 | $13.60 | $22.56 | -$5.73 | $0.278 |

Zero break-even token price means paid-work revenue covers the modeled cost, not risk-free profit. The paid workload and rate are unproven assumptions. At 65% utilization the rental needs about **$1.71 per paid GPU-hour** to break even at $0.10/NOET. Without any subsidy it needs about **$1.99/hour**. Whether customers accept that rate must be tested against real inference throughput, quality and alternatives. Do not equate GPU-hours with customer token throughput.

At zero paid demand and $0.10/NOET the total year-one net mining payout is about $3,222/day. It covers only about **143 rental workers** at $22.56/day, or **746 owned workers** at $4.32/day, before extra independent verifier/security costs. These are approximate zero-profit capacities, not safe network sizes or admission caps. Ten times the equally productive miners divides each subsidy by ten. At constant cost and no fees, year-20 supported capacity falls by about 95.4% unless token price changes.

The 65% utilization scenario at 1,000 workers requires **12,597 paid equivalent GPU-hours/day**, or about **$15,116/day customer spending**. Its 10% service share is about $1,512/day to cover the entire verification and service layer. Full redundant inference verification could exhaust that budget. Measure it before fixing the commercial split. Do not advertise that every new miner gets 65% demand.

`fixed-demand.csv` holds demand constant instead: at 1,000 paid GPU-hours/day, $1.20/hour and 1,000 workers, paid revenue is only $1.08/worker/day; rental net at $0.10/NOET is about **-$18.26/day**. With 10,000 workers, paid revenue drops to $0.108 and subsidy to $0.322/day. This avoids the false assumption that customer demand automatically scales with miner entry.

Combined stress: an 80% token-price fall from $0.10 to $0.02, entry from 1,000 to 2,000 workers and only 1,000 paid network GPU-hours/day produces about $0.322 subsidy plus $0.54 paid revenue per worker per day, against $22.56 rental cost: **-$21.70/day**. A hard cap cannot prevent this outcome.

At 1,000 equal solo miners and 144 blocks/day, mean block wait is 6.94 days; approximately 86.6% of days have no win under a Poisson approximation. Pools smooth payouts but add custody, concentration and operator risks. Expected subsidy is not guaranteed daily cash. Sell pressure, slippage, orphan rates, hardware failures, heterogeneous performance and strategic entry/exit are not modeled dynamically.

## Token purpose and reward security

NOET should pay native transaction fees and support native compute settlement. Let customers see stable dollar quotes and pay through familiar app billing, with explicit conversion/settlement if NOET is used underneath. Paying miners from customer fees is a commercial settlement function that still needs implementation. Do not claim existing OpenAI-backed portal revenue already flows to native miners.

Recommend 100% of mining subsidy and ordinary chain fees to valid block producers; no additional treasury block tax. Paid customer work is separately priced. The same job may qualify as both delivered service and consensus work only if the proof system securely binds job, model, input, result and chain challenge. The model assumes that coexistence is possible; it is not yet demonstrated. Otherwise throughput and costs must be recomputed.

Do not reward self-reported FLOPs, uptime alone, unverified outputs, or account count. Invalid/replayed work earns nothing. Self-purchased jobs must not unlock additional subsidies, grants or fee rebates beyond genuine consensus work; otherwise wash demand becomes profitable. Work selection and reward eligibility must prevent cheap tasks, copying, withheld outputs, GPU-specific shortcuts or request spam from buying disproportionate block probability. Paid demand is not itself proof of consensus security.

Do not add staking yield or burn fees at launch. Subsidies already dilute holders; fee burning would remove funds potentially needed for security. No mandatory token holding threshold to start mining beyond real anti-abuse costs. Governance must not permit an administrator to mint above the cap. Verifiers need measured sustainable funding; running a verifier does not automatically qualify for a new reward allocation.

## Conditions before approval

1. Benchmark real proof generation and independent full verification on eligible models and hardware. Measure joules, accepted proofs, throughput loss, stale work and p50/p95 latency. Mock lab blocks establish none of these.
2. Run paid customer pilots. Establish a defensible price, measured demand and the true verification/support margin. Test a token-price drop of 80%, doubled miner entry and falling utilization.
3. Audit useful-work consensus against cheating, grinding, replay and purchased/rented majority capacity. Dollar reward coverage is not an attack-cost analysis.
4. Implement exact subsidy/cap boundaries and vesting consensus tests, including multi-node agreement, reorgs and integer overflow. Current upstream `MAX_MONEY` is 21m: a 100m proposal requires a deliberate full monetary-range audit, not changing one constant. Existing lab rules remain untouched.
5. Publish supply, treasury and vesting dashboards, and independent wallet/consensus review results. Secure fiat operating runway; a treasury of unsold tokens is not cash runway.
6. Review allocations and commercial settlement legally in intended launch jurisdictions. This proposal does not establish token classification or permission to sell it.

Recommendation: approve this as a **working design for testnet economics**, then reconsider the 10% versus 15% schedule after real-work and customer benchmarks. Do not announce mainnet profitability, guaranteed rewards or a launch valuation.

## Reproduce and inspect

Run `python3 protocol/economics/simulate.py` and `python3 -m unittest discover -s protocol/economics -v`. Standard library only for calculations. `plot.py` optionally needs matplotlib and produces PNG/SVG charts. The calculation uses integer atoms for emissions; float dollar outputs are illustrative economics. Tests cover allocation totals, finite exact cap, per-block rounding, vesting boundaries, independent profit examples and basic sensitivity. They are model tests, not production consensus tests.

Files: `results/emissions.csv` (three 50-year schedules), `results/miner-scenarios.csv` (960 cases), `results/fixed-demand.csv` (nine demand-constrained cases), `results/summary.json` and `results/economics.png`.

## Sources and provenance

- [Runpod GPU pricing](https://www.runpod.io/pricing), observed 11 October 2026. Rental prices are not bookings or mining-workload permission.
- [Akash provider calculator](https://akash.network/pricing/provider-calculator/), for comparison with a demand-priced compute market. No Akash returns assumed here.
- [Bittensor emissions documentation](https://www.bittensor.com/docs/concepts/emissions), comparison with a capped emission system. NOET does not inherit Bittensor security or economics.
- Local pinned open-source core: `src/validation.cpp` GetBlockSubsidy, `src/consensus/amount.h` MAX_MONEY, `src/consensus/consensus.h` maturity and `src/kernel/chainparams.cpp` laboratory parameters. Current lab enables the inherited experimental subsidy; this proposal does not change it.


An unactivated C++ arithmetic candidate and exact-height acceptance are now available in [candidate/README.md](candidate/README.md). This does not implement live consensus or vesting-output spend restrictions.
