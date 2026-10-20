import re
from pathlib import Path
import types
import unittest
from unittest.mock import Mock
from noet.vdf import verify, FORM_BYTES, PROOF_BYTES
from test_verifier_artifacts import extracted

ROOT = Path(__file__).resolve().parents[1]


class VdfBoundaryTests(unittest.TestCase):
    def test_wire_arguments_are_exact_without_endian_changes(self):
        backend = Mock(return_value=True)
        challenge = bytes(range(32)); proof = bytes(range(200))
        self.assertTrue(verify(challenge.hex().upper(), proof.hex(), 10_000, backend))
        backend.assert_called_once_with(challenge, proof, 1024, 10_000, 0)

    def test_malformed_challenge_never_calls_native_verifier(self):
        backend = Mock(side_effect=AssertionError('native call'))
        for value in (None, b'a'*32, '', 'a'*63, 'a'*65, '0x'+'a'*64, 'a'*63+' ', 'z'*64, 'é'*64):
            with self.subTest(value=repr(value)):
                self.assertFalse(verify(value,'ab'*200,1,backend))
        backend.assert_not_called()

    def test_proof_length_and_encoding_checked_before_native_call(self):
        backend = Mock(side_effect=AssertionError('native call'))
        for value in (None, b'a'*200, '', 'a'*399, 'a'*401, 'a'*1000000, '0x'+'a'*400, 'g'*400, 'a'*399+'\n'):
            self.assertFalse(verify('a'*64,value,1,backend))
        backend.assert_not_called()

    def test_iteration_type_and_uint64_bounds(self):
        backend = Mock(return_value=True)
        for value in (-1, 2**64, True, False, 1.0, '1', None):
            self.assertFalse(verify('a'*64,'b'*400,value,backend))
        backend.assert_not_called()
        # Input gate follows the native uint64 contract, not a new monetary rule.
        for value in (0, 2**64-1):
            self.assertTrue(verify('a'*64,'b'*400,value,backend))

    def test_native_errors_and_non_boolean_results_fail_closed(self):
        for value in (False, None, 1, 'true', {}, object()):
            self.assertFalse(verify('a'*64,'b'*400,1,Mock(return_value=value)))
        self.assertFalse(verify('a'*64,'b'*400,1,Mock(side_effect=RuntimeError('failure'))))

    def test_unsupported_discriminant_refused(self):
        backend = Mock()
        for size in (512, 2048, 1024.0, True, '1024'):
            self.assertFalse(verify('a'*64,'b'*400,1,backend,size))
        backend.assert_not_called()

    def test_actual_service_calls_bounded_verifier(self):
        backend = Mock(return_value=True)
        method = extracted('utils/shared_utils.py','chiavdf_verify',{
            'verify_bounded_vdf':verify, 'chiavdf':types.SimpleNamespace(verify_from_hash=backend),
            'constants':types.SimpleNamespace(DISCRIMINANT_SIZE=1024)})
        self.assertFalse(method('bad','b'*400,1));backend.assert_not_called()
        self.assertTrue(method('a'*64,'b'*400,1));self.assertEqual(backend.call_count,1)

    def test_size_contract_matches_pinned_native_source(self):
        source = ROOT/'.upstream/shared-utils/chiavdf/src'
        header = (source/'bqfc.h').read_text()
        bits = int(re.search(r'#define BQFC_MAX_D_BITS (\d+)',header).group(1))
        self.assertEqual(bits,1024)
        self.assertIn('#define BQFC_FORM_SIZE ((BQFC_MAX_D_BITS + 31) / 32 * 3 + 4)',header)
        self.assertEqual(FORM_BYTES,(bits+31)//32*3+4)
        self.assertEqual(PROOF_BYTES,200)
        self.assertIn('proof_blob_len != 2 * form_size + depth * segment_len',(source/'verifier.h').read_text())
