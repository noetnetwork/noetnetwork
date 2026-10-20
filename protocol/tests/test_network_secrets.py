import hashlib
import hmac
import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('noet_network_secrets', ROOT / 'deploy/network_secrets.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class NetworkSecretsTests(unittest.TestCase):
    def test_rpcauth_matches_the_node_format(self):
        line = module.rpcauth('miner', 'pw', salt='ab' * 16)
        user, rest = line.split(':')
        salt, digest = rest.split('$')
        self.assertEqual(user, 'miner')
        self.assertEqual(digest, hmac.new(salt.encode(), b'pw', hashlib.sha256).hexdigest())

    def test_generates_private_independent_sets(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder) / 'a', Path(folder) / 'b'
            module.generate(a)
            module.generate(b)
            self.assertEqual(stat.S_IMODE(a.stat().st_mode), 0o700)
            for name in ('explorer-token', 'faucet-token', 'faucet-passphrase', 'shared-secret',
                         'miner-credentials', 'miner-rpcauth'):
                self.assertEqual(stat.S_IMODE((a / name).stat().st_mode), 0o600)
                self.assertNotEqual((a / name).read_text(), (b / name).read_text())
            password = (a / 'miner-credentials').read_text().strip().split(':', 1)[1]
            auth = (a / 'miner-rpcauth').read_text().strip()
            salt = auth.split(':')[1].split('$')[0]
            self.assertEqual(auth, module.rpcauth('miner', password, salt))
            self.assertTrue((a / 'certs/node-bundle.b64').exists())
            self.assertTrue((a / 'certs/gpu-bundle.b64').exists())
            with self.assertRaises(FileExistsError):
                module.generate(a)

    def test_mainnet_set_has_no_faucet_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            files = module.generate(Path(folder) / 'main', faucet=False)
            self.assertNotIn('faucet-token', files)
            self.assertFalse((Path(folder) / 'main/faucet-passphrase').exists())
            self.assertTrue((Path(folder) / 'main/shared-secret').exists())


if __name__ == '__main__':
    unittest.main()
