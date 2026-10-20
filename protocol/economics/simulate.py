"""Proposal analysis only. Does not change NOET consensus or contact a network."""
import csv
import json
from pathlib import Path

from tokenomics import ALLOCATIONS, ALLOCATION_ATOMS, CAP, COIN, MINING, RELEASE_PERCENT, epoch_budget
from tokenomics import EPOCH_BLOCKS as BLOCKS_PER_YEAR  # one epoch is the model year

OUT = Path(__file__).parent / 'results'


def budget(remaining, rate=RELEASE_PERCENT):
    # Exact finite cap: assign final dust once the percentage rounds to zero.
    return epoch_budget(remaining, rate)


def block_reward(epoch_budget, offset):
    return ((offset + 1) * epoch_budget // BLOCKS_PER_YEAR
            - offset * epoch_budget // BLOCKS_PER_YEAR)


def allocation_unlocked(allocation, year):
    """Coins of one class unlocked at the end of a model year (12 vesting months)."""
    locked = (allocation['coins'] - allocation['initialCoins']) * COIN
    months = min(max(12 * year - allocation['cliffMonths'], 0), allocation['months'])
    return allocation['initialCoins'] * COIN + locked * months // allocation['months']


def schedule(rate=RELEASE_PERCENT, years=50):
    remaining = MINING
    rows = []
    for year in range(1, years + 1):
        emitted = budget(remaining, rate)
        remaining -= emitted
        mining = MINING - remaining
        unlocked = {a['name']: allocation_unlocked(a, year) for a in ALLOCATIONS}
        rows.append(dict(year=year, rate_percent=rate,
            mining_emission=emitted / COIN, cumulative_mining=mining / COIN,
            minted_supply=(ALLOCATION_ATOMS + mining) / COIN,
            unlocked_upper_bound=(mining + sum(unlocked.values())) / COIN,
            **{f'{name}_unlocked': atoms / COIN for name, atoms in unlocked.items()},
            reward_per_block=emitted / COIN / BLOCKS_PER_YEAR))
    return rows


def miner(year, workers, price, hourly_cost, utilization, paid_hour_price=1.2,
          availability=.95, usable_capacity=.85, worker_share=.9, pool_fee=.02):
    # Homogeneous, equally productive eligible workers. Availability/proof losses
    # cancel in relative subsidy share; target block cadence is assumed achieved.
    tokens = schedule(years=year)[-1]['mining_emission'] / 365 / workers * (1-pool_fee)
    paid = 24 * availability * usable_capacity * utilization * paid_hour_price * worker_share
    cost = 24 * hourly_cost
    return dict(year=year, workers=workers, token_price_usd=price,
        hourly_cost_usd=hourly_cost, utilization=utilization,
        tokens_per_day=tokens, paid_revenue_per_day_usd=paid,
        cost_per_day_usd=cost, subsidy_per_day_usd=tokens * price,
        profit_per_day_usd=tokens * price + paid - cost,
        break_even_token_price_usd=max(0, (cost-paid)/tokens),
        required_gross_paid_hour_price_usd=max(0, (cost-tokens*price) /
            (24*availability*usable_capacity*utilization*worker_share)) if utilization else None)


def write_csv(name, rows):
    with (OUT / name).open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def main():
    OUT.mkdir(exist_ok=True)
    annual = [row for rate in (10, 12.5, 15, 20) for row in schedule(rate)]
    scenarios = [miner(y,n,p,c,u) for y in (1,5,10,20)
        for n in (100,1000,10000) for p in (.01,.05,.1,.25,1)
        for c in (.18,.64,.94,1.84) for u in (0,.25,.65,.9)]
    fixed_demand = []
    for n in (100, 1000, 10000):
        for hours in (0, 1000, 10000):
            utilization = min(.9, hours / (n*24*.95*.85))
            row = miner(1,n,.1,.94,utilization)
            row['network_customer_gpu_hours_requested_per_day'] = hours
            row['network_customer_gpu_hours_served_per_day'] = n*24*.95*.85*utilization
            fixed_demand.append(row)
    write_csv('fixed-demand.csv', fixed_demand)
    write_csv('emissions.csv', annual)
    write_csv('miner-scenarios.csv', scenarios)
    summary = {'selected_emissions': [r for r in schedule() if r['year'] in (1,2,3,5,10,20,30,50)],
        'base_case': [miner(1,1000,.1,c,u) for c in (.18,.94) for u in (0,.65)],
        'schedule_comparison': [{ 'rate':r, 'first_year':schedule(r)[0]['mining_emission'],
            'year_5_cumulative':schedule(r)[4]['cumulative_mining'],
            'year_10_emission':schedule(r)[9]['mining_emission'],
            'year_20_emission':schedule(r)[19]['mining_emission']} for r in (10,15,20)],
        'scenarios':len(scenarios)}
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))

if __name__ == '__main__': main()
