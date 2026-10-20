"""The payout-proof overlay applies cleanly to the pinned node source."""
from pathlib import Path
import unittest

import payout_proof

SOURCE = Path(__file__).resolve().parents[1] / '.upstream/services/core-node/bcore' / payout_proof.FILE


@unittest.skipUnless(SOURCE.exists(), 'pinned sources not fetched (run bootstrap.py)')
class PayoutProofOverlayTests(unittest.TestCase):
    def test_both_work_unit_rpcs_return_coinbase_and_branch(self):
        out = payout_proof.overlay(SOURCE.read_text())
        self.assertEqual(out.count('pushKV("coinbase_tx"'), 2)
        self.assertEqual(out.count('pushKV("coinbase_merkle_branch"'), 2)
        self.assertEqual(out.count('"coinbase_tx", "NOET:'), 2)  # documented in both RPC results
        self.assertIn('coinbase_bytes << TX_NO_WITNESS(*coinbase);', out)
        with self.assertRaises(ValueError):
            payout_proof.overlay(out)  # anchors are single-use; a second pass must not silently re-apply


if __name__ == '__main__':
    unittest.main()
