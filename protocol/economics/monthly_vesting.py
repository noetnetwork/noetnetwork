"""Unactivated monthly alternative to the paper's continuous allocation model.

Amounts are integer atoms, serialized as strings. No keys, addresses, transaction
creation, issuance or consensus changes are supplied by this proposal utility.
"""
import json

from tokenomics import ALLOCATIONS, COIN, MONTH_BLOCKS

# (name, atoms vesting monthly, months, cliff months); initial amounts are separate.
POLICIES = tuple((a['name'], (a['coins'] - a['initialCoins']) * COIN, a['months'], a['cliffMonths'])
                 for a in ALLOCATIONS)


def installments():
    rows = [{'allocation': a['name'], 'installment': 0, 'amountAtoms': str(a['initialCoins'] * COIN),
             'firstEligibleHeight': 0, 'cltvHeight': None} for a in ALLOCATIONS if a['initialCoins']]
    for allocation, total, months, cliff in POLICIES:
        for month in range(1, months + 1):
            height = (cliff + month) * MONTH_BLOCKS
            # A transaction with nLockTime L can be mined only after height L.
            # Bind H - 1 in the script for the proposed first eligible block H.
            rows.append({'allocation': allocation, 'installment': month,
                         'amountAtoms': str(total * month // months - total * (month - 1) // months),
                         'firstEligibleHeight': height, 'cltvHeight': height - 1})
    return sorted(rows, key=lambda row: (row['firstEligibleHeight'], row['allocation']))


def released(height):
    if type(height) is not int or height < 0:
        raise ValueError('Use a non-negative integer block height')
    result = dict.fromkeys((a['name'] for a in ALLOCATIONS), 0)
    for row in installments():
        if row['firstEligibleHeight'] <= height:
            result[row['allocation']] += int(row['amountAtoms'])
    return result


def proposal():
    rows = installments()
    return {'activated': False, 'cadenceApproved': False, 'custodyConfigured': False,
            'description': 'Monthly alternative for review; not deployed tokenomics',
            'monthBlocks': MONTH_BLOCKS, 'allocationAtoms': str(sum(int(row['amountAtoms']) for row in rows)),
            'outputCount': len(rows), 'outputs': rows,
            'limitations': ['Issuance rules and beneficiary scripts are not supplied.',
                           'Initial ecosystem release means eligibility at issuance, not a spendable genesis coinbase.',
                           'Coinbase maturity and actual issuance height can delay spending beyond eligibility.',
                           'Cancellable team grants require a separately reviewed design.',
                           'Monthly cadence changes within-month release timing from the paper.']}


if __name__ == '__main__':
    print(json.dumps(proposal(), indent=2))
