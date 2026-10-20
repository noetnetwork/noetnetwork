import asyncio
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from noet import secure_transport as transport


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def openssl(folder, *args):
    subprocess.run(['openssl', *args], cwd=folder, check=True, capture_output=True, timeout=15)


def issue(folder, name, ca='ca', purpose='clientAuth'):
    openssl(folder, 'req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
        '-keyout', name+'.key', '-out', name+'.csr', '-subj', '/CN='+name)
    Path(folder, name+'.ext').write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage='+purpose+'\nsubjectAltName=DNS:'+name+'.noet.internal\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n')
    openssl(folder, 'x509', '-req', '-in', name+'.csr', '-CA', ca+'.pem', '-CAkey', ca+'.key',
        '-CAcreateserial', '-days', '1', '-extfile', name+'.ext', '-out', name+'.pem')
    Path(folder, name+'.key').chmod(0o600)


def pin(path):
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(Path(path).read_text())).hexdigest()


class SecureTransportTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='noet-tls-test-')
        cls.root = Path(cls.folder.name)
        for ca in ('ca', 'other'):
            openssl(cls.root, 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
                '-keyout', ca+'.key', '-out', ca+'.pem', '-days', '1', '-subj', '/CN=NOET transport test '+ca,
                '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
                '-addext', 'subjectKeyIdentifier=hash')
        issue(cls.root, 'verifier', purpose='serverAuth')
        issue(cls.root, 'node')
        issue(cls.root, 'other-node')
        issue(cls.root, 'untrusted', ca='other')

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.relays = []
        self.writers = []
        self.echo_tasks = set()
        self.received = bytearray()
        self.echo_connections = 0
        async def echo(reader, writer):
            task = asyncio.current_task(); self.echo_tasks.add(task)
            self.echo_connections += 1
            try:
                while data := await reader.read(65536):
                    self.received.extend(data)
                    writer.write(data); await writer.drain()
            finally:
                await transport.close_stream(writer)
                self.echo_tasks.discard(task)
        self.echo = await asyncio.start_server(echo, '127.0.0.1', 0)
        root = self.root
        self.server_config = transport.Config('server', '127.0.0.1', free_port(), '127.0.0.1',
            self.echo.sockets[0].getsockname()[1], str(root/'ca.pem'), str(root/'verifier.pem'),
            str(root/'verifier.key'), pin(root/'node.pem'), 'verifier.noet.internal')
        self.client_config = transport.Config('client', '127.0.0.1', free_port(), '127.0.0.1',
            self.server_config.listen_port, str(root/'ca.pem'), str(root/'node.pem'), str(root/'node.key'),
            pin(root/'verifier.pem'), 'verifier.noet.internal')

    async def asyncTearDown(self):
        for writer in self.writers:
            await transport.close_stream(writer)
        for relay in reversed(self.relays):
            await relay.close()
        self.echo.close(); await self.echo.wait_closed()
        tasks = tuple(self.echo_tasks)
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def relay(self, config):
        relay = await transport.Relay(config).start()
        self.relays.append(relay)
        return relay

    async def client(self, config=None):
        relay = await self.relay(config or self.client_config)
        reader, writer = await asyncio.open_connection('127.0.0.1', relay.config.listen_port)
        self.writers.append(writer)
        return reader, writer, relay

    async def test_real_tls_roundtrip_binary_stream_and_cleanup(self):
        server = await self.relay(self.server_config)
        reader, writer, client = await self.client()
        payload = bytes(range(256))*4096
        async def send():
            writer.write(payload); await writer.drain()
        sending = asyncio.create_task(send())
        self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)), 5), payload)
        await sending
        self.assertEqual(bytes(self.received), payload)
        self.assertEqual((client.accepted, server.accepted), (1, 1))
        await transport.close_stream(writer)
        await client.close(); await server.close()
        self.assertEqual((len(client.connections), len(server.connections)), (0, 0))

    async def test_wrong_client_pin_never_opens_plaintext_target(self):
        server = await self.relay(self.server_config)
        config = replace(self.client_config, cert_file=str(self.root/'other-node.pem'), key_file=str(self.root/'other-node.key'))
        reader, writer, _ = await self.client(config)
        writer.write(b'not authorized'); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
        self.assertEqual(self.echo_connections, 0)
        self.assertEqual(server.failed, 1)

    async def test_untrusted_client_certificate_never_opens_target(self):
        await self.relay(self.server_config)
        config = replace(self.client_config, cert_file=str(self.root/'untrusted.pem'), key_file=str(self.root/'untrusted.key'))
        reader, writer, _ = await self.client(config)
        writer.write(b'not trusted'); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
        self.assertEqual(self.echo_connections, 0)

    async def test_missing_client_certificate_never_opens_target(self):
        await self.relay(self.server_config)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_verify_locations(str(self.root/'ca.pem'))
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', self.server_config.listen_port,
                ssl=context, server_hostname='verifier.noet.internal')
            self.writers.append(writer)
            writer.write(b'no certificate'); await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
        except (ssl.SSLError, ConnectionResetError):
            pass
        self.assertEqual(self.echo_connections, 0)

    async def test_wrong_server_name_forwards_no_payload(self):
        await self.relay(self.server_config)
        reader, writer, client = await self.client(replace(self.client_config, server_name='wrong.noet.internal'))
        writer.write(b'wrong name'); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
        self.assertEqual(self.received, b'')
        self.assertEqual(client.failed, 1)

    async def test_wrong_server_pin_forwards_no_payload(self):
        await self.relay(self.server_config)
        reader, writer, client = await self.client(replace(self.client_config, peer_sha256='0'*64))
        writer.write(b'wrong pin'); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
        self.assertEqual(self.received, b'')
        self.assertEqual(client.failed, 1)

    async def test_tampered_certificate_signature_rejected(self):
        der = bytearray(ssl.PEM_cert_to_DER_cert((self.root/'verifier.pem').read_text()))
        der[-1] ^= 1
        with tempfile.TemporaryDirectory() as folder:
            cert = Path(folder)/'tampered.pem'; cert.write_text(ssl.DER_cert_to_PEM_cert(bytes(der)))
            await self.relay(replace(self.server_config, cert_file=str(cert)))
            reader, writer, client = await self.client(replace(self.client_config, peer_sha256=pin(cert)))
            writer.write(b'bad signature'); await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
            self.assertEqual(self.received, b'')
            self.assertEqual(client.failed, 1)

    async def test_server_certificate_cannot_be_used_as_client_identity(self):
        await self.relay(replace(self.server_config, peer_sha256=pin(self.root/'verifier.pem')))
        config = replace(self.client_config, cert_file=str(self.root/'verifier.pem'), key_file=str(self.root/'verifier.key'))
        reader, writer, _ = await self.client(config)
        writer.write(b'wrong certificate purpose'); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
        self.assertEqual(self.echo_connections, 0)

    async def test_legacy_tls_is_refused(self):
        await self.relay(self.server_config)
        context = transport.tls_context(self.client_config)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        with self.assertRaises((ssl.SSLError, ConnectionResetError)):
            await asyncio.open_connection('127.0.0.1', self.server_config.listen_port,
                ssl=context, server_hostname='verifier.noet.internal')
        self.assertEqual(self.echo_connections, 0)

    async def test_capacity_drops_extra_connection_and_recovers(self):
        await self.relay(self.server_config)
        with patch.object(transport, 'MAX_CONNECTIONS', 1):
            reader, writer, client = await self.client()
            writer.write(b'one'); await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(3), 3), b'one')
            extra, second = await asyncio.open_connection('127.0.0.1', client.config.listen_port)
            self.writers.append(second)
            self.assertEqual(await asyncio.wait_for(extra.read(), 3), b'')
            self.assertEqual(client.refused, 1)
            await transport.close_stream(writer)
            for _ in range(50):
                if not client.connections: break
                await asyncio.sleep(.01)
            self.assertFalse(client.connections)

    async def test_idle_timeout_closes_both_sides(self):
        with patch.object(transport, 'IDLE_SECONDS', .1):
            await self.relay(self.server_config)
            reader, writer, _ = await self.client()
            writer.write(b'hello'); await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(5), 3), b'hello')
            self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')

    async def test_session_lease_forces_reauthentication(self):
        with patch.object(transport, 'MAX_SESSION_SECONDS', .1):
            await self.relay(self.server_config)
            reader, writer, _ = await self.client()
            writer.write(b'lease'); await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(5), 3), b'lease')
            self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')

    async def test_incomplete_handshake_times_out_without_opening_target(self):
        with patch.object(transport, 'HANDSHAKE_SECONDS', .1):
            await self.relay(self.server_config)
            reader, writer = await asyncio.open_connection('127.0.0.1', self.server_config.listen_port)
            self.writers.append(writer)
            self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
            self.assertEqual(self.echo_connections, 0)

    async def test_stalled_handshakes_share_capacity_and_excess_never_starts_tls(self):
        loop = asyncio.get_running_loop()
        with patch.object(transport, 'MAX_CONNECTIONS', 2), patch.object(loop,
                'connect_accepted_socket', wraps=loop.connect_accepted_socket) as attach:
            server = await self.relay(self.server_config)
            stalled = []
            for _ in range(2):
                reader, writer = await asyncio.open_connection('127.0.0.1', self.server_config.listen_port)
                self.writers.append(writer); stalled.append((reader, writer))
            for _ in range(50):
                if attach.call_count == 2: break
                await asyncio.sleep(.01)
            self.assertEqual(len(server.connections), 2)
            self.assertEqual(attach.call_count, 2)
            for _ in range(16):
                extra, writer = await asyncio.open_connection('127.0.0.1', self.server_config.listen_port)
                self.writers.append(writer)
                self.assertEqual(await asyncio.wait_for(extra.read(), 1), b'')
            self.assertEqual(server.refused, 16)
            self.assertEqual(attach.call_count, 2)
            self.assertEqual(server.accepted, 0)
            self.assertEqual(self.echo_connections, 0)
            await transport.close_stream(stalled[0][1])
            for _ in range(50):
                if len(server.connections) == 1: break
                await asyncio.sleep(.01)
            self.assertEqual(len(server.connections), 1)
            reader, writer, _ = await self.client()
            writer.write(b'capacity recovered'); await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(18), 3), b'capacity recovered')
            self.assertEqual(len(server.connections), 2)

    async def test_shutdown_cancels_handshakes_and_releases_listener(self):
        server = await self.relay(self.server_config)
        stalled = []
        for _ in range(transport.MAX_CONNECTIONS):
            reader, writer = await asyncio.open_connection('127.0.0.1', self.server_config.listen_port)
            self.writers.append(writer); stalled.append(reader)
        await asyncio.sleep(.01)
        self.assertEqual(len(server.connections), transport.MAX_CONNECTIONS)
        await asyncio.wait_for(server.close(), 1)
        self.assertFalse(server.connections)
        self.assertTrue(server.accept_task.done())
        for reader in stalled:
            self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
        await self.relay(self.server_config)
        reader, writer, _ = await self.client()
        writer.write(b'new listener'); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(12), 3), b'new listener')

    async def test_rejected_handshakes_do_not_leak_admission_slots(self):
        server = await self.relay(self.server_config)
        for _ in range(12):
            reader, writer = await asyncio.open_connection('127.0.0.1', self.server_config.listen_port)
            self.writers.append(writer)
            writer.write(b'not a TLS handshake\r\n'); await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
        for _ in range(50):
            if not server.connections: break
            await asyncio.sleep(.01)
        self.assertFalse(server.connections)
        self.assertEqual(server.failed, 12)
        self.assertEqual(self.echo_connections, 0)
        reader, writer, _ = await self.client()
        writer.write(b'valid'); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(5), 3), b'valid')

    async def test_standby_target_takes_over_when_the_target_refuses(self):
        await self.relay(self.server_config)
        dead = ('127.0.0.1', free_port())
        config = replace(self.client_config, target_port=dead[1],
                         fallback_targets=[('127.0.0.1', self.server_config.listen_port)])
        reader, writer, client = await self.client(config)
        writer.write(b'standby'); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(7), 5), b'standby')
        self.assertIn(dead, client.down)
        self.assertEqual(client.candidates()[0], ('127.0.0.1', self.server_config.listen_port))

    async def test_target_whose_service_is_gone_is_skipped_until_it_cools_down(self):
        # The primary's relay is up and authenticates, but its verifier is not.
        broken = await self.relay(replace(self.server_config, listen_port=free_port(), target_port=free_port()))
        await self.relay(self.server_config)
        primary = ('127.0.0.1', broken.config.listen_port)
        config = replace(self.client_config, target_port=primary[1],
                         fallback_targets=[('127.0.0.1', self.server_config.listen_port)])
        reader, writer, client = await self.client(config)
        writer.write(b'first'); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), 5), b'')
        self.assertEqual(self.echo_connections, 0)
        self.assertIn(primary, client.down)
        reader, writer = await asyncio.open_connection('127.0.0.1', config.listen_port)
        self.writers.append(writer)
        writer.write(b'second'); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(6), 5), b'second')
        # After the cooldown the primary is tried first again.
        client.down[primary] = 0
        self.assertEqual(client.candidates()[0], primary)

    async def test_without_standby_a_down_target_is_still_retried(self):
        config = replace(self.client_config, target_port=free_port())
        reader, writer, client = await self.client(config)
        writer.write(b'x'); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), 5), b'')
        self.assertEqual(client.candidates(), [(config.target_host, config.target_port)])
        await self.relay(replace(self.server_config, listen_port=config.target_port))
        reader, writer = await asyncio.open_connection('127.0.0.1', config.listen_port)
        self.writers.append(writer)
        writer.write(b'back'); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(4), 5), b'back')
        self.assertNotIn((config.target_host, config.target_port), client.down)

    def test_fallback_targets_are_validated_like_the_target(self):
        port = self.server_config.listen_port
        for values in ({'fallback_targets': [('127.0.0.2', port)] * 2},
                       {'fallback_targets': [(self.client_config.target_host, self.client_config.target_port)]},
                       {'fallback_targets': [('127.0.0.1', p) for p in range(2001, 2005)]},
                       {'fallback_targets': [('203.0.113.9', port)]},
                       {'fallback_targets': [('127.0.0.2',)]},
                       {'fallback_targets': '127.0.0.2:1'}):
            with self.subTest(values=values), self.assertRaises(ValueError): replace(self.client_config, **values)
        with self.assertRaises(ValueError):
            replace(self.server_config, fallback_targets=[('127.0.0.2', port)])
        public = replace(self.client_config, target_host='203.0.113.7', public_tls=True,
                         fallback_targets=[['198.51.100.4', 40001]])
        self.assertEqual(public.fallback_targets, (('198.51.100.4', 40001),))
        with self.assertRaises(ValueError):
            replace(public, fallback_targets=[('224.0.0.1', 40001)])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.json'
            path.write_text(json.dumps(asdict(public))); path.chmod(0o600)
            self.assertEqual(transport.Config.read(path), public)

    def test_configuration_refuses_plaintext_remote_public_or_ambiguous(self):
        for values in ({'mode':'unknown'}, {'mode':'client','listen_host':'10.0.0.2'},
            {'target_host':'10.0.0.2'}, {'listen_host':'0.0.0.0'}, {'listen_host':'8.8.8.8'},
            {'listen_port':True}, {'listen_port':0}, {'peer_sha256':''}, {'peer_sha256':'a'*64+'\n'},
            {'server_name':''}, {'server_name':'name\ninvalid'}):
            with self.subTest(values=values), self.assertRaises(ValueError): replace(self.server_config, **values)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.json'
            path.write_text(json.dumps(asdict(self.server_config))); path.chmod(0o600)
            self.assertEqual(transport.Config.read(path), self.server_config)
            path.write_text('{"mode":"client","mode":"server"}')
            with self.assertRaisesRegex(ValueError,'Duplicate'): transport.Config.read(path)

    def test_public_tls_side_requires_explicit_opt_in_and_keeps_plaintext_local(self):
        public_server = replace(self.server_config, listen_host='0.0.0.0', public_tls=True)
        self.assertTrue(public_server.public_tls)
        public_client = replace(self.client_config, target_host='203.0.113.7', public_tls=True)
        self.assertEqual(public_client.target_host, '203.0.113.7')
        for config, values in ((self.server_config, {'listen_host':'0.0.0.0'}),
                (self.client_config, {'target_host':'203.0.113.7'}),
                (public_server, {'target_host':'203.0.113.7'}),
                (public_client, {'listen_host':'0.0.0.0'}),
                (public_client, {'target_host':'0.0.0.0'}),
                (public_client, {'target_host':'noet.example'}),
                (public_client, {'target_host':'224.0.0.1'}),
                (public_client, {'target_host':'169.254.1.1'}),
                (public_server, {'public_tls':'yes'})):
            with self.subTest(values=values), self.assertRaises(ValueError): replace(config, **values)

    def test_private_files_and_no_tls_key_log(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            secret = folder/'key'; secret.write_bytes(b'key'); secret.chmod(0o644)
            with self.assertRaises(ValueError): transport.regular_file(secret, private=True)
            secret.chmod(0o600)
            self.assertEqual(transport.regular_file(secret, private=True), b'key')
            link = folder/'link'; link.symlink_to(secret)
            with self.assertRaises(OSError): transport.regular_file(link)
            with self.assertRaises(ValueError): transport.regular_file(secret, limit=2)
            with self.assertRaises(ValueError): transport.regular_file('relative')
            log = folder/'session-keys'
            with patch.dict(os.environ, {'SSLKEYLOGFILE':str(log)}):
                context = transport.tls_context(self.client_config)
            self.assertIsNone(context.keylog_filename)
            self.assertFalse(log.exists())
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_3)
            self.assertTrue(context.verify_flags & ssl.VERIFY_X509_STRICT)
