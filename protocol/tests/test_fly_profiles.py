import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('noet_fly_supervise_profiles', ROOT / 'deploy/fly/noet_node/supervise.py')
supervise = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervise)


class FlyProfileTests(unittest.TestCase):
    def test_default_is_the_closed_development_node(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(supervise.network(Path(folder) / 'missing.json')['hrp'], 'nwrk')
        self.assertIn('-listen=0', supervise.node_command({'miner': ''}))

    def test_testnet_profile_listens_for_peers_on_its_own_port(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'network.json'
            path.write_text(json.dumps({'profile': 'testnet'}))
            net = supervise.network(path)
        self.assertEqual((net['hrp'], net['p2pPort'], net['public']), ('tnoet', 33441, True))
        self.assertEqual(net['genesis'], '2da97e8dadb7fe27645263d3e732211cb67650adbc02a016102cc7390785cdaa')
        original = supervise.NETWORK
        try:
            supervise.NETWORK = net
            command = supervise.node_command({'miner': ''})
        finally:
            supervise.NETWORK = original
        self.assertIn('-listen=1', command)
        self.assertIn('-port=33441', command)
        self.assertIn('-connect=0', command)  # the testnet has a single node and no seeds

    def test_unknown_profiles_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'network.json'
            for data in ({'profile': 'other'}, {'profile': 'mainnet'},
                         {'profile': 'mainnet', 'genesis': 'x', 'hrp': 'noet', 'name': 'n', 'p2pPort': 1}):
                path.write_text(json.dumps(data))
                with self.subTest(data=data), self.assertRaises((KeyError, ValueError)):
                    supervise.network(path)

    def test_mainnet_reads_its_identity_and_refuses_a_faucet(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'network.json'
            path.write_text(json.dumps({'profile': 'mainnet', 'genesis': 'ab' * 32, 'hrp': 'noet',
                                        'name': 'noet-mainnet-1', 'p2pPort': 33440}))
            net = supervise.network(path)
        self.assertEqual((net['p2pPort'], net['faucet'], net['genesis']), (33440, False, 'ab' * 32))
        original = supervise.NETWORK
        try:
            supervise.NETWORK = net
            with self.assertRaisesRegex(ValueError, 'no faucet'):
                supervise.secrets_from_env({'NOET_FAUCET_TOKEN': 'f' * 40, 'NOET_FAUCET_PASSPHRASE': 'p' * 20,
                                            **self.base_env()})
        finally:
            supervise.NETWORK = original

    def test_mainnet_with_seeds_dials_out(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'network.json'
            path.write_text(json.dumps({'profile': 'mainnet', 'genesis': 'ab' * 32, 'hrp': 'noet',
                                        'name': 'noet-mainnet-1', 'p2pPort': 33440,
                                        'seeds': ['noet-mainnet.fly.dev']}))
            net = supervise.network(path)
        original = supervise.NETWORK
        try:
            supervise.NETWORK = net
            command = supervise.node_command({'miner': ''})
        finally:
            supervise.NETWORK = original
        self.assertIn('-dnsseed=1', command)
        self.assertNotIn('-connect=0', command)

    def base_env(self):
        import base64
        bundle = {n: {'cert': 'c', 'key': 'k', 'peer_sha256': 'p', 'server_name': 's'} for n in supervise.RELAY_NAMES}
        return {'NOET_EXPLORER_TOKEN': 't' * 40, 'VALIDATOR_SHARED_SECRET': 'a' * 64,
                'NOET_TLS_BUNDLE': base64.b64encode(json.dumps(bundle).encode()).decode()}


if __name__ == '__main__':
    unittest.main()
