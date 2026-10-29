"""Public mining gateway. Anyone may lease work paying their own address.

The node's RPC never leaves the host. This service exposes narrow calls:
network information, a work lease bound to the caller's reward address, proof
submission for that lease, and the node's verdict on a block. It also queues
useful work (`tasks.py`): anyone may queue a prompt, a miner may take one with
its lease and return the answer, and answers are public. The node itself
runs QuickVerify, including the proof-of-work target, before any submission
reaches the GPU verifier, so a junk proof costs the network almost nothing.
Leases carry a random secret so nobody can submit (or spoil) another miner's
lease. Per-client and global limits bound memory and node load.
"""
import base64
import binascii
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit
from .rpc import NodeRPC, RPCError
from .serve import bind_address
from .tasks import TaskRefused, open_store

HOUR = 3600
PROOF_LIMIT = 2 * 1024 * 1024
SUBMITS_PER_LEASE = 8
GROUP_FACTOR = 4  # an IPv6 /48 (one tunnel or site) gets four clients' worth
# One lease per attempt: a GPU attempt takes about 24 s (150 an hour), so a
# single GPU never waits on this; it still bounds work-unit requests per client.
LEASES_PER_HOUR = 360
INFO_SECONDS = 5
# How long a block accepted at a height is treated as awaiting its Full verdict
# (about 1.5 min with Qwen3-0.6B, 224-275 s for Qwen3-14B on an A100,
# validation/model-switch-rehearsal.json); afterwards leases are plain again.
VERIFYING_SECONDS = 420
BODY_SECONDS = 30
LARGE_BODY = 64 * 1024
LARGE_BODIES = 4  # concurrent large submissions held in memory
BLOCK = re.compile(r"[0-9a-f]{64}")
LEASE = re.compile(r"[0-9a-f]{32}")
SITE_CLIENT = re.compile(r"[0-9a-f]{16,64}")
TASK_PATH = re.compile(r"/tasks/([0-9a-f]{24})")
# Everything the gateway may ever ask the node. Nothing else is reachable.
METHODS = frozenset(('create_mining_work_unit', 'submit_mining_response', 'getrecentvalidations',
                     'getblockchaininfo', 'getblockhash', 'getblockheader', 'getmodelslist',
                     'validateaddress'))
ACCEPTED = ('accepted', 'accepted_pending_connect')


