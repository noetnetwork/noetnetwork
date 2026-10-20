"""Mutual-TLS transport for private verifier links; never expose plaintext remotely."""
import argparse
import asyncio
from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import tempfile
import time

from .verifier_transport import private_endpoint

BUFFER_BYTES = 64 * 1024
HANDSHAKE_SECONDS = 5
CONNECT_SECONDS = 5
IDLE_SECONDS = 120
MAX_CONNECTIONS = 4
MAX_SESSION_SECONDS = 3600
# A client relay may name standby verifiers. A target that refuses, fails its
# pin, or closes a fresh session without sending a byte is skipped for a while.
MAX_FALLBACKS = 3
SHORT_SESSION_SECONDS = 10
TARGET_COOLDOWN_SECONDS = 60


def public_endpoint(host, port, *, listening):
    """An explicit IP address for the encrypted side of a cross-provider link.
    Servers may listen on the unspecified address of a dedicated container;
    clients must target a concrete unicast address (no names, no broadcast)."""
    if not isinstance(host, str) or type(port) is not int or not 1 <= port <= 65535 or '%' in host:
        raise ValueError('Use an explicit IP address and valid port for the TLS endpoint')
    address = ipaddress.ip_address(host)
    if address.is_multicast or address.is_reserved or address.is_link_local:
        raise ValueError('Unsupported TLS endpoint address')
    if address.is_unspecified and not listening:
        raise ValueError('A TLS client needs a concrete peer address')
    return str(address)


def regular_file(path, *, private=False, limit=64 * 1024):
    """Read one bounded regular file without following a final symlink."""
    if os.name != 'posix':
        raise ValueError('Secure transport currently requires a POSIX host')
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('Transport files must use absolute paths')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('Invalid transport file')
        if info.st_mode & (0o077 if private else 0o022):
            raise ValueError('Transport file permissions are too broad')
        data = stream.read(limit + 1)
        if not data or len(data) > limit:
            raise ValueError('Invalid transport file size')
        return data


@dataclass(frozen=True)
class Config:
    mode: str
    listen_host: str
    listen_port: int
    target_host: str
    target_port: int
    ca_file: str
    cert_file: str
    key_file: str
    peer_sha256: str
    server_name: str
    # Cross-provider links without a private routed network. Only the encrypted
    # side may then use a public address; plaintext stays on loopback.
    public_tls: bool = False
    # Client mode only: standby targets, tried in order when the target is down.
    # All of them present the same pinned certificate.
    fallback_targets: tuple = ()

    def __post_init__(self):
        if self.mode not in ('client', 'server'):
            raise ValueError('Transport mode must be client or server')
        if type(self.public_tls) is not bool:
            raise ValueError('public_tls must be a boolean')
        if not isinstance(self.fallback_targets, (list, tuple)) or len(self.fallback_targets) > MAX_FALLBACKS:
            raise ValueError(f'At most {MAX_FALLBACKS} fallback targets')
        if self.fallback_targets and self.mode != 'client':
            raise ValueError('Only a client relay has fallback targets')
        fallbacks = []
        for item in self.fallback_targets:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError('A fallback target is [host, port]')
            host, port = item
            if self.public_tls:
                public_endpoint(host, port, listening=False)
            else:
                private_endpoint(host, port)
            fallbacks.append((host, port))
        if len(set(fallbacks + [(self.target_host, self.target_port)])) != len(fallbacks) + 1:
            raise ValueError('Fallback targets must differ from each other and from the target')
        object.__setattr__(self, 'fallback_targets', tuple(fallbacks))
        tls_side = ('target' if self.mode == 'client' else 'listen')
        for side in ('listen', 'target'):
            host, port = getattr(self, side + '_host'), getattr(self, side + '_port')
            if side == tls_side and self.public_tls:
                public_endpoint(host, port, listening=side == 'listen')
            else:
                private_endpoint(host, port)
        plaintext_host = self.listen_host if self.mode == 'client' else self.target_host
        if not ipaddress.ip_address(plaintext_host).is_loopback:
            raise ValueError('Plaintext endpoints must be loopback addresses')
        if not isinstance(self.peer_sha256, str) or not re.fullmatch('[a-f0-9]{64}', self.peer_sha256):
            raise ValueError('An exact peer certificate SHA-256 is required')
        if not isinstance(self.server_name, str) or not re.fullmatch(r'(?=.{1,253}$)[a-z0-9]+(?:[.-][a-z0-9]+)*', self.server_name):
            raise ValueError('An explicit TLS server name is required')

    @classmethod
    def read(cls, path):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate transport configuration field')
                result[key] = value
            return result
        return cls(**json.loads(regular_file(path, private=True), object_pairs_hook=unique))


