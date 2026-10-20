import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import Mock, patch

from noet import verifier_entry
from test_verifier_artifacts import extracted, SOURCE

spec = importlib.util.spec_from_file_location('binding_reference', SOURCE / 'utils/vdf_binding.py')
binding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(binding)
PARENT = 'ab' * 32
PREFIX = '00' * 4 + PARENT + '00' * 40


def decision():
    return extracted('proof_verifier.py', '_vdf_challenge_binding', {
        '_strict': types.SimpleNamespace(RULESET_V4_STRICT=2),
        '_env_flag': lambda key, default: os.environ.get(key, str(default)).lower() in ('1', 'true', 'yes'),
        '_env_int': lambda key, default: int(os.environ.get(key, default)),
        'resolve_challenge': binding.resolve_challenge,
    })


class VerifierBindingTests(unittest.TestCase):
    def test_default_is_enforced_before_service_import(self):
        with patch.dict(os.environ, {'POW_PROMPT_GIBBERISH_CALIBRATED': 'NOET/model@'+'a'*40+':bf16', 'MODEL_NAME': 'NOET/model', 'MODEL_COMMIT': 'a'*40,
                                    'VALIDATOR_SHARED_SECRET': 'b'*64}, clear=True), \
             patch.object(verifier_entry, 'checked_model_directory'), \
             patch.object(verifier_entry.sys, 'path', sys.path[:]), \
             patch.object(verifier_entry.runpy, 'run_path') as run:
            def check(*args, **kwargs):
                self.assertEqual(os.environ['POW_VDF_CHALLENGE_BIND_ENFORCE'], '1')
                self.assertEqual(os.environ['POW_VDF_CHALLENGE_BIND_ACTIVATION_TIME'], '0')
            run.side_effect = check
            verifier_entry.main()
            run.assert_called_once()

    def test_disable_or_shadow_configuration_refused_before_model(self):
        cases = [{'POW_VDF_CHALLENGE_BIND_ENFORCE': value} for value in ('false', '0', 'no', 'shadow', 'garbage')]
        cases += [{'POW_VDF_CHALLENGE_BIND_ACTIVATION_TIME': value} for value in ('1', '-1', '3000000000', 'nan')]
        for env in cases:
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True), \
                 patch.object(verifier_entry, 'checked_model_directory') as model, \
                 patch.object(verifier_entry.runpy, 'run_path') as run:
                with self.assertRaisesRegex(ValueError, 'immediate VDF challenge binding'):
                    verifier_entry.main()
                model.assert_not_called()
                run.assert_not_called()

    def test_actual_verifier_method_binds_legacy_and_current_requests(self):
        method = decision()
        with patch.dict(os.environ, {}, clear=True):
            verifier_entry.require_challenge_binding()
            for ruleset in (0, 1, 2, 3, 4):
                obj = types.SimpleNamespace(_ruleset_ctx=types.SimpleNamespace(ruleset=ruleset),
                    _request_header_time=1, _request_prev_block_hash_hex=PARENT, _context_bound=True)
                proof = {'block_hash': PARENT, 'header_prefix': PREFIX}
                self.assertEqual(method(obj, proof), (None, PARENT, True))
                for wrong in ({'block_hash': 'cd'*32, 'header_prefix': PREFIX},
                              {'block_hash': PARENT, 'header_prefix': '00'*80},
                              {'block_hash': PARENT, 'header_prefix': PREFIX[:-2]}):
                    error, _, enforced = method(obj, wrong)
                    self.assertIsNotNone(error)
                    self.assertTrue(enforced)
                obj._request_prev_block_hash_hex = 'cd'*32
                error, _, enforced = method(obj, proof)
                self.assertIn('outer prev_block_hash', error)
                self.assertTrue(enforced)

    def test_full_path_rejects_mismatch_before_any_model_load(self):
        proof = {'model_identifier': 'NOET/model@'+'a'*40,
                 'block_hash': 'cd'*32, 'header_prefix': PREFIX}
        full = extracted('proof_verifier.py', 'full_verify', {
            'torch': types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False)),
            'pfunpack': types.SimpleNamespace(unpack_validation_request=lambda _: {'request': {'pow_blob': proof}}),
        })
        obj = types.SimpleNamespace(_ruleset_ctx=types.SimpleNamespace(ruleset=0),
            _request_header_time=1, _request_prev_block_hash_hex=PARENT, _context_bound=True,
            _stash_request_difficulty=Mock(), _stash_request_header_time=Mock(),
            _stash_request_prev_hash=Mock(), _stash_request_ruleset=Mock(),
            _check_ruleset_context=Mock(), _prompt_gibberish_reset=Mock(),
            _log_vdf_binding_mismatch=Mock(), _release_full_verify_residue=Mock(),
            initialise=Mock(), reload=Mock())
        obj._vdf_challenge_binding = types.MethodType(decision(), obj)
        with patch.dict(os.environ, {}, clear=True):
            verifier_entry.require_challenge_binding()
            self.assertEqual(full(obj, b'bounded-test-envelope'), 'RED')
        obj._log_vdf_binding_mismatch.assert_called_once()
        obj.initialise.assert_not_called()
        obj.reload.assert_not_called()


if __name__ == '__main__':
    unittest.main()
