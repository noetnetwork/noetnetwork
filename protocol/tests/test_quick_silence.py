"""The node overlay's handling of Quick requests the verifier never answered
(testnet/prepare.py quick_silence): dropped without a verdict, never a stored fail."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from testnet import prepare

CORE = prepare.ROOT / '.upstream/services/core-node/bcore'
SPEC = json.loads((prepare.ROOT / 'mainnet/network.json').read_text())


@unittest.skipUnless(CORE.exists(), 'node sources not present')
class QuickSilenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = (CORE / 'src/validationapi.cpp').read_text()
        cls.patched = prepare.quick_silence(cls.original)

    def test_exhausted_quick_requests_store_no_fail(self):
        for kind, status in (('Quick', 'Quick_Fail'), ('Quick_Smell', 'Quick_Fail_Smell_Fail')):
            line = f'to_default.push_back({{it->first, ValidationReqType::{kind}, ValidationResponseValue::{status}}});'
            self.assertIn(line, self.original)
            self.assertNotIn(line, self.patched)
            self.assertIn(f'"VALIDATOR: {kind} %s unanswered after every retry; dropped without a verdict\\n"', self.patched)
        self.assertIn('it = requestTracker.short_requests_.erase(it);', self.patched)
        self.assertEqual(self.patched.count('it = requestTracker.short_smell_requests_.erase(it);'),
                         self.original.count('it = requestTracker.short_smell_requests_.erase(it);') + 1)

    def test_everything_else_is_unchanged(self):
        # Challenge and Model keep their defaults; a real local Quick fail still fails the block.
        for kept in ('ValidationReqType::Challenge, ValidationResponseValue::Challenge_Fail',
                     'ValidationReqType::Model, ValidationResponseValue::Model_Fail',
                     'VALIDATOR: Local QuickVerifier FAILED for %s',
                     'to_local_quick.push_back({it->first, it->second.first, ValidationReqType::Quick_Smell});'):
            self.assertEqual(self.patched.count(kept), self.original.count(kept), kept)
        removed = [line for line in self.original.splitlines() if line not in self.patched.splitlines()]
        self.assertEqual(len(removed), 2, removed)  # one to_default line per kind; "it++;" lines remain elsewhere
        self.assertEqual(self.patched.count('{') - self.patched.count('}'),
                         self.original.count('{') - self.original.count('}'))

    def test_a_moved_anchor_stops_the_build(self):
        with self.assertRaisesRegex(ValueError, 'exactly one source anchor'):
            prepare.quick_silence(self.original.replace('ValidationResponseValue::Quick_Fail_Smell_Fail});', 'X});'))

    def test_testnet_and_mainnet_overlays_carry_it(self):
        sources = {p: (CORE / p).read_text() for p in prepare.FILES}
        with tempfile.TemporaryDirectory() as folder:
            keys = Path(folder) / 'keys.json'
            shutil.copyfile(prepare.ROOT / SPEC['allocationKeys'], keys)
            (Path(folder) / 'network.json').write_text(json.dumps({**SPEC, 'allocationKeys': str(keys)}))
            main = prepare.overlay(sources, net=prepare.mainnet(Path(folder) / 'network.json'))
        for result in (main, prepare.overlay(sources), prepare.overlay(sources, test=True)):
            self.assertEqual(result['src/validationapi.cpp'], self.patched)


if __name__ == '__main__':
    unittest.main()
