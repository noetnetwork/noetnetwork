"""Exercise the actual pinned miner configuration without starting a worker."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

UPSTREAM = Path(__file__).resolve().parents[1] / ".upstream"
CONSTANTS = UPSTREAM / "services/miner-api/src/components/constants.py"
LOAD_CONFIG = """
import runpy, sys, types
from pathlib import Path
root = Path(sys.argv[1])
utils = types.ModuleType('utils')
utils.__path__ = [str(root / 'shared-utils/pow-utils')]
sys.modules['utils'] = utils
config = runpy.run_path(str(root / 'services/miner-api/src/components/constants.py'))
print('test_mode=' + str(config['TEST_MODE']))
print('remote_fallback=' + str(config['VDF_FALLBACK_ENABLED']))
print('unverified=' + str(config['VDF_FALLBACK_ALLOW_UNVERIFIED']))
print('fallback_url=' + config['VDF_FALLBACK_URL'])
"""



LOAD_VDF = LOAD_CONFIG + """
from unittest.mock import patch
components = types.ModuleType('components')
components.constants = types.SimpleNamespace(**config)
sys.modules['components'] = components
context = types.ModuleType('components.context')
context.LockFreeContext = object
sys.modules['components.context'] = context
sys.modules['chiavdf'] = None
vdf = runpy.run_path(str(root / 'services/miner-api/src/components/vdf_service.py'))
service = vdf['VDFService'](object())
"""

@unittest.skipUnless(CONSTANTS.exists(), "Run protocol/bootstrap.py to test the pinned miner")
class MinerConfigurationTests(unittest.TestCase):
    def load_config(self, value=None, extra=None, script=LOAD_CONFIG):
        env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
        if value is not None:
            env["TEST_MODE"] = value
        env.update(extra or {})
        return subprocess.run(
            [sys.executable, "-c", script, str(UPSTREAM)],
            env=env, capture_output=True, text=True, timeout=10,
        )

    def test_real_work_is_default(self):
        result = self.load_config()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("test_mode=False", result.stdout)

    def test_false_values_never_generate_synthetic_jobs(self):
        for value in ("false", "0", "no", " FALSE "):
            with self.subTest(value=value):
                result = self.load_config(value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("test_mode=False", result.stdout)

    def test_synthetic_jobs_require_explicit_opt_in(self):
        for value in ("true", "1", "yes", " TRUE "):
            with self.subTest(value=value):
                result = self.load_config(value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("test_mode=True", result.stdout)

    def test_remote_fallback_and_unverified_adoption_are_disabled(self):
        result = self.load_config()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("remote_fallback=False", result.stdout)
        self.assertIn("unverified=False", result.stdout)
        self.assertIn("fallback_url=\n", result.stdout)

    def test_remote_proof_settings_cannot_enable_an_unreviewed_path(self):
        for setting in ("VDF_FALLBACK_ENABLED", "VDF_FALLBACK_ALLOW_UNVERIFIED"):
            for value in ("true", "1", "yes", " TRUE ", "typo", ""):
                with self.subTest(setting=setting, value=value):
                    result = self.load_config(extra={setting: value})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(setting + " must remain disabled", result.stderr)
            for value in ("false", "0", "no", " FALSE "):
                with self.subTest(setting=setting, value=value):
                    result = self.load_config(extra={setting: value})
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_remote_url_is_rejected_without_disclosing_its_value(self):
        url = "https://example.invalid/proof?token=private-test-value"
        result = self.load_config(extra={"VDF_FALLBACK_URL": url})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("VDF_FALLBACK_URL is not supported", result.stderr)
        self.assertNotIn(url, result.stderr + result.stdout)

    def test_missing_local_prover_fails_before_starting_threads(self):
        result = self.load_config(script=LOAD_VDF + """
with patch('threading.Thread') as thread, patch('urllib.request.urlopen') as request:
    try:
        service.start()
    except RuntimeError as error:
        assert 'Local VDF support is required' in str(error)
    else:
        raise AssertionError('Missing prover was accepted')
    thread.assert_not_called()
    request.assert_not_called()
    assert not service.running
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_prover_start_does_not_start_remote_worker(self):
        result = self.load_config(script=LOAD_VDF + """
# Stub availability, not proof correctness. This test checks thread routing only.
service.start.__globals__['CHIAVDF_AVAILABLE'] = True
with patch('threading.Thread') as thread, patch('urllib.request.urlopen') as request:
    service.start()
    assert thread.call_count == 1
    thread.return_value.start.assert_called_once()
    request.assert_not_called()
    assert service.remote_thread is None
    assert service.running
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_setting_refuses_startup(self):
        for value in ("", "disabled", "tru", "2"):
            with self.subTest(value=value):
                result = self.load_config(value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("TEST_MODE must be", result.stderr)


if __name__ == "__main__":
    unittest.main()
