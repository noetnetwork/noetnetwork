"""Safety boundaries of the isolated native wallet launcher."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('launch_wallet', Path(__file__).resolve().parents[1] / 'desktop/launch_wallet.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def test_private_data_and_isolated_defaults(self):
        with tempfile.TemporaryDirectory() as root:
            data = Path(root) / 'wallet with spaces'
            args = launcher.command(data)
            self.assertEqual(data.stat().st_mode & 0o777, 0o700)
            self.assertIn('-datadir=' + str(data.resolve()), args)
            self.assertIn('-chain=tensor-reg', args)
            for option in ('server', 'listen', 'dnsseed', 'fixedseeds', 'useextapi'):
                self.assertIn('-' + option + '=0', args)

    def test_rejects_shared_directory(self):
        with tempfile.TemporaryDirectory() as root:
            data = Path(root) / 'shared'
            data.mkdir()
            data.chmod(0o755)
            with self.assertRaises(ValueError):
                launcher.command(data)


if __name__ == '__main__':
    unittest.main()
