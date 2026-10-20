"""tokenomics.json is the one place the economics live; every copy follows it."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'economics'))
import tokenomics  # noqa: E402
import monthly_vesting  # noqa: E402


class TokenomicsTests(unittest.TestCase):
    def test_generated_copies_are_current(self):
        self.assertEqual(tokenomics.stale(), [], 'Run: python3 protocol/economics/tokenomics.py --write')

    def test_python_consumers_share_the_numbers(self):
        from testnet import allocations, economics
        self.assertEqual(economics.MINING, tokenomics.MINING)
        self.assertEqual(economics.EPOCHS, tokenomics.epochs())
        rows = monthly_vesting.installments()
        self.assertEqual(sum(int(r['amountAtoms']) for r in rows), tokenomics.ALLOCATION_ATOMS)
        self.assertEqual(len(rows), allocations.OUTPUTS)
        self.assertEqual(monthly_vesting.released(10**12),
                         {a['name']: a['coins'] * tokenomics.COIN for a in tokenomics.ALLOCATIONS})

    def test_inconsistent_numbers_are_refused(self):
        base = json.loads(tokenomics.SOURCE.read_text())
        bad = []
        changed = copy.deepcopy(base); changed['mining']['coins'] += 1; bad.append(changed)  # cap no longer adds up
        changed = copy.deepcopy(base); changed['mining']['releasePercent'] = 0; bad.append(changed)
        changed = copy.deepcopy(base); changed['mining']['releasePercent'] = 12.345; bad.append(changed)
        changed = copy.deepcopy(base); changed['mining']['releasePercent'] = '15'; bad.append(changed)
        changed = copy.deepcopy(base); changed['vesting']['monthBlocks'] = 4000; bad.append(changed)
        changed = copy.deepcopy(base); changed['allocations'][0]['initialCoins'] = 10**9; bad.append(changed)
        changed = copy.deepcopy(base); changed['allocations'][1]['name'] = changed['allocations'][0]['name']; bad.append(changed)
        with tempfile.TemporaryDirectory() as folder:
            for number, data in enumerate(bad):
                path = Path(folder) / f'{number}.json'
                path.write_text(json.dumps(data))
                with self.subTest(case=number), self.assertRaises(ValueError):
                    tokenomics.load(path)

    def test_a_what_if_change_is_accepted(self):
        data = json.loads(tokenomics.SOURCE.read_text())
        data['mining']['releasePercent'] = 10
        data['allocations'][1]['coins'] -= 1_000_000
        data['mining']['coins'] += 1_000_000
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'what-if.json'
            path.write_text(json.dumps(data))
            self.assertEqual(tokenomics.load(path)['mining']['releasePercent'], 10)

    def test_fractional_rates_are_exact(self):
        self.assertEqual(tokenomics.basis_points(15), 1500)
        self.assertEqual(tokenomics.basis_points(12.5), 1250)
        self.assertEqual(tokenomics.epoch_budget(tokenomics.MINING, 12.5), 10_000_000 * tokenomics.COIN)
        # Basis points give exactly the old whole-percent arithmetic.
        for remaining in (tokenomics.MINING, 123_456_789_012_345, 9_999, 1):
            self.assertEqual(tokenomics.epoch_budget(remaining, 15), min(remaining, max(1, remaining * 15 // 100)))

    def test_mine_page_shows_the_generated_reward(self):
        if not tokenomics.WEB.exists():
            self.skipTest('website not present (protocol-only export)')
        first = tokenomics.epochs()[0][1]
        self.assertIn(f'"yearOneReward": "{tokenomics.coins(first // tokenomics.EPOCH_BLOCKS, 2)}"',
                      tokenomics.WEB.read_text())


if __name__ == '__main__':
    unittest.main()
