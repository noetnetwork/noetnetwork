import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from gpu.audit import audit, VIEWS


class GpuEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.report = {'passed': True, 'finalHeight': 3,
            'acceptedBlocks': [{'height': height} for height in (1, 2, 3)],
            'attempts': [{'nativeFullValidation': {'full_status': 'green'}, 'duplicateIsIdempotent': True} for _ in range(3)]}
        self.write_report()
        for height in (1, 2, 3):
            item = self.root / 'verifier' / str(height)
            item.mkdir(parents=True)
            raw = ('record-' + str(height)).encode()
            (item / 'request.bin').write_bytes(raw)
            (item / 'detector.npz').write_bytes(raw)
            row = {'requestType': 2, 'height': height, 'ruleset': 4 if height >= 3 else 2,
                'fullVerdict': 'GREEN', 'responseSent': True, 'responseCode': 6,
                'envelopeSha256': hashlib.sha256(raw).hexdigest(), 'replayViews': VIEWS,
                'detectorRecordStatus': 'scored',
                'detector': {'detector_status': 'required', 'detector_decision': 'NO_VIOLATION'},
                'detectorCapture': {'sha256': hashlib.sha256(raw).hexdigest()}}
            (item / 'result.json').write_text(json.dumps(row))

    def write_report(self):
        (self.root / 'result.json').write_text(json.dumps(self.report))

    def change(self, number, **changes):
        path = self.root / 'verifier' / str(number) / 'result.json'
        row = json.loads(path.read_text())
        row.update(changes)
        path.write_text(json.dumps(row))

    def test_complete_record_and_exact_retry_pass(self):
        prior = self.root / 'verifier' / '3' / 'result.json'
        retry = prior.parent.with_name('4')
        retry.mkdir()
        (retry / 'request.bin').write_bytes(prior.with_name('request.bin').read_bytes())
        row = json.loads(prior.read_text())
        row.update(reusedExactEnvelope=True, computedResultDirectory='3')
        (retry / 'result.json').write_text(json.dumps(row))
        result = audit(self.root, require_captures=True)
        self.assertTrue(result['passed'], result['failures'])
        self.assertEqual(result['exactRetryResponses'], 1)

    def test_switch_rehearsal_forgery_must_be_red_and_stays_apart(self):
        # native.py --relabel-after-switch: genesis weights claiming the switch model at height 3.
        forged = self.root / 'verifier' / '9'
        forged.mkdir()
        raw = b'forged-3'
        (forged / 'request.bin').write_bytes(raw)
        (forged / 'result.json').write_text(json.dumps({'requestType': 2, 'height': 3, 'fullVerdict': 'RED',
            'responseSent': True, 'responseCode': 8, 'envelopeSha256': hashlib.sha256(raw).hexdigest()}))
        retry = forged.with_name('10')  # the node asks again; the verifier repeats RED
        retry.mkdir()
        (retry / 'request.bin').write_bytes(raw)
        row = {**json.loads((forged / 'result.json').read_text()), 'reusedExactEnvelope': True,
               'computedResultDirectory': '9'}
        (retry / 'result.json').write_text(json.dumps(row))
        self.report['forgeries'] = [{'height': 3, 'verdict': 'red'}]
        self.report['attempts'].append({'forgedRelabel': True, 'nativeFullValidation': {'full_status': 'red'}})
        self.write_report()
        result = audit(self.root)
        self.assertTrue(result['passed'], result['failures'])
        self.assertEqual((result['refusedForgeries'], result['exactRetryResponses']), (['9'], 1))
        (retry / 'result.json').write_text(json.dumps({**row, 'fullVerdict': 'GREEN', 'responseCode': 6}))
        self.assertIn('10: Retry does not match its computed verdict', audit(self.root)['failures'])
        (retry / 'result.json').write_text(json.dumps(row))
        # A recorded forgery without its RED result, or an honest block that is RED, fails.
        self.report['forgeries'].append({'height': 2, 'verdict': 'red'})
        self.write_report()
        self.assertIn('A recorded forgery has no RED Full result', audit(self.root)['failures'])
        self.report['forgeries'] = []
        self.write_report()
        self.assertFalse(audit(self.root)['passed'])

    def test_overall_success_cannot_hide_failed_full(self):
        self.change(2, fullVerdict='RED', responseCode=8)
        self.assertFalse(audit(self.root)['passed'])

    def test_green_label_cannot_hide_red_transport_or_native_status(self):
        self.change(2, responseCode=8)
        self.assertFalse(audit(self.root)['passed'])
        self.change(2, responseCode=6)
        self.report['attempts'][1]['nativeFullValidation']['full_status'] = 'red'
        self.write_report()
        self.assertFalse(audit(self.root)['passed'])

    def test_retry_must_have_sent_its_recorded_response(self):
        prior = self.root / 'verifier' / '3' / 'result.json'
        retry = prior.parent.with_name('4')
        retry.mkdir()
        (retry / 'request.bin').write_bytes(prior.with_name('request.bin').read_bytes())
        row = json.loads(prior.read_text())
        row.update(reusedExactEnvelope=True, computedResultDirectory='3', responseSent=False)
        (retry / 'result.json').write_text(json.dumps(row))
        self.assertFalse(audit(self.root)['passed'])

    def test_required_scoring_cannot_be_claimed_without_views(self):
        self.change(3, replayViews=['1'], detectorRecordStatus='not_scored')
        self.assertFalse(audit(self.root)['passed'])

    def test_capture_tamper_fails(self):
        (self.root / 'verifier' / '3' / 'detector.npz').write_bytes(b'changed')
        self.assertFalse(audit(self.root, require_captures=True)['passed'])

    def test_envelope_tamper_fails(self):
        (self.root / 'verifier' / '2' / 'request.bin').write_bytes(b'changed')
        self.assertFalse(audit(self.root)['passed'])

    def test_retry_cannot_invent_computed_evidence(self):
        self.change(3, reusedExactEnvelope=True, computedResultDirectory='999')
        self.assertFalse(audit(self.root)['passed'])

    def test_missing_height_and_duplicate_check_fail(self):
        self.report['acceptedBlocks'].pop(1)
        self.report['attempts'][0]['duplicateIsIdempotent'] = False
        self.write_report()
        self.assertFalse(audit(self.root)['passed'])

    def test_interrupted_run_is_not_success(self):
        self.report['passed'] = False
        self.write_report()
        self.assertFalse(audit(self.root)['passed'])
