import unittest
from simulate import COIN, MINING, CAP, BLOCKS_PER_YEAR, budget, block_reward, schedule, miner

class EconomicsTests(unittest.TestCase):
    def test_allocations(self):
        self.assertEqual(MINING + (8_000_000+8_000_000+4_000_000)*COIN, CAP)

    def test_integer_conservation_and_final_dust(self):
        remaining, total, epochs = MINING, 0, 0
        while remaining:
            b = budget(remaining)
            self.assertGreater(b, 0)
            self.assertLessEqual(b, remaining)
            total += b
            remaining -= b
            epochs += 1
            self.assertLess(epochs, 1000)
        self.assertEqual(total, MINING)
        self.assertEqual(budget(0), 0)

    def test_epoch_distribution_and_boundary(self):
        for b in (budget(MINING), budget(MINING-budget(MINING)), 1, 6):
            rewards = [block_reward(b,i) for i in range(BLOCKS_PER_YEAR)]
            self.assertEqual(sum(rewards), b)
            self.assertLessEqual(max(rewards)-min(rewards), 1)
            self.assertGreaterEqual(min(rewards), 0)

    def test_vesting(self):
        rows = schedule()
        # Working draft 1: 5M at launch, then 100,000 a month for 150 months.
        unlocked = lambda r: r['team_unlocked'] + r['treasury_unlocked'] + r['ecosystem_unlocked']
        self.assertEqual(unlocked(rows[0]), 6_200_000)
        self.assertEqual(unlocked(rows[4]), 11_000_000)
        self.assertEqual(rows[0]['team_unlocked'], 2_000_000 + 12 * 40_000)
        self.assertLess(unlocked(rows[11]), 20_000_000)
        self.assertEqual(unlocked(rows[12]), 20_000_000)
        for row in rows:
            self.assertLessEqual(row['unlocked_upper_bound'], row['minted_supply'])
            self.assertLessEqual(row['minted_supply'], CAP/COIN)

    def test_independent_financial_examples(self):
        m = miner(1,1000,.1,.94,0)
        self.assertAlmostEqual(m['tokens_per_day'], 12_000_000/365/1000*.98)
        self.assertAlmostEqual(m['profit_per_day_usd'], 12_000_000/365/1000*.98*.1-22.56)
        m = miner(1,1000,.1,.94,.65)
        self.assertAlmostEqual(m['paid_revenue_per_day_usd'], 13.60476)
        p = m['break_even_token_price_usd']
        self.assertAlmostEqual(miner(1,1000,p,.94,.65)['profit_per_day_usd'],0)

    def test_sensitivity(self):
        a=miner(1,1000,.1,.94,0); b=miner(1,10000,.1,.94,0)
        self.assertAlmostEqual(a['tokens_per_day']/10,b['tokens_per_day'])
        self.assertLess(miner(10,1000,.1,.94,0)['tokens_per_day'],a['tokens_per_day'])

if __name__ == '__main__': unittest.main()
