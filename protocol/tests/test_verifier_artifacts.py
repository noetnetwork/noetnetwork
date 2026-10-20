"""Exercise actual verifier loader call sites without importing the GPU stack."""
import ast
import os
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch
from noet import model_runtime as runtime

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '.upstream/services/verification-api/src'


def extracted(path, name, namespace):
    tree = ast.parse((SOURCE / path).read_text())
    node = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)
    # Heavy optional annotations must not import torch just to test the boundary.
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
    return namespace[name]


class VerifierArtifactTests(unittest.TestCase):
    def test_loader_has_no_network_revision_or_remote_code(self):
        loader = Mock()
        with patch.object(runtime, 'checked_model_directory', return_value='/approved') as check:
            result = runtime.approved_pretrained(loader, 'NOET/model', revision='a'*40, use_safetensors=True, local_files_only=False)
        check.assert_called_once_with('NOET/model', 'a'*40)
        loader.from_pretrained.assert_called_once_with('/approved', use_safetensors=True, local_files_only=True, trust_remote_code=False)
        self.assertIs(result, loader.from_pretrained.return_value)

    def test_invalid_snapshot_never_invokes_transformers(self):
        loader = Mock()
        with patch.object(runtime, 'checked_model_directory', side_effect=ValueError('invalid')), self.assertRaises(ValueError):
            runtime.approved_pretrained(loader, 'NOET/model', revision='main')
        loader.from_pretrained.assert_not_called()

    def test_unsafe_loader_overrides_refused(self):
        for kwargs in ({'trust_remote_code':True}, {'use_safetensors':False}, {'token':'secret'}, {'subfolder':'../other'}, {'code_revision':'main'}, {'force_download':True}, {'proxies':{'https':'proxy'}}):
            loader = Mock()
            with self.subTest(kwargs=tuple(kwargs)), patch.object(runtime, 'checked_model_directory', return_value='/approved'), self.assertRaises(ValueError):
                runtime.approved_pretrained(loader, 'NOET/model', revision='a'*40, **kwargs)
            loader.from_pretrained.assert_not_called()

    def test_every_transformers_call_is_guarded_and_weights_are_safetensors(self):
        count = 0
        for path in ('proof_verifier.py', 'model_verifier.py'):
            tree = ast.parse((SOURCE/path).read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call): continue
                self.assertFalse(isinstance(node.func, ast.Attribute) and node.func.attr == 'from_pretrained', path)
                if isinstance(node.func, ast.Name) and node.func.id == 'approved_pretrained':
                    count += 1
                    self.assertTrue(any(k.arg == 'revision' for k in node.keywords))
                    if node.args[0].id == 'AutoModelForCausalLM':
                        self.assertTrue(any(k.arg == 'use_safetensors' and isinstance(k.value, ast.Constant) and k.value.value is True for k in node.keywords))
        self.assertEqual(count, 11)

    def test_ipfs_fallback_fails_before_any_side_effect(self):
        method = extracted('proof_verifier.py', '_load_model_from_ipfs', {})
        with self.assertRaisesRegex(ValueError, 'disabled'):
            method(object(), 'arbitrary-cid', dtype=None)

    def test_cached_model_reuse_checks_snapshot_before_registry_access(self):
        guard = Mock(side_effect=ValueError('not approved'))
        method = extracted('proof_verifier.py', '_load_or_reuse_model', {'checked_model_directory':guard})
        obj = types.SimpleNamespace(model_name='NOET/model', commit_hash='a'*40)
        with self.assertRaisesRegex(ValueError, 'not approved'):method(obj)
        guard.assert_called_once_with(obj.model_name, obj.commit_hash)

    def test_tokenizer_uses_guarded_local_loader(self):
        loader = Mock(); cls = object()
        method = extracted('proof_verifier.py', '_ensure_tokenizer', {'approved_pretrained':loader, 'AutoTokenizer':cls})
        obj = types.SimpleNamespace(tokenizer=None, model_name='NOET/model', commit_hash='a'*40)
        method(obj)
        loader.assert_called_once_with(cls, obj.model_name, revision=obj.commit_hash)
        self.assertIs(obj.tokenizer, loader.return_value)

    def test_dtype_inspection_rejects_remote_and_pickle_paths(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'model.safetensors').write_bytes(b'fixture'); (root/'other.bin').write_bytes(b'pickle')
            guard = Mock(return_value=folder); parse = Mock(return_value='float16')
            method = extracted('utils/shared_utils.py', 'get_native_dtype_from_commit', {'checked_model_directory':guard, 'Path':Path, 'parse_safetensors_header':parse})
            self.assertEqual(method('NOET/model', 'a'*40), 'float16')
            parse.assert_called_once_with(root/'model.safetensors')
            for name in ('../other.safetensors', '/remote/model.safetensors', 'other.bin'):
                with self.assertRaises(ValueError):method('NOET/model', 'a'*40, name)
            guard.side_effect = ValueError('invalid snapshot')
            with self.assertRaises(ValueError):method('other/model', 'main')
            self.assertEqual(parse.call_count, 1)

    def test_oversized_safetensors_header_does_not_read_payload(self):
        import json, struct, tempfile
        method = extracted('utils/shared_utils.py', 'parse_safetensors_header', {'json':json, 'struct':struct, 'SF_DTYPES':{'F16':'float16'}})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'weights.safetensors'
            path.write_bytes(struct.pack('<Q', 2**63))
            self.assertIsNone(method(path))
            header = json.dumps({'weight':{'dtype':'F16'}}).encode()
            path.write_bytes(struct.pack('<Q',len(header))+header)
            self.assertEqual(method(path), 'float16')

    def test_proof_io_rejects_unapproved_model_before_tensor_creation(self):
        guard = Mock(side_effect=ValueError('not approved'))
        method = extracted('proof_verifier.py', '_load_io', {'checked_model_directory':guard, 'os':os})
        obj = types.SimpleNamespace(proof={'model_identifier':'other/model@'+'b'*40}, logger=Mock())
        with self.assertRaisesRegex(ValueError, 'not approved'):method(obj)
        guard.assert_called_once_with('other/model', 'b'*40)
        self.assertFalse(hasattr(obj, 'prompt_tokens'))