def tls_context(config):
    # Construct explicitly: no system trust store or SSLKEYLOGFILE inheritance.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT if config.mode == 'client' else ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    context.verify_flags |= ssl.VERIFY_X509_STRICT
    context.options |= ssl.OP_NO_TICKET
    if config.mode == 'server':
        context.num_tickets = 0
    context.load_verify_locations(cadata=regular_file(config.ca_file).decode('ascii'))
    certificate = regular_file(config.cert_file)
    key = regular_file(config.key_file, private=True)
    # Load validated snapshots, avoiding a second read of mutable input paths.
    with tempfile.TemporaryDirectory(prefix='noet-tls-') as folder:
        cert_path = Path(folder)/'certificate.pem'
        key_path = Path(folder)/'key.pem'
        for path, content in ((cert_path, certificate), (key_path, key)):
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(content)
        def no_interactive_password():
            raise ValueError('Use a private service key from the secret store')
        context.load_cert_chain(cert_path, key_path, password=no_interactive_password)
    return context


def check_peer(writer, fingerprint):
    session = writer.get_extra_info('ssl_object')
    if session is None or session.version() != 'TLSv1.3':
        raise ValueError('Authenticated TLS 1.3 is required')
    certificate = session.getpeercert(binary_form=True)
    if not certificate or not hmac.compare_digest(hashlib.sha256(certificate).hexdigest(), fingerprint):
        raise ValueError('Peer certificate is not approved')
    expiry = ssl.cert_time_to_seconds(session.getpeercert()['notAfter'])
    remaining = expiry - time.time()
    if remaining <= 0:
        raise ValueError('Peer certificate has expired')
    return time.monotonic() + min(MAX_SESSION_SECONDS, remaining)


async def close_stream(writer):
    if writer is not None:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 2)
        except (OSError, TimeoutError):
            pass
        finally:
            writer.transport.abort()


