"""Generated relay identities work end to end; node/GPU configs stay loopback-plaintext."""
import asyncio
import base64
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock

from noet import secure_transport as transport

ROOT = Path(__file__).resolve().parents[1]


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


certs = module(ROOT / 'deploy/certs.py', 'noet_deploy_certs')
supervise = module(ROOT / 'deploy/fly/noet_node/supervise.py', 'noet_fly_supervise')
live = module(ROOT / 'gpu/live.py', 'noet_gpu_live')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class LiveLinkTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='noet-live-test-')
        cls.root = Path(cls.folder.name)
        cls.pins = certs.issue(cls.root / 'certs', days=1)
        cls.node = json.loads(base64.b64decode((cls.root / 'certs/node-bundle.b64').read_bytes()))
        cls.gpu = json.loads(base64.b64decode((cls.root / 'certs/gpu-bundle.b64').read_bytes()))

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_bundles_pin_their_exact_peers_and_keep_ca_key_private(self):
        self.assertNotIn('ca.key', json.dumps(self.node))
        self.assertNotIn('PRIVATE KEY-----', self.node['ca'])
        self.assertEqual(self.node['node-rpc']['peer_sha256'], self.pins['miner-rpc'])
        self.assertEqual(self.gpu['miner-rpc']['peer_sha256'], self.pins['node-rpc'])
        self.assertEqual(self.node['node-request']['peer_sha256'], self.pins['verifier-request'])
        self.assertEqual(self.gpu['verifier-verdict']['peer_sha256'], self.pins['node-verdict'])
        self.assertEqual(set(self.node) - {'ca'}, {'node-request', 'node-verdict', 'node-rpc'})
        for path in (self.root / 'certs').iterdir():
            self.assertEqual(path.stat().st_mode & 0o077, 0, path.name)

    async def test_generated_identities_carry_miner_rpc_end_to_end(self):
        folder = self.root / 'run'
        received = bytearray()

        async def echo(reader, writer):
            while data := await reader.read(65536):
                received.extend(data)
                writer.write(data)
                await writer.drain()
            writer.close()
        target = await asyncio.start_server(echo, '127.0.0.1', 0)
        target_port = target.sockets[0].getsockname()[1]
        tls_port, local_port = free_port(), free_port()
        files = {}
        for side, name in (('node', 'node-rpc'), ('gpu', 'miner-rpc')):
            bundle = self.node if side == 'node' else self.gpu
            for suffix, value in (('.pem', bundle[name]['cert']), ('.key', bundle[name]['key'])):
                path = folder / (name + suffix)
                path.parent.mkdir(exist_ok=True)
                path.write_text(value)
                path.chmod(0o600)
                files[name + suffix] = str(path)
        (folder / 'ca.pem').write_text(self.node['ca'])
        common = {'ca_file': str(folder / 'ca.pem'), 'public_tls': True}
        server = transport.Config(mode='server', listen_host='0.0.0.0', listen_port=tls_port,
                                  target_host='127.0.0.1', target_port=target_port,
                                  cert_file=files['node-rpc.pem'], key_file=files['node-rpc.key'],
                                  peer_sha256=self.node['node-rpc']['peer_sha256'],
                                  server_name=self.node['node-rpc']['server_name'], **common)
        client = transport.Config(mode='client', listen_host='127.0.0.1', listen_port=local_port,
                                  target_host='127.0.0.1', target_port=tls_port,
                                  cert_file=files['miner-rpc.pem'], key_file=files['miner-rpc.key'],
                                  peer_sha256=self.gpu['miner-rpc']['peer_sha256'],
                                  server_name=self.gpu['miner-rpc']['server_name'], **common)
        relays = [transport.Relay(server), transport.Relay(client)]
        for relay in relays:
            await relay.start()
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', local_port)
            writer.write(b'{"method":"getblockcount"}')
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(26), 5), b'{"method":"getblockcount"}')
            writer.close()
        finally:
            for relay in relays:
                await relay.close()
            target.close()
            await target.wait_closed()

    def test_fly_supervisor_configs_and_secret_validation(self):
        env = {'NOET_EXPLORER_TOKEN': 't' * 40, 'VALIDATOR_SHARED_SECRET': 'a' * 64,
               'NOET_TLS_BUNDLE': (self.root / 'certs/node-bundle.b64').read_text(),
               'NOET_MINER_RPCAUTH': 'miner:' + '1' * 32 + '$' + '2' * 64,
               'NOET_VERIFIER_ENDPOINT': '203.0.113.9:40123'}
        secrets = supervise.secrets_from_env(env)
        with tempfile.TemporaryDirectory() as run, mock.patch.object(supervise, 'RUN', Path(run)):
            paths = supervise.relay_configs(secrets)
            configs = {name: transport.Config.read(path) for name, path in paths.items()}
        self.assertEqual(configs['node-request'].target_host, '203.0.113.9')
        self.assertEqual(configs['node-request'].listen_host, '127.0.0.1')
        self.assertEqual(configs['node-verdict'].target_host, '127.0.0.1')
        self.assertEqual(configs['node-rpc'].target_port, supervise.RPC_PORT)
        expires = int(supervise.explorer_env(secrets)['NOET_RELAY_CERT_EXPIRES'])
        self.assertTrue(0 < expires - __import__('time').time() <= 86400 + 60)  # this test's 1-day certificates
        command = supervise.node_command(secrets)
        self.assertIn('-rpcbind=127.0.0.1', command)
        self.assertIn('-validationapi=real', command)
        self.assertIn('-rpcwhitelistdefault=0', command)
        whitelist = next(c for c in command if c.startswith('-rpcwhitelist=miner:'))
        for forbidden in ('walletpassphrase', 'sendtoaddress', 'dumpprivkey', 'stop', 'createwallet'):
            self.assertNotIn(forbidden, whitelist)
        for bad in ({'VALIDATOR_SHARED_SECRET': 'short'}, {'NOET_EXPLORER_TOKEN': 'x'},
                    {'NOET_MINER_RPCAUTH': 'admin:x$y'}, {'NOET_VERIFIER_ENDPOINT': 'verifier.example:1'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                supervise.secrets_from_env({**env, **bad})
        without = supervise.secrets_from_env({**env, 'NOET_VERIFIER_ENDPOINT': ''})
        with tempfile.TemporaryDirectory() as run, mock.patch.object(supervise, 'RUN', Path(run)):
            self.assertNotIn('node-request', supervise.relay_configs(without))
        self.assertEqual(configs['node-request'].fallback_targets, ())
        # Standby verifiers follow the first endpoint, in order.
        standby = supervise.secrets_from_env({**env, 'NOET_VERIFIER_ENDPOINT': '203.0.113.9:40123, 198.51.100.4:41000'})
        with tempfile.TemporaryDirectory() as run, mock.patch.object(supervise, 'RUN', Path(run)):
            request = transport.Config.read(supervise.relay_configs(standby)['node-request'])
        self.assertEqual((request.target_host, request.target_port, request.fallback_targets),
                         ('203.0.113.9', 40123, (('198.51.100.4', 41000),)))
        for bad in ('203.0.113.9:1,', ','.join(['203.0.113.9:1'] * 5), '203.0.113.9:1,203.0.113.9:1',
                    '203.0.113.9:1,verifier.example:2'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                supervise.secrets_from_env({**env, 'NOET_VERIFIER_ENDPOINT': bad})

    def test_gpu_supervisor_relays_point_at_the_node_and_keep_plaintext_local(self):
        settings = {'node': '203.0.113.20', 'rewardAddress': 'nwrk1q' + 'q' * 38}
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(live.os, 'chown'):
            paths = live.relay_configs(settings, self.gpu, Path(folder))
            configs = {name: transport.Config.read(path) for name, path in paths.items()}
        self.assertEqual(configs['verifier-request'].mode, 'server')
        self.assertEqual(configs['verifier-request'].target_host, '127.0.0.1')
        self.assertEqual(configs['verifier-request'].listen_port, live.REQUEST_TLS_PORT)
        for name, port in (('verifier-verdict', 17443), ('miner-rpc', 18443)):
            self.assertEqual(configs[name].target_host, '203.0.113.20')
            self.assertEqual(configs[name].target_port, port)
            self.assertEqual(configs[name].listen_host, '127.0.0.1')
        # Each GPU relay pins the matching node identity.
        self.assertEqual(configs['miner-rpc'].peer_sha256, self.pins['node-rpc'])
        self.assertEqual(configs['verifier-request'].peer_sha256, self.pins['node-request'])

    def test_gpu_supervisor_rejects_incomplete_live_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'gpu-bundle.b64').write_text((self.root / 'certs/gpu-bundle.b64').read_text())
            (root / 'shared-secret').write_text('a' * 64)
            (root / 'miner-credentials').write_text('miner:' + 'x' * 32)
            (root / 'live.json').write_text(json.dumps({'node': '203.0.113.20', 'rewardAddress': 'nwrk1q' + 'q' * 38}))
            self.assertEqual(live.load(root)[0]['node'], '203.0.113.20')
            for name, value in (('shared-secret', 'short'), ('miner-credentials', 'admin:x'),
                                ('live.json', json.dumps({'node': 'node.example', 'rewardAddress': 'nwrk1q'}))):
                original = (root / name).read_text()
                (root / name).write_text(value)
                with self.subTest(name=name), self.assertRaises(ValueError):
                    live.load(root)
                (root / name).write_text(original)

    def test_gpu_supervisor_selects_the_network_and_its_addresses(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'gpu-bundle.b64').write_text((self.root / 'certs/gpu-bundle.b64').read_text())
            (root / 'shared-secret').write_text('a' * 64)
            (root / 'miner-credentials').write_text('miner:' + 'x' * 32)
            testnet = {'node': '203.0.113.20', 'rewardAddress': 'tnoet1q' + 'q' * 38, 'network': 'testnet',
                       'gateway': 'https://noet-testnet.fly.dev:9443'}
            (root / 'live.json').write_text(json.dumps(testnet))
            settings = live.load(root)[0]
            self.assertEqual(settings['network'], 'testnet')
            for bad in ({**testnet, 'rewardAddress': 'nwrk1q' + 'q' * 38}, {**testnet, 'gateway': ''},
                        {**testnet, 'network': 'mainnet'}):
                (root / 'live.json').write_text(json.dumps(bad))
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    live.load(root)
            # Mainnet mines through its gateway; a 2-of-3 vault (P2WSH) may take the reward.
            mainnet = {'node': '203.0.113.21', 'rewardAddress': 'noet1q' + 'q' * 58, 'network': 'mainnet',
                       'gateway': 'https://noet-mainnet.fly.dev:9443'}
            (root / 'live.json').write_text(json.dumps(mainnet))
            self.assertEqual(live.load(root)[0]['network'], 'mainnet')
            for bad in ({**mainnet, 'rewardAddress': 'tnoet1q' + 'q' * 38}, {**mainnet, 'gateway': ''}):
                (root / 'live.json').write_text(json.dumps(bad))
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    live.load(root)
        with mock.patch.object(live, 'OUT', Path('/tmp/out')):
            items = live.services(settings, 'a' * 64, {})
        miner = [item for item in items if item.name == 'miner'][0].command()
        self.assertIn('noet_miner.py', miner[2])
        self.assertEqual(miner[miner.index('--network') + 1], 'testnet')
        self.assertEqual([miner[i + 1] for i, part in enumerate(miner) if part == '--model'],
                         [str(live.ROOT / 'qwen3-0.6b-bf16.gguf')])
        # A verifier host (the 80 GB GPU for the 14B replay) runs the verifier and relays only.
        with mock.patch.object(live, 'OUT', Path('/tmp/out')):
            names = [item.name for item in live.services({**settings, 'mine': False}, 'a' * 64, {})]
        self.assertEqual(names, ['verifier'])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'gpu-bundle.b64').write_text((self.root / 'certs/gpu-bundle.b64').read_text())
            (root / 'shared-secret').write_text('a' * 64)
            (root / 'miner-credentials').write_text('miner:' + 'x' * 32)
            (root / 'live.json').write_text(json.dumps({**mainnet, 'mine': 'no'}))
            with self.assertRaisesRegex(ValueError, 'mine must be true or false'):
                live.load(root)
            (root / 'live.json').write_text(json.dumps({**mainnet, 'mine': False}))
            self.assertIs(live.load(root)[0]['mine'], False)

    def test_gpu_supervisor_restarts_quickly_after_a_long_run_and_backs_off_in_a_crash_loop(self):
        class Exited:
            pid, returncode = 1, 1

            def poll(self):
                return 1
        service = live.Service('verifier', lambda: ['true'], {}, 'verifier.log')
        delays = []
        with mock.patch.object(live, 'log'), mock.patch.object(live.subprocess, 'Popen', return_value=Exited()), \
                mock.patch.object(live, 'OUT', Path(self.enterContext(tempfile.TemporaryDirectory()))):
            now = 0.0
            service.poll(now)  # first start
            for ran in (5, 5, 5, live.HEALTHY_SECONDS, 5):
                now += ran
                service.poll(now)  # notices the exit
                delays.append(service.next_start - now)
                now = service.next_start
                service.poll(now)  # starts again
        # Crash loop: 2, 4, 8 s; after ten healthy minutes the delay starts over.
        self.assertEqual(delays, [2, 4, 8, 2, 4])

    def test_gpu_supervisor_crosses_a_model_switch_on_two_gpus(self):
        # design/MODEL-SWITCH.md: both GGUF files go to the miner; producer and verifier get their own GPU.
        settings = {'node': '203.0.113.21', 'rewardAddress': 'noet1q' + 'q' * 58, 'network': 'mainnet',
                    'gateway': 'https://noet-mainnet.fly.dev:9443', 'producerGpu': 0, 'verifierGpu': 1}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in live.GGUFS:
                (root / name).write_bytes(b'')
            with mock.patch.object(live, 'OUT', Path('/tmp/out')), mock.patch.object(live, 'ROOT', root):
                items = {item.name: item for item in live.services(settings, 'a' * 64, {})}
        miner = items['miner'].command()
        self.assertEqual([miner[i + 1] for i, part in enumerate(miner) if part == '--model'],
                         [str(root / name) for name in live.GGUFS])
        for name in ('miner', 'verifier'):
            self.assertEqual((items[name].env['NOET_PRODUCER_GPU'], items[name].env['NOET_VERIFIER_GPU']), ('0', '1'))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'gpu-bundle.b64').write_text((self.root / 'certs/gpu-bundle.b64').read_text())
            (root / 'shared-secret').write_text('a' * 64)
            (root / 'miner-credentials').write_text('miner:' + 'x' * 32)
            for bad in (10, -1, '1', True):
                (root / 'live.json').write_text(json.dumps({**settings, 'verifierGpu': bad}))
                with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'GPU index'):
                    live.load(root)


if __name__ == '__main__':
    unittest.main()
