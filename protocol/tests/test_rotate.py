"""Certificate rotation keeps the current set in certs/ and both bundles in step."""
import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('noet_deploy_rotate', ROOT / 'deploy/rotate.py')
rotate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rotate)


class RotateTests(unittest.TestCase):
    def test_check_rotate_and_bundles_stay_paired(self):
        with tempfile.TemporaryDirectory() as folder:
            secrets = Path(folder) / 'net'
            secrets.mkdir()
            rotate.issue(secrets / 'certs', 3)
            old_gpu = (secrets / 'certs/gpu-bundle.b64').read_text()
            with mock.patch('sys.stdout'):
                self.assertEqual(rotate.main(['--secrets', str(secrets), '--check']), 1)
                self.assertEqual(rotate.main(['--secrets', str(secrets)]), 0)
                self.assertEqual(rotate.main(['--secrets', str(secrets), '--check']), 0)
            self.assertEqual(len(list(secrets.glob('certs-retired-*'))), 1)
            self.assertFalse((secrets / 'certs.new').exists())
            pins = json.loads((secrets / 'pins.json').read_text())
            gpu = json.loads(base64.b64decode((secrets / 'certs/gpu-bundle.b64').read_text()))
            node = json.loads(base64.b64decode((secrets / 'certs/node-bundle.b64').read_text()))
            self.assertNotEqual((secrets / 'certs/gpu-bundle.b64').read_text(), old_gpu)
            self.assertEqual(gpu['miner-rpc']['peer_sha256'], pins['node-rpc'])
            self.assertEqual(node['node-rpc']['peer_sha256'], pins['miner-rpc'])
            self.assertGreaterEqual(rotate.days_left(secrets / 'certs'), 29)

    def test_apply_refuses_to_change_only_one_side(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch('sys.stderr'):
            with self.assertRaises(SystemExit):
                rotate.main(['--secrets', folder, '--apply'])
            self.assertFalse((Path(folder) / 'certs').exists())

            with self.assertRaises(SystemExit):
                rotate.main(['--secrets', folder, '--apply', '--gpu-ssh', 'a', '--gpu-ssh', 'b',
                             '--gpu-host', 'h1', '--gpu-host', 'h2', '--gpu-host', 'h3'])
            self.assertFalse((Path(folder) / 'certs').exists())

    def test_apply_installs_the_same_bundle_on_every_gpu_host(self):
        self.assertEqual(rotate.gpu_hosts(['ssh'], ['a', 'b']), [('ssh', 'a'), ('ssh', 'b')])
        self.assertEqual(rotate.gpu_hosts(['s1', 's2'], ['a', 'b']), [('s1', 'a'), ('s2', 'b')])
        with tempfile.TemporaryDirectory() as folder:
            secrets = Path(folder)
            rotate.issue(secrets / 'certs', 3)
            calls = []
            with mock.patch.object(rotate.subprocess, 'run', lambda command, **kw: calls.append(command)):
                rotate.apply(secrets, 'noet-mainnet', rotate.gpu_hosts(['ssh'], ['root@a', 'root@b']))
        self.assertEqual(calls[0][:5], ['fly', 'secrets', 'import', '--app', 'noet-mainnet'])
        self.assertEqual([c[1] for c in calls[1:]], ['root@a', 'root@a', 'root@b', 'root@b'])

    def test_gpu_restart_spares_the_container_start_shell(self):
        # The start shell's command line contains "python3 checks/live.py";
        # only the anchored pattern may select processes.
        self.assertEqual(rotate.RESTART_GPU.count('live[.]py'), 1)
        self.assertIn("LIVE='^python3 checks/live[.]py'", rotate.RESTART_GPU)
        self.assertNotIn('checks/live.py"', rotate.RESTART_GPU)
        self.assertIn('setsid nohup bash output/live/rotate-restart.sh', rotate.RESTART_GPU)
        self.assertEqual(subprocess.run(['bash', '-n'], input=rotate.RESTART_GPU, text=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