class Refused(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def task_lease(req_id, record):
    """Names one lease in the task store; req_id alone may repeat after a node restart."""
    return f"{req_id}:{record['secret'].hex()[:16]}"


class Window:
    """Sliding one-hour counters per key, pruned as they are touched."""

    def __init__(self):
        self.events = {}

    def take(self, key, limit, now):
        recent = [t for t in self.events.get(key, ()) if t > now - HOUR]
        if len(recent) >= limit:
            self.events[key] = recent
            return False
        recent.append(now)
        self.events[key] = recent
        return True

    def full(self, key, limit, now):
        return sum(1 for t in self.events.get(key, ()) if t > now - HOUR) >= limit

    def prune(self, now):
        self.events = {k: v for k, v in self.events.items() if v and v[-1] > now - HOUR}


class Gateway:
    def __init__(self, rpc, hrp, genesis, chain='tensor-reg', leases_per_hour=LEASES_PER_HOUR, submits_per_hour=120,
                 open_per_client=8, open_total=512, clock=time.time, tasks=None, tasks_per_hour=10,
                 site_tasks_per_hour=300, name=None):
        self._rpc, self.hrp, self.genesis, self.chain = rpc, hrp, genesis, chain
        # The public network name (noet-testnet-1, noet-mainnet-1); `chain` is
        # only the node's internal chain parameter set.
        self.name = name
        self.address = re.compile(re.escape(hrp) + r"1[023456789acdefghjklmnpqrstuvwxyz]{8,85}")
        self.leases_per_hour, self.submits_per_hour = leases_per_hour, submits_per_hour
        self.open_per_client, self.open_total, self.clock = open_per_client, open_total, clock
        # req_id -> {secret digest, client, group, address, expires}; while the node is
        # being asked, a ('pending', n) placeholder holds the slot.
        self.leases = {}
        self.lease_window, self.submit_window, self.read_window = Window(), Window(), Window()
        self.lock = threading.Lock()
        self.pending = 0
        self.cached_info = (0.0, None)
        self.tasks, self.tasks_per_hour, self.site_tasks_per_hour = tasks, tasks_per_hour, site_tasks_per_hour
        self.task_window = Window()
        self.stats = {'leased': 0, 'submitted': 0, 'accepted': 0, 'released': 0}
        # The newest block accepted through this gateway and still awaiting its Full
        # verdict: {'height', 'since'}. Work leased at or below that height can only
        # win if the block fails, so leases say so and miners may wait instead.
        self.verifying = None

    def rpc(self, method, *params):
        if method not in METHODS:
            raise RPCError('Method is not available through the mining gateway')
        return self._rpc(method, *params)

    def _expire(self, now):
        self.leases = {k: v for k, v in self.leases.items() if v['expires'] > now}

    def _awaiting(self, height, now):
        """The block awaiting its verdict at or above `height` (the tip + 1) as {'height',
        'seconds'}, or None; call with the lock held."""
        pending = self.verifying
        if pending is None or now - pending['since'] >= HOUR or (isinstance(height, int) and height > pending['height']):
            self.verifying = None
            return None
        return {'height': pending['height'], 'seconds': int(now - pending['since'])}

    def _verifying(self, height, now):
        """The hint for miners: only while a verdict is still due soon."""
        pending = self._awaiting(height, now)
        return pending if pending and pending['seconds'] < VERIFYING_SECONDS else None

    def info(self):
        now = self.clock()
        with self.lock:
            stamp, cached = self.cached_info
            if cached is not None and now - stamp < INFO_SECONDS:
                return {**cached, 'openLeases': len(self.leases), 'served': dict(self.stats)}
        fresh = self._info()
        with self.lock:
            self.cached_info = (now, fresh)
        return fresh

    def _info(self):
        chain = self.rpc('getblockchaininfo')
        if self.rpc('getblockhash', 0) != self.genesis:
            raise RPCError('Node is on an unexpected network')
        models = [{'model': m['model_name'] + '@' + m['model_commit'], 'difficulty': m['difficulty']}
                  for m in self.rpc('getmodelslist', False)]
        with self.lock:
            open_leases = len(self.leases)
        info = {'network': self.name, 'genesis': self.genesis, 'addressPrefix': self.hrp + '1',
                'height': chain['blocks'], 'bestBlock': chain['bestblockhash'], 'models': models,
                'limits': {'leasesPerHour': self.leases_per_hour, 'submitsPerHour': self.submits_per_hour,
                           'openLeases': self.open_per_client, 'proofBytes': PROOF_LIMIT},
                'openLeases': open_leases, 'served': dict(self.stats)}
        with self.lock:
            awaiting = self._awaiting(chain['blocks'] + 1, self.clock())
        if awaiting:
            # How long the oldest block accepted here has waited for its verdict: the
            # monitor alerts on a long wait (the verifier is down).
            info['awaitingVerdict'] = awaiting
            if awaiting['seconds'] < VERIFYING_SECONDS:
                info['verifying'] = awaiting
        if self.tasks:
            counts = self.tasks.counts()
            info['tasks'] = {'queued': counts.get('request:queued', 0) + counts.get('request:claimed', 0),
                             'answeredRequests': counts.get('request:answered', 0),
                             'answeredDataset': counts.get('dataset:answered', 0),
                             'tasksPerHour': self.tasks_per_hour}
        return info

    def lease(self, address, client, group=None, want_task=False):
        if not isinstance(address, str) or not self.address.fullmatch(address):
            raise Refused(400, f'Enter a reward address starting with {self.hrp}1')
        group = group or client
        now = self.clock()
        with self.lock:
            self._expire(now)
            if len(self.leases) >= self.open_total:
                raise Refused(503, 'The gateway is at capacity. Try again in a minute.')
            if sum(1 for v in self.leases.values() if v['client'] == client) >= self.open_per_client or \
                    sum(1 for v in self.leases.values() if v['group'] == group) >= self.open_per_client * GROUP_FACTOR:
                raise Refused(429, 'Too many open leases. Submit or let them expire first.')
            if self.lease_window.full(group, self.leases_per_hour * GROUP_FACTOR, now) or \
                    not self.lease_window.take(client, self.leases_per_hour, now):
                raise Refused(429, 'Hourly lease limit reached. Try again later.')
            if group != client:
                self.lease_window.take(group, self.leases_per_hour * GROUP_FACTOR, now)
            # Hold the slot while the node is asked, so parallel requests cannot overshoot.
            self.pending += 1
            slot = ('pending', self.pending)
            self.leases[slot] = {'secret': b'', 'client': client, 'group': group, 'expires': now + 120}
        try:
            info = self.rpc('validateaddress', address)
            if not info.get('isvalid') or not re.fullmatch(r'[0-9a-f]{4,200}', info.get('scriptPubKey', '')):
                raise Refused(400, 'This address is not valid on this network')
            job = self.rpc('create_mining_work_unit', self.chain, info['scriptPubKey'], secrets.token_hex(8))
            req_id = int(job['req_id'])
        finally:
            with self.lock:
                self.leases.pop(slot, None)
        secret = secrets.token_hex(16)
        expires = min(float(job.get('expires_at', now + 600)), now + 1800)
        height = job.get('height') if isinstance(job.get('height'), int) else None
        record = {'secret': hashlib.sha256(secret.encode()).digest(), 'client': client,
                  'group': group, 'address': address, 'expires': expires, 'submits': 0, 'height': height}
        with self.lock:
            self.leases[req_id] = record
            self.stats['leased'] += 1
            verifying = self._verifying(height, now) if height is not None else None
        lease = {'lease': secret, 'job': job, 'rewardAddress': address}
        if verifying:
            lease['verifying'] = verifying
        if want_task and self.tasks:
            # The miner runs this prompt as the inference behind its proof.
            lease['task'] = self.tasks.claim(task_lease(req_id, record), expires, address)
        return lease

    def may_submit(self, client):
        """Cheap check before a large body is read."""
        with self.lock:
            return not self.submit_window.full(client, self.submits_per_hour, self.clock())

    def submit(self, req_id, lease, proof, client):
        if not isinstance(req_id, int) or isinstance(req_id, bool) or not 0 <= req_id < 2 ** 63:
            raise Refused(400, 'Invalid work id')
        if not isinstance(lease, str) or not LEASE.fullmatch(lease):
            raise Refused(400, 'Invalid lease')
        if not isinstance(proof, str) or len(proof) > (PROOF_LIMIT * 4) // 3 + 4:
            raise Refused(413, 'Proof is too large')
        try:
            raw = base64.b64decode(proof, validate=True)
        except (binascii.Error, ValueError):
            raise Refused(400, 'Proof must be base64') from None
        if not 0 < len(raw) <= PROOF_LIMIT:
            raise Refused(413, 'Proof is too large')
        now = self.clock()
        with self.lock:
            self._expire(now)
            record = self.leases.get(req_id)
            if not record or not hmac.compare_digest(record['secret'], hashlib.sha256(lease.encode()).digest()):
                raise Refused(404, 'Unknown or expired lease')
            key = task_lease(req_id, record)
            if not self.submit_window.take(client, self.submits_per_hour, now):
                raise Refused(429, 'Hourly submission limit reached. Try again later.')
            # A lease may carry a few candidate proofs, never an open-ended probe.
            record['submits'] += 1
            if record['submits'] >= SUBMITS_PER_LEASE:
                del self.leases[req_id]
            self.stats['submitted'] += 1
        response = self.rpc('submit_mining_response', req_id, proof, 0)
        status = str(response.get('status', 'unknown'))
        if status in ACCEPTED or status in ('already_submitted', 'stale_tip', 'unknown_req_id'):
            with self.lock:
                self.leases.pop(req_id, None)
                self.stats['accepted'] += status in ACCEPTED
                if status == 'accepted_pending_connect' and record.get('height') is not None and \
                        (self.verifying is None or self.verifying['height'] != record['height']):
                    # A second block at the same height keeps the first one's start time.
                    self.verifying = {'height': record['height'], 'since': self.clock()}
            if self.tasks and status in ACCEPTED and BLOCK.fullmatch(str(response.get('block_hash', ''))):
                height = response.get('height')
                self.tasks.mined(key, response['block_hash'], height if isinstance(height, int) else None)
        result = {'status': status, 'accepted': bool(response.get('accepted'))}
        for key in ('block_hash', 'reject_reason', 'height'):
            if key in response:
                result[key] = str(response[key])[:300] if key == 'reject_reason' else response[key]
        return result

    def _held(self, req_id, lease):
        """The open lease record for (req_id, lease secret); call with the lock held."""
        if not isinstance(req_id, int) or isinstance(req_id, bool) or not 0 <= req_id < 2 ** 63:
            raise Refused(400, 'Invalid work id')
        if not isinstance(lease, str) or not LEASE.fullmatch(lease):
            raise Refused(400, 'Invalid lease')
        self._expire(self.clock())
        record = self.leases.get(req_id)
        if not record or not hmac.compare_digest(record['secret'], hashlib.sha256(lease.encode()).digest()):
            raise Refused(404, 'Unknown or expired lease')
        return record

    def release(self, req_id, lease):
        """Hand back a lease that produced nothing to submit, so its slot frees
        before it expires. The hourly lease limit still bounds how much work a
        client can request."""
        with self.lock:
            record = self._held(req_id, lease)
            del self.leases[req_id]
            self.stats['released'] += 1
        if self.tasks:
            self.tasks.released(task_lease(req_id, record))
        return {'released': True}

    def answer(self, req_id, lease, task, text, truncated=False):
        """The miner's answer to the task its lease holds. Sent before the proofs,
        whether or not the attempt finds a block."""
        if not self.tasks:
            raise Refused(404, 'Not found')
        with self.lock:
            record = self._held(req_id, lease)
        try:
            return self.tasks.answer(task_lease(req_id, record), task, text, bool(truncated))
        except TaskRefused as error:
            raise Refused(error.status, str(error)) from None

    def add_task(self, prompt, client, group=None, site=False):
        """Queue a prompt. Direct callers are limited per address; the website
        passes its own visitor key and shares one larger budget."""
        if not self.tasks:
            raise Refused(404, 'Not found')
        group = group or client
        now = self.clock()
        with self.lock:
            if site and self.task_window.full('site', self.site_tasks_per_hour, now):
                raise Refused(429, 'The network is answering as many requests as it can. Try again later.')
            if self.task_window.full(group, self.tasks_per_hour * GROUP_FACTOR, now) or \
                    not self.task_window.take(client, self.tasks_per_hour, now):
                raise Refused(429, 'Hourly request limit reached. Try again later.')
            if group != client:
                self.task_window.take(group, self.tasks_per_hour * GROUP_FACTOR, now)
            if site:
                self.task_window.take('site', self.site_tasks_per_hour, now)
        try:
            return self.tasks.add(prompt, client)
        except TaskRefused as error:
            raise Refused(error.status, str(error)) from None

    def task(self, task_id):
        if not self.tasks:
            raise Refused(404, 'Not found')
        try:
            return self.tasks.get(task_id)
        except TaskRefused as error:
            raise Refused(error.status, str(error)) from None

    def answered(self, query):
        if not self.tasks:
            raise Refused(404, 'Not found')
        params = parse_qs(query, max_num_fields=4)
        try:
            before = float(params['before'][0]) if 'before' in params else None
            limit = int(params.get('limit', ['50'])[0])
        except ValueError:
            raise Refused(400, 'Invalid query') from None
        try:
            return self.tasks.recent(params.get('kind', [None])[0], before, limit)
        except TaskRefused as error:
            raise Refused(error.status, str(error)) from None

    def block(self, block_hash, client='local', reads_per_hour=1800):
        if not isinstance(block_hash, str) or not BLOCK.fullmatch(block_hash):
            raise Refused(400, 'Invalid block hash')
        with self.lock:
            if not self.read_window.take(client, reads_per_hour, self.clock()):
                raise Refused(429, 'Too many block lookups. Try again later.')
        rows = [r for r in self.rpc('getrecentvalidations', 200) if r.get('block_hash') == block_hash]
        verdict = {'quick': None, 'full': None}
        for row in rows:
            if row.get('type') == 'full':
                verdict['full'] = row.get('full_status')
            elif row.get('quick_status'):
                verdict['quick'] = row.get('quick_status')
        try:
            header = self.rpc('getblockheader', block_hash)
            confirmations, height = int(header.get('confirmations', -1)), header.get('height')
        except RPCError:
            confirmations, height = -1, None
        return {'block': block_hash, 'height': height, 'inBestChain': confirmations > 0,
                'confirmations': max(confirmations, 0), **verdict}

    def prune(self):
        now = self.clock()
        with self.lock:
            self._expire(now)
            self.lease_window.prune(now)
            self.submit_window.prune(now)
            self.read_window.prune(now)
            self.task_window.prune(now)
        if self.tasks:
            self.tasks.prune()


def client_group(key):
    """The wider network a client key belongs to: the /48 for IPv6, itself for IPv4."""
    if '/' in key:
        return str(ipaddress.ip_network(key).supernet(new_prefix=48))
    return key


def client_key(handler, trust_proxy):
    """The caller's address, grouped to /64 for IPv6 so one host cannot rotate past limits."""
    host = handler.client_address[0]
    if trust_proxy:
        host = handler.headers.get('Fly-Client-IP', host)
    try:
        address = ipaddress.ip_address(host.strip())
    except ValueError:
        return 'unknown'
    if address.version == 6:
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f'{address}/64', strict=False))
    return str(address)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128  # the default of 5 resets bursts of miners connecting at once