class Relay:
    """Forward opaque ZMQ bytes only after certificate and pin verification."""
    def __init__(self, config):
        self.config = config
        self.context = tls_context(config)
        self.listener = None
        self.accept_task = None
        self.closing = False
        self.connections = {}
        self.accepted = self.refused = self.failed = 0
        # (host, port) -> monotonic time until which a failed target is skipped.
        self.down = {}

    async def start(self):
        if self.closing or self.listener is not None:
            raise ValueError('Start a fresh relay instance')
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.config.listen_host, self.config.listen_port))
            listener.listen(MAX_CONNECTIONS)
            listener.setblocking(False)
        except BaseException:
            listener.close()
            raise
        self.listener = listener
        self.accept_task = asyncio.create_task(self.accept_connections())
        return self

    async def accept_connections(self):
        loop = asyncio.get_running_loop()
        while not self.closing:
            raw, _ = await loop.sock_accept(self.listener)
            # Reserve before constructing a stream or SSL protocol. Incomplete
            # handshakes and established connections share this same budget.
            if self.closing or len(self.connections) >= MAX_CONNECTIONS:
                self.refused += 1
                raw.close()
            else:
                task = asyncio.create_task(self.handle(raw))
                self.connections[task] = raw
                task.add_done_callback(self.connection_done)
            # Even a permanently full accept queue must let existing sessions
            # make progress and release their slots.
            await asyncio.sleep(0)

    def connection_done(self, task):
        raw = self.connections.pop(task, None)
        if raw is not None:
            raw.close()  # Also covers cancellation before the task first runs.
        if not task.cancelled() and task.exception() is not None:
            self.failed += 1  # Never log peer payloads or certificate details.

    async def pump(self, reader, writer, counted=None):
        writer.transport.set_write_buffer_limits(high=BUFFER_BYTES, low=BUFFER_BYTES//2)
        while True:
            data = await asyncio.wait_for(reader.read(BUFFER_BYTES), IDLE_SECONDS)
            if not data:
                return
            if counted is not None:
                counted[0] += len(data)
            writer.write(data)
            await asyncio.wait_for(writer.drain(), IDLE_SECONDS)

    def candidates(self):
        """Targets in configured order, healthy ones first; never none."""
        targets = [(self.config.target_host, self.config.target_port), *self.config.fallback_targets]
        now = time.monotonic()
        return [t for t in targets if self.down.get(t, 0) <= now] + [t for t in targets if self.down.get(t, 0) > now]

    async def open_target(self):
        """Client mode: the first target that connects and presents the pinned certificate."""
        config = self.config
        options = dict(ssl=self.context, server_hostname=config.server_name,
            ssl_handshake_timeout=HANDSHAKE_SECONDS, ssl_shutdown_timeout=2)
        failure = None
        for host, port in self.candidates():
            target = None
            try:
                upstream, target = await asyncio.wait_for(asyncio.open_connection(
                    host, port, limit=BUFFER_BYTES, **options), CONNECT_SECONDS)
                deadline = check_peer(target, config.peer_sha256)
                self.down.pop((host, port), None)
                return upstream, target, deadline, (host, port)
            except (OSError, ValueError, TimeoutError) as error:
                failure = error
                self.down[(host, port)] = time.monotonic() + TARGET_COOLDOWN_SECONDS
                if target is not None:
                    target.transport.abort()
        raise failure

    async def handle(self, raw):
        writer = None
        target = None
        pumps = []
        try:
            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader(limit=BUFFER_BYTES)
            protocol = asyncio.StreamReaderProtocol(reader)
            incoming = {}
            if self.config.mode == 'server':
                incoming = dict(ssl=self.context, ssl_handshake_timeout=HANDSHAKE_SECONDS,
                    ssl_shutdown_timeout=2)
            channel, _ = await loop.connect_accepted_socket(lambda: protocol, raw, **incoming)
            writer = asyncio.StreamWriter(channel, protocol, reader, loop)
            config = self.config
            if config.mode == 'server':
                deadline = check_peer(writer, config.peer_sha256)
                upstream, target = await asyncio.wait_for(asyncio.open_connection(
                    config.target_host, config.target_port, limit=BUFFER_BYTES), CONNECT_SECONDS)
            else:
                upstream, target, deadline, chosen = await self.open_target()
            self.accepted += 1
            started, answered = time.monotonic(), [0]
            pumps = [asyncio.create_task(self.pump(reader, target)),
                     asyncio.create_task(self.pump(upstream, writer, answered))]
            # ZMQ uses persistent full-duplex connections. Either EOF closes the
            # entire link; a failed channel must reconnect, never fall back.
            try:
                done, _ = await asyncio.wait(pumps, timeout=max(0, deadline-time.monotonic()), return_when=asyncio.FIRST_COMPLETED)
                for completed in done:
                    completed.result()
            finally:
                # A target whose relay accepts but whose service behind it is
                # gone closes at once without a byte: try the next one first.
                if config.mode == 'client' and not answered[0] and time.monotonic() - started < SHORT_SESSION_SECONDS:
                    self.down[chosen] = time.monotonic() + TARGET_COOLDOWN_SECONDS
        except (OSError, ValueError, TimeoutError):
            self.failed += 1
        finally:
            for pump in pumps:
                pump.cancel()
            try:
                await asyncio.gather(*pumps, return_exceptions=True)
                await asyncio.gather(close_stream(writer), close_stream(target))
            finally:
                # A concurrent shutdown can cancel graceful TLS close. Always
                # release sockets and the admission slot, even in that race.
                if writer is not None:
                    writer.transport.abort()
                if target is not None:
                    target.transport.abort()

    async def close(self):
        self.closing = True
        if self.accept_task:
            self.accept_task.cancel()
            await asyncio.gather(self.accept_task, return_exceptions=True)
        if self.listener:
            self.listener.close()
        tasks = tuple(self.connections)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Done callbacks normally run during gather; explicit cleanup also
        # makes shutdown safe if cancellation preceded connection startup.
        for task in tasks:
            if task in self.connections:
                self.connection_done(task)


async def serve(config):
    relay = await Relay(config).start()
    try:
        await relay.accept_task
    finally:
        await relay.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(serve(Config.read(args.config)))
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, TypeError):
        # Never print key material, payloads or config paths on startup failure.
        parser.exit(1, 'NOET secure transport could not start. Check its private configuration.\n')


if __name__ == '__main__':
    main()
