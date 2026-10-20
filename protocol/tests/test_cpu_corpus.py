import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from cpu import replay_envelopes as corpus


def item(root, name, raw, **recorded):
    folder = Path(root) / name
    folder.mkdir()
    (folder / 'request.bin').write_bytes(raw)
    (folder / 'result.json').write_text(json.dumps({'envelopeSha256': hashlib.sha256(raw).hexdigest(), **recorded}))


class CpuCorpusTests(unittest.TestCase):
    def test_selects_distinct_completed_full_verdicts_in_height_order(self):
        with tempfile.TemporaryDirectory() as root:
            item(root, '0', b'quick', quickVerdict=0, height=1)
            item(root, '1', b'b', fullVerdict='GREEN', height=2, replaySeed=7)
            item(root, '2', b'a', fullVerdict='RED', height=1)
            item(root, '3', b'b', fullVerdict='GREEN', height=2)  # retry of the same envelope
            item(root, '4', b'c', fullVerdict='GREEN', height=3, reusedExactEnvelope=True)
            picked = corpus.corpus(Path(root))
        self.assertEqual([(r['height'], r['fullVerdict']) for _, r in picked], [(1, 'RED'), (2, 'GREEN')])

    def test_refuses_an_envelope_that_does_not_match_its_record(self):
        with tempfile.TemporaryDirectory() as root:
            item(root, '0', b'a', fullVerdict='GREEN', height=1)
            (Path(root) / '0/request.bin').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'does not match'):
                corpus.corpus(Path(root))

    def test_compares_verdict_and_detector_and_summarizes(self):
        detector = {'detector_status': 'required', 'detector_decision': 'NO_VIOLATION',
                    'joint': {'eligible_total': 98, 'models': {'U1': {'flagged_eligible_positions': []}}}}
        recorded = {'fullVerdict': 'GREEN', 'detector': detector}
        same = {'fullVerdict': 'GREEN', 'detector': corpus.detector_summary(detector),
                'maxLogitSeverity': 2.0, 'elapsedSeconds': 90}
        abstained = {'exceptionType': 'DetectorEvidenceIncomplete', 'detector': None,
                     'maxLogitSeverity': None, 'elapsedSeconds': 80}
        self.assertTrue(all(corpus.compare(recorded, same).values()))
        self.assertFalse(corpus.compare(recorded, abstained)['verdict'])
        with tempfile.TemporaryDirectory() as root:
            for name, cpu in (('a', same), ('b', abstained)):
                (Path(root) / name).mkdir()
                (Path(root) / name / 'result.json').write_text(json.dumps(
                    {'envelopeSha256': name, 'recorded': recorded, 'cpu': cpu, 'agree': corpus.compare(recorded, cpu)}))
            summary = corpus.summarize(Path(root))
        self.assertEqual((summary['replayed'], summary['verdictAgreement'], summary['abstentions']), (2, 1, 1))
        self.assertEqual(summary['disagreements'], ['b'])
        self.assertEqual(summary['maxLogitSeverity'], 2.0)
        self.assertEqual(summary['detectorRequired'], 2)
        advisory = {**recorded, 'detector': {**detector, 'detector_status': 'advisory'}}
        self.assertEqual(corpus.compare(advisory, {**same, 'detector': None}), {'verdict': True})


class ReplaySeedTests(unittest.TestCase):
    def test_seed_is_stable_per_envelope_and_keyed(self):
        from realwork.verify import replay_seed
        key, other = bytes(32), bytes([1]) * 32
        envelope = hashlib.sha256(b'request').hexdigest()
        self.assertEqual(replay_seed(key, envelope), replay_seed(key, envelope))
        self.assertNotEqual(replay_seed(key, envelope), replay_seed(other, envelope))
        self.assertNotEqual(replay_seed(key, envelope), replay_seed(key, hashlib.sha256(b'other').hexdigest()))
        self.assertLess(replay_seed(key, envelope), 2 ** 32)


class NativeHeightTests(unittest.TestCase):
    def test_any_positive_32_bit_height(self):
        from realwork.verify import native_height
        for height in (1, 1000, 1001, 52_560 * 40, 2 ** 31 - 1):
            self.assertTrue(native_height(height), height)
        for height in (0, -1, 2 ** 31, True, 1.0, '7', None):
            self.assertFalse(native_height(height), repr(height))

if __name__ == '__main__':
    unittest.main()
