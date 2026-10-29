import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('gateway_load', ROOT / 'analysis/gateway_load.py')
load = importlib.util.module_from_spec(spec)
spec.loader.exec_module(load)


class GatewayLoadTests(unittest.TestCase):
    def test_every_miner_leases_under_contention(self):
        result = load.run(miners=200, rounds=3, workers=32)
        self.assertEqual(result['leased'], 600)
        self.assertEqual(result['other'], 0)
        self.assertLessEqual(result['peakPerClient'], result['limits']['openPerClient'])

    def test_total_cap_never_overshoots_with_parallel_requests(self):
        # Nobody releases: the gateway must stop at its total, never above it.
        result = load.run(miners=300, rounds=1, node_ms=1, open_total=40, workers=48, release=False)
        self.assertEqual(result['leased'], 40)
        self.assertEqual(result['capacity'], 260)
        self.assertLessEqual(result['peakOpen'], 40)

    def test_per_client_cap_holds(self):
        result = load.run(miners=5, rounds=20, node_ms=1, workers=40, release=False)
        self.assertEqual(result['leased'], 5 * 8)
        self.assertEqual(result['limited'], 100 - 40)
        self.assertLessEqual(result['peakPerClient'], 8)

    def test_bech32_addresses_are_valid_for_the_gateway(self):
        self.assertEqual(len(load.bech32_digits(12345, 38)), 38)
        self.assertNotRegex(load.bech32_digits(10 ** 12, 38), '[1bio]')


if __name__ == '__main__':
    unittest.main()
