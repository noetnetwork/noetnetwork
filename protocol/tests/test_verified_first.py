"""The node overlay's fork-choice tie-break (testnet/prepare.py verified_first): a
pending block promoted to Full-GREEN is ordered after the blocks verified
before it, so on equal work the block verified first keeps the tip."""
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from testnet import prepare

CORE = prepare.ROOT / '.upstream/services/core-node/bcore'
SPEC = json.loads((prepare.ROOT / 'mainnet/network.json').read_text())
RESTAMP = 'pindex->nSequenceId = nBlockSequenceId++;'


def function(text, signature):
    """The body of the C++ function that starts with `signature`."""
    start = text.index(signature)
    depth, i = 0, text.index('{', start)
    for j in range(i, len(text)):
        depth += {'{': 1, '}': -1}.get(text[j], 0)
        if depth == 0:
            return text[start:j + 1]
    raise AssertionError('unbalanced')


@unittest.skipUnless(CORE.exists(), 'node sources not present')
class VerifiedFirstTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = (CORE / 'src/validation.cpp').read_text()
        cls.patched = prepare.verified_first(cls.original)
        cls.signature = 'ChainstateManager::FullVerdictApply ChainstateManager::ApplyBlockFullVerdict(CBlockIndex* pindex,'

    def test_only_a_green_promotion_of_a_received_pending_block_is_restamped(self):
        body = function(self.patched, self.signature)
        self.assertEqual(self.patched.count(RESTAMP), self.original.count(RESTAMP) + 1)
        self.assertIn(RESTAMP, body, 'the new number is set inside ApplyBlockFullVerdict')
        guard = body[body.rindex('if (', 0, body.index(RESTAMP)):body.index(RESTAMP)]
        condition = ' '.join(re.fullmatch(r'if \((.*)\) \{\s*', guard, re.S).group(1).split())
        self.assertEqual(condition, 'was_pending && !now_red && source != FullVerdictSource::IndexLoad && '
                                    'pindex->HaveNumChainTxs() && pindex->nSequenceId >= 0')
        # After the pending bit is cleared (candidates were cleared before that) and
        # before the function returns; the caller rebuilds the candidate set.
        self.assertLess(body.index('ClearBlockIndexCandidates();'), body.index(RESTAMP))
        self.assertLess(body.index('pindex->nStatus |= now_red ? BLOCK_FULL_RED_LOCAL : BLOCK_FULL_GREEN_LOCAL;'),
                        body.index(RESTAMP))
        self.assertIn('AssertLockHeld(cs_main);', body)

    def test_the_tie_break_it_relies_on_is_unchanged(self):
        # Equal work: the lower nSequenceId wins, in both comparators.
        storage = (CORE / 'src/node/blockstorage.cpp').read_text()
        self.assertEqual(storage.count('    if (pa->nSequenceId < pb->nSequenceId) return false;'), 2)
        self.assertIn('int32_t nBlockSequenceId GUARDED_BY(::cs_main) = 1;', (CORE / 'src/validation.h').read_text())

    def test_nothing_else_changes(self):
        removed = [line for line in self.original.splitlines() if line not in self.patched.splitlines()]
        self.assertEqual(removed, [])
        self.assertEqual(len(self.patched.splitlines()) - len(self.original.splitlines()), 5)
        self.assertEqual(self.patched.count('{') - self.patched.count('}'),
                         self.original.count('{') - self.original.count('}'))

    def test_a_moved_anchor_stops_the_build(self):
        with self.assertRaisesRegex(ValueError, 'exactly one source anchor'):
            prepare.verified_first(self.original.replace(prepare.VERIFIED_FIRST_ANCHOR, 'X'))

    def test_testnet_and_mainnet_overlays_carry_it(self):
        sources = {p: (CORE / p).read_text() for p in prepare.FILES}
        with tempfile.TemporaryDirectory() as folder:
            keys = Path(folder) / 'keys.json'
            shutil.copyfile(prepare.ROOT / SPEC['allocationKeys'], keys)
            (Path(folder) / 'network.json').write_text(json.dumps({**SPEC, 'allocationKeys': str(keys)}))
            main = prepare.overlay(sources, net=prepare.mainnet(Path(folder) / 'network.json'))
        for result in (main, prepare.overlay(sources), prepare.overlay(sources, test=True)):
            self.assertEqual(result['src/validation.cpp'].count(RESTAMP), self.original.count(RESTAMP) + 1)
            self.assertIn('testnet/prepare.py verified_first', result['src/validation.cpp'])


if __name__ == '__main__':
    unittest.main()
