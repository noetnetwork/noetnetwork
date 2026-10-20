"""Guarded calibration policy and actual full-verifier decision boundaries.

Scores below are synthetic boundary fixtures, never model calibration evidence.
"""
import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import Mock, patch

from noet import verifier_entry as entry
from test_verifier_artifacts import SOURCE, extracted

spec = importlib.util.spec_from_file_location('prompt_gate_reference', SOURCE / 'utils/prompt_gibberish.py')
gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gate
spec.loader.exec_module(gate)
MID = 'NOET/model@' + 'a' * 40
ENV = {'MODEL_NAME': 'NOET/model', 'MODEL_COMMIT': 'a' * 40,
       'POW_PROMPT_GIBBERISH_CALIBRATED': MID + ':bf16'}


def verifier(precision='bf16', bits=8):
    obj = types.SimpleNamespace(logger=Mock(), _prompt_gibberish_header_time=1,
        _prompt_gibberish_context=lambda: (MID, precision),
        _last_prompt_gibberish=gate.score_surprisal([bits] * 256))
    method = extracted('proof_verifier.py', '_prompt_gibberish_verdict', {
        'os': os, '_prompt_gibberish': gate,
        'require_prompt_calibration': entry.require_prompt_calibration})
    return lambda: method(obj), obj


class PromptCalibrationTests(unittest.TestCase):
    def test_missing_or_overbroad_scope_refused_before_model_and_service(self):
        for scope in ('', '*', MID, MID + ':', MID + ':BF16', MID + ':unknown',
                      MID + ':bf16,', MID + ':bf16,' + MID + ':bf16',
                      'OTHER/model@' + 'a'*40 + ':bf16', MID.replace('a'*40, 'main') + ':bf16'):
            with self.subTest(scope=scope), patch.dict(os.environ, ENV | {'POW_PROMPT_GIBBERISH_CALIBRATED': scope}, clear=True), \
                 patch.object(entry, 'checked_model_directory') as model, patch.object(entry.runpy, 'run_path') as run:
                with self.assertRaisesRegex(ValueError, 'exact model@revision:precision'):
                    entry.main()
                model.assert_not_called()
                run.assert_not_called()

    def test_configuration_cannot_disable_delay_or_retune_gate(self):
        bad = {'NOET_REQUIRE_PROMPT_CALIBRATION': ['0', 'false'],
               'POW_PROMPT_GIBBERISH_GATE': ['off', 'shadow', 'typo'],
               'POW_PROMPT_GIBBERISH_ACTIVATION_TIME': ['1', '-1', '3000000000'],
               'POW_PROMPT_GIBBERISH_BITS': ['99', 'nan', 'inf', '0'],
               'POW_PROMPT_GIBBERISH_MIN_TRANSITIONS': ['9999', '0'],
               'POW_PROMPT_GIBBERISH_TAIL': ['1', '9999']}
        for key, values in bad.items():
            for value in values:
                with self.subTest(key=key, value=value), patch.dict(os.environ, ENV | {key: value}, clear=True):
                    with self.assertRaisesRegex(ValueError, key): entry.require_prompt_calibration()

    def test_reviewed_pairs_enforce_with_immediate_activation(self):
        with patch.dict(os.environ, ENV, clear=True):
            entry.require_prompt_calibration()
            self.assertEqual(gate.effective_mode(MID, 'bf16'), ('enforce', True))
            self.assertEqual(gate.is_active(1)[0], True)
            self.assertEqual((gate.threshold_bits(), gate.min_transitions(), gate.tail_transitions()), (14, 32, 256))
            for bits, rejects in ((13.999, False), (14, True), (64, True), (float('nan'), True)):
                method, obj = verifier(bits=bits)
                self.assertEqual(method() is not None, rejects)
                self.assertEqual(obj._last_prompt_gibberish_decision['mode'], 'enforce')

    def test_unknown_precision_abstains_even_with_cached_pass(self):
        with patch.dict(os.environ, ENV, clear=True):
            entry.require_prompt_calibration()
            for precision in ('fp16', 'fp32', None):
                method, obj = verifier(precision)
                obj._last_prompt_gibberish_decision = {'red': False}
                with self.assertRaisesRegex(gate.PromptGibberishScoringError, 'No reviewed prompt calibration'):
                    method()

    def test_missing_model_and_precision_do_not_become_startup_check(self):
        with patch.dict(os.environ, ENV, clear=True):
            entry.require_prompt_calibration()
            method, obj = verifier()
            obj._prompt_gibberish_context = lambda: (None, None)
            with self.assertRaises(gate.PromptGibberishScoringError): method()

    def test_unreviewed_proof_abstains_before_snapshot_or_tensor_allocation(self):
        guard = Mock()
        method = extracted('proof_verifier.py', '_load_io', {
            'os':os, 'require_prompt_calibration':entry.require_prompt_calibration,
            '_prompt_gibberish':gate, 'checked_model_directory':guard})
        with patch.dict(os.environ, ENV, clear=True):
            entry.require_prompt_calibration()
            for proof in ({}, {'model_identifier':MID, 'compute_precision':'fp32'},
                          {'model_identifier':'other/model@'+'a'*40, 'compute_precision':'bf16'}):
                obj = types.SimpleNamespace(proof=proof, logger=Mock())
                with self.assertRaises(gate.PromptGibberishScoringError): method(obj)
            guard.assert_not_called()

    def test_runtime_configuration_drift_abstains_before_cached_result(self):
        with patch.dict(os.environ, ENV, clear=True):
            entry.require_prompt_calibration()
            method, obj = verifier()
            self.assertIsNone(method())
            os.environ['POW_PROMPT_GIBBERISH_GATE'] = 'off'
            with self.assertRaises(gate.PromptGibberishScoringError): method()

    def test_scoring_failure_abstains_and_malformed_prompt_rejects(self):
        with patch.dict(os.environ, ENV, clear=True):
            entry.require_prompt_calibration()
            for error in (None, ('infra', 'out of memory')):
                method, obj = verifier()
                obj._last_prompt_gibberish = None
                obj._last_prompt_gibberish_error = error
                with self.assertRaises(gate.PromptGibberishScoringError): method()
            method, obj = verifier()
            obj._last_prompt_gibberish = None
            obj._last_prompt_gibberish_error = ('malformed', 'mask length mismatch')
            self.assertEqual(method()[0], 'RED')

    def test_research_path_retains_explicit_shadow_without_calibration_claim(self):
        with patch.dict(os.environ, {}, clear=True):
            method, obj = verifier(bits=64)
            self.assertIsNone(method())
            self.assertEqual(obj._last_prompt_gibberish_decision['mode'], 'shadow')
            self.assertFalse(obj._last_prompt_gibberish_decision['calibrated'])

    def test_short_prompt_policy_is_not_changed(self):
        with patch.dict(os.environ, ENV, clear=True):
            entry.require_prompt_calibration()
            method, obj = verifier()
            obj._last_prompt_gibberish = gate.score_surprisal([64]*31)
            self.assertIsNone(method())
            self.assertFalse(obj._last_prompt_gibberish_decision['score']['applicable'])


if __name__ == '__main__':
    unittest.main()
