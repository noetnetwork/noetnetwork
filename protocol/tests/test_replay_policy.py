"""Replay compute policy: fp32 reference for bf16/fp16 proofs, grids unchanged."""
import ast
import os
from pathlib import Path
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '.upstream/services/verification-api/src/proof_verifier.py'


def load_policy():
    tree = ast.parse(SOURCE.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_replay_compute_dtype')
    torch = types.SimpleNamespace(dtype=object, float32='float32', bfloat16='bfloat16', float16='float16')
    scope = {'torch': torch, 'os': os}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope['_replay_compute_dtype'], torch


@unittest.skipUnless(SOURCE.exists(), 'Pinned verifier source is not prepared')
class ReplayPolicyTests(unittest.TestCase):
    def test_gpu_bf16_and_fp16_replay_in_fp32_by_default(self):
        policy, torch = load_policy()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(policy(torch.bfloat16, precision='bf16', cpu=False), torch.float32)
            self.assertEqual(policy(torch.float16, precision='fp16', cpu=False), torch.float32)
            self.assertEqual(policy(torch.bfloat16, precision='bf16', cpu=True), torch.float32)

    def test_fp8_keeps_quantized_activation_dtype(self):
        policy, torch = load_policy()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(policy(torch.bfloat16, precision='fp8', cpu=False), torch.bfloat16)

    def test_declared_mode_is_explicit_and_unknown_values_fail(self):
        policy, torch = load_policy()
        with mock.patch.dict(os.environ, {'NOET_REPLAY_COMPUTE': 'declared'}, clear=True):
            self.assertEqual(policy(torch.bfloat16, precision='bf16', cpu=False), torch.bfloat16)
        with mock.patch.dict(os.environ, {'NOET_REPLAY_COMPUTE': 'bf16'}, clear=True):
            with self.assertRaises(ValueError):
                policy(torch.bfloat16, precision='bf16', cpu=False)

    def test_model_load_uses_policy_and_grids_keep_declared_dtype(self):
        text = SOURCE.read_text()
        self.assertIn('requested_dtype = _replay_compute_dtype(dtype, precision=self.precision, cpu=cpu)', text)
        self.assertNotIn('requested_dtype = (torch.float32 if cpu else dtype)', text)
        self.assertIn("return torch.float32 if self._declared_head() in ('h1', 'h2') else self.dtype", text)


if __name__ == '__main__':
    unittest.main()
