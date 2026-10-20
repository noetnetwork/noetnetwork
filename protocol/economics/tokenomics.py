#!/usr/bin/env python3
"""The single source of NOET economics: tokenomics.json.

Python code imports the values from here. C++ and the website get generated
copies:

  python3 protocol/economics/tokenomics.py --write   # after editing tokenomics.json
  python3 protocol/economics/tokenomics.py --check   # exit 1 if a copy is stale

Changing the numbers changes consensus (block rewards, block-1 allocations and
the supply cap), so any edit means a new testnet genesis.
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'tokenomics.json'
POLICY = HERE / 'candidate/policy.h'
WEB = HERE.parents[1] / 'web/src/pages/mine/economics.generated.ts'
# Mainnet keeps its own release rate; the website shows the rate of the network it serves.
MAINNET = HERE.parent / 'mainnet/network.json'
BEGIN, END = '// BEGIN GENERATED', '// END GENERATED'


def basis_points(percent):
    """An exact rate in hundredths of a percent: 15 -> 1500, 12.5 -> 1250."""
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        raise TypeError('The release rate must be a number')
    value = Decimal(str(percent)) * 100
    if value != value.to_integral_value():
        raise ValueError('Use at most two decimal places for the release rate')
    return int(value)


def load(path=SOURCE):
    data = json.loads(Path(path).read_text())
    coin, cap = data['coinAtoms'], data['capCoins']
    mining, vesting = data['mining'], data['vesting']
    allocations = data['allocations']
    names = [a['name'] for a in allocations]
    problems = []
    if mining['coins'] + sum(a['coins'] for a in allocations) != cap:
        problems.append('mining plus allocations must equal the cap')
    try:
        if not 100 <= basis_points(mining['releasePercent']) <= 10000:
            raise ValueError
    except (ValueError, TypeError, ArithmeticError):
        problems.append('releasePercent must be between 1 and 100, in steps of 0.01')
    if mining['epochBlocks'] % vesting['monthBlocks']:
        problems.append('an epoch must be a whole number of vesting months')
    if len(set(names)) != len(names) or not all(re.fullmatch(r'[a-z][a-z0-9]{0,15}', n) for n in names):
        problems.append('allocation names must be unique, lowercase and short')
    for a in allocations:
        if not 0 <= a['initialCoins'] <= a['coins'] or a['months'] < 1 or a['cliffMonths'] < 0:
            problems.append(f'{a["name"]}: need 0 <= initialCoins <= coins, months >= 1, cliffMonths >= 0')
    if cap * coin >= 2**63:
        problems.append('the cap in atoms must fit a signed 64-bit amount')
    if problems:
        raise ValueError('; '.join(problems))
    return data


DATA = load()
COIN = DATA['coinAtoms']
CAP = DATA['capCoins'] * COIN
MINING = DATA['mining']['coins'] * COIN
EPOCH_BLOCKS = DATA['mining']['epochBlocks']
RELEASE_PERCENT = DATA['mining']['releasePercent']
RELEASE_BASIS_POINTS = basis_points(RELEASE_PERCENT)
MONTH_BLOCKS = DATA['vesting']['monthBlocks']
BLOCK_SECONDS = DATA['blockSeconds']
ALLOCATIONS = tuple(DATA['allocations'])
ALLOCATION_ATOMS = sum(a['coins'] for a in ALLOCATIONS) * COIN


def epoch_budget(remaining, percent=RELEASE_PERCENT):
    """Each epoch releases `percent` of what remains; the last dust is released whole."""
    return min(remaining, max(1, remaining * basis_points(percent) // 10000)) if remaining else 0


def epochs(percent=RELEASE_PERCENT):
    remaining, rows = MINING, []
    while remaining:
        budget = epoch_budget(remaining, percent)
        rows.append((MINING - remaining, budget))
        remaining -= budget
    return rows


def coins(atoms, places=0):
    whole, rest = divmod(atoms, COIN)
    text = f'{whole:,}'
    return text + (f'.{rest * 10**places // COIN:0{places}d}' if places else '')


def release_text(percent=RELEASE_PERCENT):
    days = EPOCH_BLOCKS * BLOCK_SECONDS / 86400
    period = 'each year' if days == 365 else f'every {days:g} days'
    return f'{percent:g}% of the remaining mining reserve {period}'


def policy_block(percent=RELEASE_PERCENT):
    rows = [f'    {{{a["coins"]} * COIN, {a["initialCoins"]} * COIN, {a["cliffMonths"]} * MONTH_BLOCKS, '
            f'{a["months"]} * MONTH_BLOCKS}},  // {a["name"]}' for a in ALLOCATIONS]
    return '\n'.join([
        f'{BEGIN} from protocol/economics/tokenomics.json by tokenomics.py --write',
        f'constexpr Amount CAP = {DATA["capCoins"]} * COIN;',
        f'constexpr Amount MINING = {DATA["mining"]["coins"]} * COIN;',
        f'constexpr std::uint64_t EPOCH_BLOCKS = {EPOCH_BLOCKS};',
        f'constexpr Amount RELEASE_BASIS_POINTS = {basis_points(percent)};  // {percent:g}%',
        f'constexpr std::uint64_t MONTH_BLOCKS = {MONTH_BLOCKS};',
        f'constexpr std::array<Allocation, {len(ALLOCATIONS)}> ALLOCATIONS{{{{',
        *rows,
        '}};',
        f'constexpr std::size_t EXPECTED_EPOCHS = {len(epochs(percent))};',
        END,
    ])


def web_economics(percent=RELEASE_PERCENT):
    first = epochs(percent)[0][1]
    return {
        'cap': coins(CAP), 'mined': coins(MINING), 'epochRelease': release_text(percent),
        'yearOneReward': coins(first // EPOCH_BLOCKS, 2), 'yearOneTotal': coins(first),
        'blockSeconds': BLOCK_SECONDS,
        # Exact integers (as strings) for the explorer's supply calculation.
        'capAtoms': str(CAP), 'miningAtoms': str(MINING), 'allocationAtoms': str(ALLOCATION_ATOMS),
        'epochBlocks': EPOCH_BLOCKS, 'releaseBasisPoints': basis_points(percent),
        'allocations': [{'name': a['name'], 'coins': f'{a["coins"]:,}', 'initialCoins': f'{a["initialCoins"]:,}',
                         'cliffMonths': a['cliffMonths'], 'months': a['months']} for a in ALLOCATIONS],
    }


def web_module():
    networks = {'test': web_economics()}
    if MAINNET.exists():
        networks['main'] = web_economics(json.loads(MAINNET.read_text())['releasePercent'])
    select = ' : '.join([f'ACTIVE_NETWORK.id === "{n}" ? ECONOMICS_BY_NETWORK.{n}' for n in networks if n != 'test']
                        + ['ECONOMICS_BY_NETWORK.test'])
    return ('// Generated by protocol/economics/tokenomics.py from tokenomics.json and the\n'
            '// mainnet release rate (protocol/mainnet/network.json). Do not edit by hand.\n'
            'import { ACTIVE_NETWORK } from "../../wallet/active";\n\n'
            f'export const ECONOMICS_BY_NETWORK = {json.dumps(networks, indent=2)} as const;\n\n'
            '// The economics of the network this build serves; other builds show the testnet\'s.\n'
            f'export const ECONOMICS = {select};\n')


def policy_text(percent=RELEASE_PERCENT):
    """The C++ policy with the given release rate (a network may override the shared rate)."""
    policy = POLICY.read_text()
    start, end = policy.index(BEGIN), policy.index(END) + len(END)
    return policy[:start] + policy_block(percent) + policy[end:]


def rendered():
    out = {POLICY: policy_text()}
    if WEB.parent.exists():
        out[WEB] = web_module()
    return out


def stale():
    return [path for path, text in rendered().items() if not path.exists() or path.read_text() != text]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--write', action='store_true')
    group.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        old = stale()
        for path in old:
            print(f'stale: {path}')
        sys.exit(1 if old else 0)
    for path, text in rendered().items():
        path.write_text(text)
    print(json.dumps({'cap': coins(CAP), 'mining': coins(MINING), 'epochs': len(epochs()),
                      'yearOneRewardPerBlock': coins(epochs()[0][1] // EPOCH_BLOCKS, 8),
                      'allocationOutputs': sum(a['months'] + (1 if a['initialCoins'] else 0) for a in ALLOCATIONS)}))
    print('Next: allocations.py --write-manifest, then rebuild the node and rerun the tests.', file=sys.stderr)
