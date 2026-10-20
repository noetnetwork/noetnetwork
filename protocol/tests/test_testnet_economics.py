import hashlib
import struct
import unittest
from testnet import economics
from testnet.prepare import GENESIS_NONCE, GENESIS_TIME
from testnet.allocations import outputs, script_number


class TestnetEconomicsTests(unittest.TestCase):
    def test_emission_matches_tokenomics(self):
        from tokenomics import RELEASE_PERCENT, epochs
        self.assertEqual(economics.EPOCHS, epochs())
        self.assertEqual(economics.subsidy(0), 0)
        year_one = economics.mining_issued(economics.EPOCH_BLOCKS)
        self.assertEqual(year_one, economics.MINING * RELEASE_PERCENT // 100)
        second = (economics.MINING - year_one) * RELEASE_PERCENT // 100
        self.assertEqual(economics.mining_issued(2 * economics.EPOCH_BLOCKS) - year_one, second)
        self.assertEqual(economics.mining_issued(len(epochs()) * economics.EPOCH_BLOCKS), economics.MINING)
        # Per-block rewards differ by at most one atom inside an epoch.
        rewards = {economics.subsidy(h) for h in range(1, 2000)}
        self.assertLessEqual(max(rewards) - min(rewards), 1)
        self.assertEqual(min(rewards), year_one // economics.EPOCH_BLOCKS)

    def test_allocations_conserve_twenty_million_with_locks(self):
        from testnet.allocations import OUTPUTS, TOTAL_ATOMS
        rows = outputs()
        self.assertEqual(len(rows), OUTPUTS)
        self.assertEqual(sum(int(r['amountAtoms']) for r in rows), TOTAL_ATOMS)
        self.assertEqual(TOTAL_ATOMS + economics.MINING, 100_000_000 * economics.COIN)
        self.assertTrue(all(r['scriptPubKey'].startswith('0020') and len(r['scriptPubKey']) == 68 for r in rows))
        # Working draft 1: 5M unlocked in block 1, then 15M over 150 monthly installments.
        initial = {r['allocation']: int(r['amountAtoms']) // economics.COIN for r in rows if r['cltvHeight'] is None}
        self.assertEqual(initial, {'team': 2_000_000, 'treasury': 1_000_000, 'ecosystem': 2_000_000})
        locked = [r for r in rows if r['cltvHeight'] is not None]
        self.assertTrue(all(r['witnessScript'].startswith(script_number(r['cltvHeight']).hex()) for r in locked))
        months = {}
        for r in locked:
            months[r['cltvHeight']] = months.get(r['cltvHeight'], 0) + int(r['amountAtoms'])
        self.assertEqual(sorted(months), [m * 4380 - 1 for m in range(1, 151)])
        self.assertTrue(all(abs(v - 100_000 * economics.COIN) <= 1 for v in months.values()))

    def test_genesis_nonce_meets_the_short_header_target(self):
        prefix = struct.pack('<I', 1) + bytes(32) + bytes.fromhex(
            '4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b')[::-1]
        header = prefix + struct.pack('<III', GENESIS_TIME, 0x207fffff, GENESIS_NONCE)
        short = hashlib.sha256(hashlib.sha256(header).digest()).digest()
        self.assertLess(int.from_bytes(short, 'little'), 0x7fffff << (8 * 29))

    def test_public_genesis_is_pinned_by_clients(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        for path in ('protocol/miner/noet_miner.py', 'web/src/wallet/core.ts'):
            if not (root / path).exists():  # the protocol-only export has no website
                continue
            self.assertIn('2da97e8dadb7fe27645263d3e732211cb67650adbc02a016102cc7390785cdaa', (root / path).read_text())

    def test_wallet_allocation_list_matches_block_one(self):
        import json
        from testnet.allocations import MANIFEST, manifest
        if not MANIFEST.exists():
            self.skipTest('website not present (protocol-only export)')
        self.assertEqual(json.loads(MANIFEST.read_text()), manifest(outputs()),
                         'Regenerate: python3 protocol/testnet/allocations.py --write-manifest')

    def test_allocation_signers_can_be_replaced_with_wallet_keys(self):
        import json
        import tempfile
        from pathlib import Path
        from testnet import set_allocation_keys as tool
        current = json.loads(tool.KEYS.read_text())['classes']
        rotated = {'treasury': current['team'], 'team': current['ecosystem'], 'ecosystem': current['treasury']}
        with tempfile.TemporaryDirectory() as folder:
            keys, listing = Path(folder) / 'keys.json', Path(folder) / 'web/list.json'
            listing.parent.mkdir()
            self.assertEqual(tool.write(rotated, keys, listing), {'signers': 9, 'outputs': 453})
            rows = json.loads(listing.read_text())['outputs']
            self.assertNotEqual(rows, json.loads(tool.MANIFEST.read_text())['outputs'] if tool.MANIFEST.exists() else [])
            self.assertEqual(sum(int(r['amountAtoms']) for r in rows), 20_000_000 * economics.COIN)
            for bad in ({**rotated, 'team': rotated['team'][:2]},
                        {**rotated, 'team': [rotated['team'][0]] * 3},
                        {**rotated, 'team': ['02' + 'ff' * 32] + rotated['team'][1:]},  # x >= p
                        {**rotated, 'team': ['04' + '11' * 32] + rotated['team'][1:]}):
                with self.subTest(bad=bad['team'][0][:6]), self.assertRaises(ValueError):
                    tool.write(bad, keys, listing)
        # A key whose x has no square root on the curve is rejected.
        off_curve = next(f'02{x:064x}' for x in range(5, 50)
                         if pow((x ** 3 + 7) % tool.P, (tool.P - 1) // 2, tool.P) == tool.P - 1)
        with self.assertRaises(ValueError):
            tool.checked_key(off_curve)

    def test_script_numbers_are_minimal(self):
        self.assertEqual(script_number(4379).hex(), '021b11')
        self.assertEqual(script_number(128).hex(), '028000')
        self.assertEqual(script_number(420479).hex(), '037f6a06')
        with self.assertRaises(ValueError):
            script_number(0)


class VaultAddressTests(unittest.TestCase):
    def test_bip173_p2wsh_vector(self):
        from testnet.allocations import segwit_v0_address
        program = bytes.fromhex('1863143c14c5166804bd19203356da136c985678cd4d27a1b8c6329604903262')
        self.assertEqual(segwit_v0_address('bc', program),
                         'bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3')

    def test_vault_addresses_decode_to_the_allocation_script(self):
        import json
        import tempfile
        from pathlib import Path
        from miner.noet_miner import payout_script
        from testnet.allocations import multisig, vault_addresses
        keys = {'ecosystem': ['02' + '11' * 32, '03' + '22' * 32, '02' + '33' * 32]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'keys.json'
            path.write_text(json.dumps({'classes': keys}))
            address = vault_addresses(path, 'noet')['ecosystem']
        self.assertTrue(address.startswith('noet1q'))
        self.assertEqual(payout_script(address, 'noet'),
                         bytes.fromhex('0020') + hashlib.sha256(multisig(keys['ecosystem'])).digest())

if __name__ == '__main__':
    unittest.main()
