"""Full v4 logit cap reads the unperturbed batch-1 replay (full-r2), in the shipped patch."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'patches/noet-miner.patch'
SOURCE = ROOT / '.upstream/services/verification-api/src'


def added(path):
    """Lines the NOET patch adds to one upstream file."""
    lines, current = [], None
    for line in PATCH.read_text().splitlines():
        if line.startswith('diff --git '):
            current = line.split(' b/', 1)[1]
        elif current == path and line.startswith('+') and not line.startswith('+++'):
            lines.append(line[1:])
    return '\n'.join(lines)


class FullGateReferenceTests(unittest.TestCase):
    def test_every_engine_gates_the_reference_replay(self):
        text = added('services/verification-api/src/proof_verifier.py')
        self.assertIn("v4_logit_severity.append(out_base['v4_severity'])", text)
        self.assertIn("ref_result = result     # cand_order[0]: batch 1, no MCA", text)
        self.assertIn("v4_logit_severity.append(ref_result['v4_severity'])", text)
        self.assertEqual(text.count('v4_sev_ref      = SEV[0]'), 2)
        self.assertEqual(text.count('assert cand_keys[0] == 1'), 2)
        self.assertEqual(text.count('v4_logit_severity   = v4_sev_ref.detach().cpu().tolist(),'), 2)

    def test_revision_and_message_change_together(self):
        gates = added('services/verification-api/src/full_gates.py')
        self.assertIn('FULL_RULES_REVISION = "full-r2-2026-10-21"', gates)
        self.assertIn('reference replay logit severity', gates)
        self.assertIn('full-r2-2026-10-21', added('services/verification-api/src/v4_dispatch.py'))

    @unittest.skipUnless(SOURCE.exists(), 'Pinned verifier source is not prepared')
    def test_prepared_source_has_no_best_p_severity(self):
        text = (SOURCE / 'proof_verifier.py').read_text()
        self.assertNotIn('v4_sev_best', text)
        self.assertNotIn("v4_logit_severity.append(best['v4_severity'])", text)
        self.assertNotIn("v4_logit_severity.append(best_result['v4_severity'])", text)
        self.assertNotIn('full-r1-2026-09-10', (SOURCE / 'v4_dispatch.py').read_text())


if __name__ == '__main__':
    unittest.main()
