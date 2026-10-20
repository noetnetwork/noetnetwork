import unittest
from monthly_vesting import COIN, MONTH_BLOCKS, installments, released, proposal


class MonthlyVestingTests(unittest.TestCase):
    def test_exact_allocation_and_rounding(self):
        rows = installments()
        self.assertEqual(len(rows), 3 + 3 * 150)
        self.assertEqual(sum(int(row['amountAtoms']) for row in rows), 20_000_000 * COIN)
        self.assertEqual(released(10**20), {'team': 8_000_000 * COIN,
                         'treasury': 8_000_000 * COIN, 'ecosystem': 4_000_000 * COIN})
        for category in ('treasury', 'team', 'ecosystem'):
            amounts = [int(row['amountAtoms']) for row in rows if row['allocation'] == category and row['installment']]
            self.assertLessEqual(max(amounts) - min(amounts), 1)

    def test_every_boundary_and_cliff(self):
        rows = installments()
        self.assertEqual(released(0), {'team': 2_000_000 * COIN, 'treasury': 1_000_000 * COIN,
                                       'ecosystem': 2_000_000 * COIN})
        for month in range(1, 151):
            height = month * MONTH_BLOCKS
            before, after = released(height - 1), released(height)
            for category in before:
                due = sum(int(row['amountAtoms']) for row in rows
                          if row['allocation'] == category and row['firstEligibleHeight'] == height)
                self.assertEqual(after[category] - before[category], due)
        self.assertEqual(released(MONTH_BLOCKS - 1)['team'], 2_000_000 * COIN)
        self.assertGreater(released(MONTH_BLOCKS)['team'], 2_000_000 * COIN)
        self.assertEqual(released(150 * MONTH_BLOCKS - 1)['team'], released(149 * MONTH_BLOCKS)['team'])
        for row in (r for r in rows if r['cltvHeight'] is not None):
            self.assertEqual(row['cltvHeight'] + 1, row['firstEligibleHeight'])

    def test_year_end_matches_continuous_proposal(self):
        # Working draft 1: 5M at launch, then 100,000 NOET a month for 150 months.
        for year in range(14):
            months = min(12 * year, 150)
            self.assertEqual(sum(released(year * 52_560).values()), (5_000_000 + 100_000 * months) * COIN)

    def test_unambiguous_json_and_invalid_height(self):
        result = proposal()
        self.assertFalse(result['activated'])
        self.assertFalse(result['cadenceApproved'])
        self.assertFalse(result['custodyConfigured'])
        self.assertIsInstance(result['allocationAtoms'], str)
        self.assertTrue(all(isinstance(row['amountAtoms'], str) for row in result['outputs']))
        for height in (-1, True, 1.5, '4380', None):
            with self.assertRaises(ValueError):
                released(height)


if __name__ == '__main__':
    unittest.main()