def site_client(handler, token):
    """The website's visitor key when the request carries the website's token, else None."""
    sent = handler.headers.get('Authorization', '')
    if not token or not sent.startswith('Bearer ') or \
            not hmac.compare_digest(sent[7:].encode(), token.encode()):
        return None
    visitor = handler.headers.get('X-NOET-Client', '')
    if not SITE_CLIENT.fullmatch(visitor):
        raise Refused(400, 'Invalid client key')
    return 'site:' + visitor


def handler_for(gateway, trust_proxy, site_token=None):
    large = threading.BoundedSemaphore(LARGE_BODIES)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'noet-gateway/1'
        sys_version = ''
        timeout = 15  # request line and headers

        def read_body(self, length):
            """Read the whole body within BODY_SECONDS, however slowly it trickles in."""
            deadline, chunks, left = time.monotonic() + BODY_SECONDS, [], length
            while left:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Refused(408, 'Request body took too long')
                self.connection.settimeout(min(remaining, 15))
                try:
                    chunk = self.rfile.read1(min(left, 65536))
                except TimeoutError:
                    raise Refused(408, 'Request body took too long') from None
                if not chunk:
                    raise Refused(400, 'Incomplete request')
                chunks.append(chunk)
                left -= len(chunk)
            return b''.join(chunks)

        def do_GET(self):
            self.connection.settimeout(15)
            try:
                if self.path == '/healthz':
                    return self.respond(200, {'ok': True})
                if self.path == '/mining/info':
                    return self.respond(200, gateway.info())
                if self.path.startswith('/mining/blocks/'):
                    return self.respond(200, gateway.block(self.path.rsplit('/', 1)[1],
                                                           client_key(self, trust_proxy)))
                url = urlsplit(self.path)
                if url.path == '/tasks':
                    return self.respond(200, gateway.answered(url.query))
                found = TASK_PATH.fullmatch(url.path)
                if found:
                    return self.respond(200, gateway.task(found[1]))
                self.respond(404, {'error': 'Not found'})
            except Refused as error:
                self.respond(error.status, {'error': str(error)})
            except Exception:
                self.respond(503, {'error': 'The node is temporarily unavailable'})

        def do_POST(self):
            limit = {'/mining/work': 1024, '/mining/release': 1024, '/mining/answer': 48 * 1024,
                     '/tasks': 16 * 1024, '/mining/submit': (PROOF_LIMIT * 4) // 3 + 4096}.get(self.path)
            if limit is None:
                return self.respond(404, {'error': 'Not found'})
            held = False
            try:
                # A JSON content type forces a CORS preflight, which fails (no CORS
                # headers), so other websites cannot make visitors' browsers lease work.
                if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                    raise Refused(415, 'Send JSON with Content-Type: application/json')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= limit:
                    raise Refused(413 if length > limit else 400, 'Invalid request size')
                client = client_key(self, trust_proxy)
                if self.path == '/mining/submit':
                    if not gateway.may_submit(client):
                        raise Refused(429, 'Hourly submission limit reached. Try again later.')
                    if length > LARGE_BODY:
                        held = large.acquire(blocking=False)
                        if not held:
                            raise Refused(503, 'The gateway is busy. Try again in a few seconds.')
                body = json.loads(self.read_body(length))
                if not isinstance(body, dict):
                    raise Refused(400, 'Invalid request')
                if self.path == '/mining/work':
                    return self.respond(200, gateway.lease(body.get('address'), client, client_group(client),
                                                           body.get('tasks') is True))
                if self.path == '/mining/release':
                    return self.respond(200, gateway.release(body.get('reqId'), body.get('lease')))
                if self.path == '/mining/answer':
                    return self.respond(200, gateway.answer(body.get('reqId'), body.get('lease'), body.get('task'),
                                                            body.get('answer'), body.get('truncated') is True))
                if self.path == '/tasks':
                    site = site_client(self, site_token)
                    if site:
                        return self.respond(202, gateway.add_task(body.get('prompt'), site, site=True))
                    return self.respond(202, gateway.add_task(body.get('prompt'), client, client_group(client)))
                self.respond(200, gateway.submit(body.get('reqId'), body.get('lease'), body.get('proof'), client))
            except Refused as error:
                self.respond(error.status, {'error': str(error)})
            except (ValueError, UnicodeDecodeError):
                self.respond(400, {'error': 'Invalid request'})
            except Exception:
                self.respond(503, {'error': 'The node is temporarily unavailable'})
            finally:
                if held:
                    large.release()

        def respond(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    return Handler


def main():
    store = open_store(os.environ['NOET_TASKS_DB']) if os.environ.get('NOET_TASKS_DB') else None
    gateway = Gateway(NodeRPC(os.environ['NOET_RPC_URL'], os.environ['NOET_RPC_COOKIE']),
                      os.environ.get('NOET_ADDRESS_HRP', 'nwrk'), os.environ['NOET_GENESIS_HASH'],
                      leases_per_hour=int(os.environ.get('NOET_GATEWAY_LEASES_PER_HOUR', LEASES_PER_HOUR)),
                      submits_per_hour=int(os.environ.get('NOET_GATEWAY_SUBMITS_PER_HOUR', '120')),
                      tasks=store, tasks_per_hour=int(os.environ.get('NOET_GATEWAY_TASKS_PER_HOUR', '10')),
                      name=os.environ.get('NOET_NETWORK_NAME') or None)
    trust_proxy = os.environ.get('NOET_EXPLORER_TLS_PROXY') == '1'
    server = Server((bind_address(), int(os.environ.get('NOET_GATEWAY_PORT', '8791'))),
                    handler_for(gateway, trust_proxy, os.environ.get('NOET_SITE_TOKEN') or None))

    def housekeeping():
        while True:
            time.sleep(60)
            gateway.prune()
    threading.Thread(target=housekeeping, daemon=True).start()
    print(json.dumps({'event': 'gateway-ready', 'hrp': gateway.hrp}), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
