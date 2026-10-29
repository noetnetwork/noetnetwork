import base64
from http.server import ThreadingHTTPServer
import json
import socket
import threading
import time
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from noet import gateway as gateway_module
from noet.gateway import Gateway, Refused, client_group, client_key, handler_for
from noet.rpc import RPCError

GENESIS = "ab" * 32
ADDRESS = "tnoet1q" + "q" * 38
OTHER = "tnoet1q" + "p" * 38
BLOCK = "cd" * 32


class Node:
    def __init__(self):
        self.calls, self.req, self.status, self.expires = [], 0, "accepted_pending_connect", 10_000

    def __call__(self, method, *params):
        self.calls.append((method, params))
        if method == "validateaddress":
            ok = params[0].startswith("tnoet1")
            return {"isvalid": ok, "scriptPubKey": "0014" + "11" * 20} if ok else {"isvalid": False}
        if method == "create_mining_work_unit":
            self.req += 1
            return {"req_id": self.req, "height": 7, "header_prefix": "00" * 76, "target": "ff" * 32,
                    "expires_at": self.expires}
        if method == "submit_mining_response":
            return {"status": self.status, "accepted": False, "block_hash": BLOCK}
        if method == "getblockchaininfo":
            return {"chain": "tensor-reg", "blocks": 6, "bestblockhash": "ef" * 32}
        if method == "getblockhash":
            return GENESIS
        if method == "getmodelslist":
            return [{"model_name": "Qwen/Qwen3-0.6B", "model_commit": "c1899de", "difficulty": 1000000}]
        if method == "getrecentvalidations":
            return [{"block_hash": BLOCK, "type": "quick", "quick_status": "green"},
                    {"block_hash": BLOCK, "type": "full", "full_status": "green"}]
        if method == "getblockheader":
            return {"confirmations": 2, "height": 7}
        raise AssertionError(method)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def proof(size=64):
    return base64.b64encode(b"p" * size).decode()


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.node, self.clock = Node(), Clock()
        self.gateway = Gateway(self.node, "tnoet", GENESIS, leases_per_hour=3, submits_per_hour=20,
                               open_per_client=2, open_total=3, clock=self.clock)

    def test_lease_pays_the_callers_own_address(self):
        lease = self.gateway.lease(ADDRESS, "1.2.3.4")
        self.assertEqual(lease["rewardAddress"], ADDRESS)
        create = [c for c in self.node.calls if c[0] == "create_mining_work_unit"][0]
        self.assertEqual(create[1][:2], ("tensor-reg", "0014" + "11" * 20))
        self.assertRegex(lease["lease"], "^[0-9a-f]{32}$")

    def test_leases_behind_a_block_awaiting_its_verdict_say_so(self):
        self.gateway.leases_per_hour = 10
        first = self.gateway.lease(ADDRESS, "a")
        self.assertNotIn("verifying", first)
        self.gateway.submit(first["job"]["req_id"], first["lease"], proof(), "a")
        self.clock.now += 40
        second = self.gateway.lease(ADDRESS, "b")
        self.assertEqual(second["verifying"], {"height": 7, "seconds": 40})
        self.assertEqual(self.gateway._info()["verifying"]["height"], 7)
        # Past the hold the hint goes, whatever the verdict; the wait stays visible for the monitor.
        self.clock.now += gateway_module.VERIFYING_SECONDS
        self.assertNotIn("verifying", self.gateway.lease(OTHER, "c"))
        info = self.gateway._info()
        self.assertNotIn("verifying", info)
        self.assertEqual(info["awaitingVerdict"], {"height": 7, "seconds": 460})
        self.clock.now += 3600
        self.assertNotIn("awaitingVerdict", self.gateway._info())

    def test_the_hint_clears_once_the_tip_moves_past_the_block(self):
        self.gateway.leases_per_hour = 10
        first = self.gateway.lease(ADDRESS, "a")
        self.gateway.submit(first["job"]["req_id"], first["lease"], proof(), "a")
        original = self.node.__call__

        class Moved(Node):
            def __call__(inner, method, *params):
                result = original(method, *params)
                if method == "create_mining_work_unit":
                    result["height"] = 8
                return result
        self.gateway._rpc = Moved()
        self.assertNotIn("verifying", self.gateway.lease(ADDRESS, "b"))
        self.assertIsNone(self.gateway.verifying)

    def test_rejects_other_networks_and_bad_addresses(self):
        for address in ("nwrk1q" + "q" * 38, "tnoet1Q" + "q" * 38, "", None, 5):
            with self.assertRaises(Refused):
                self.gateway.lease(address, "1.2.3.4")

    def test_open_lease_and_hourly_limits(self):
        self.node.expires = 1050
        self.gateway.lease(ADDRESS, "a")
        self.gateway.lease(ADDRESS, "a")
        with self.assertRaises(Refused) as caught:
            self.gateway.lease(ADDRESS, "a")
        self.assertEqual(caught.exception.status, 429)
        self.gateway.lease(OTHER, "b")
        with self.assertRaises(Refused) as caught:
            self.gateway.lease(OTHER, "c")
        self.assertEqual(caught.exception.status, 503)
        self.clock.now = 1100  # leases expired; the hourly window still counts "a"
        self.gateway.lease(ADDRESS, "a")
        with self.assertRaises(Refused):
            self.gateway.lease(ADDRESS, "a")

    def test_an_unused_lease_can_be_handed_back(self):
        first = self.gateway.lease(ADDRESS, "a")
        self.gateway.lease(ADDRESS, "a")
        req = first["job"]["req_id"]
        with self.assertRaises(Refused) as caught:
            self.gateway.release(req, "0" * 32)
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(self.gateway.release(req, first["lease"]), {"released": True})
        # The slot is free again, but the hourly window still counts the lease.
        self.gateway.lease(ADDRESS, "a")
        with self.assertRaises(Refused) as caught:
            self.gateway.release(req, first["lease"])
        self.assertEqual(caught.exception.status, 404)
        with self.assertRaises(Refused) as caught:
            self.gateway.lease(ADDRESS, "b")
            self.gateway.lease(ADDRESS, "b")
        self.assertEqual(self.gateway.info()["served"]["released"], 1)

    def test_submission_needs_the_lease_secret(self):
        lease = self.gateway.lease(ADDRESS, "a")
        req = lease["job"]["req_id"]
        with self.assertRaises(Refused) as caught:
            self.gateway.submit(req, "0" * 32, proof(), "b")
        self.assertEqual(caught.exception.status, 404)
        self.assertFalse(any(c[0] == "submit_mining_response" for c in self.node.calls))
        result = self.gateway.submit(req, lease["lease"], proof(), "b")
        self.assertEqual(result["status"], "accepted_pending_connect")
        self.assertEqual(result["block_hash"], BLOCK)
        # An accepted lease is closed.
        with self.assertRaises(Refused):
            self.gateway.submit(req, lease["lease"], proof(), "b")

    def test_rejected_candidates_may_retry_a_few_times(self):
        self.node.status = "quick_verify_failed"
        lease = self.gateway.lease(ADDRESS, "a")
        for _ in range(8):
            self.gateway.submit(lease["job"]["req_id"], lease["lease"], proof(), "a")
        with self.assertRaises(Refused):
            self.gateway.submit(lease["job"]["req_id"], lease["lease"], proof(), "a")

    def test_payload_validation(self):
        lease = self.gateway.lease(ADDRESS, "a")
        req = lease["job"]["req_id"]
        for args in ((True, lease["lease"], proof()), (-1, lease["lease"], proof()), (req, "x", proof()),
                     (req, lease["lease"], "not base64!"), (req, lease["lease"], ""),
                     (req, lease["lease"], proof(2 * 1024 * 1024 + 1))):
            with self.assertRaises(Refused):
                self.gateway.submit(*args, "a")

    def test_expired_leases_are_refused(self):
        lease = self.gateway.lease(ADDRESS, "a")
        self.clock.now = 10_001
        with self.assertRaises(Refused):
            self.gateway.submit(lease["job"]["req_id"], lease["lease"], proof(), "a")

    def test_only_mining_methods_reach_the_node(self):
        with self.assertRaises(RPCError):
            self.gateway.rpc("sendtoaddress", ADDRESS, "1")
        with self.assertRaises(RPCError):
            self.gateway.rpc("stop")

    def test_info_and_block_verdict(self):
        info = self.gateway.info()
        self.assertEqual(info["genesis"], GENESIS)
        self.assertEqual(info["addressPrefix"], "tnoet1")
        verdict = self.gateway.block(BLOCK)
        self.assertEqual((verdict["quick"], verdict["full"], verdict["inBestChain"]), ("green", "green", True))
        with self.assertRaises(Refused):
            self.gateway.block("../../etc")

    def test_info_names_the_public_network_not_the_chain_parameters(self):
        self.assertIsNone(self.gateway.info()["network"])
        named = Gateway(self.node, "tnoet", GENESIS, name="noet-testnet-1")
        self.assertEqual(named.info()["network"], "noet-testnet-1")

    def test_info_is_cached_briefly(self):
        self.gateway.info()
        self.gateway.info()
        self.assertEqual(sum(1 for c in self.node.calls if c[0] == "getblockchaininfo"), 1)
        self.clock.now += gateway_module.INFO_SECONDS
        self.gateway.info()
        self.assertEqual(sum(1 for c in self.node.calls if c[0] == "getblockchaininfo"), 2)

    def test_info_counts_miners_by_version_for_a_day(self):
        self.gateway.leases_per_hour, self.gateway.open_total = 10, 10
        self.gateway.lease(ADDRESS, "a", agent="noet-miner/0.1.0")
        self.gateway.lease(ADDRESS, "a", agent="noet-miner/0.2.0")
        self.gateway.lease(ADDRESS, "b", agent="noet-miner/0.1.0")
        self.gateway.lease(ADDRESS, "c", agent="curl/8.0")
        self.assertEqual(self.gateway.info()["minerVersions"], {"0.1.0": 1, "0.2.0": 1})
        self.clock.now += 24 * 3600
        self.assertEqual(self.gateway.info()["minerVersions"], {})

    def test_block_lookups_are_rate_limited(self):
        for _ in range(3):
            self.gateway.block(BLOCK, "a", reads_per_hour=3)
        with self.assertRaises(Refused) as caught:
            self.gateway.block(BLOCK, "a", reads_per_hour=3)
        self.assertEqual(caught.exception.status, 429)
        self.gateway.block(BLOCK, "b", reads_per_hour=3)

    def test_default_limits_never_hold_back_one_gpu(self):
        # A GPU attempt takes about 24 s; each one leases, then submits or releases.
        gateway = Gateway(self.node, "tnoet", GENESIS, clock=self.clock)
        for _ in range(2 * 3600 // 24):
            lease = gateway.lease(ADDRESS, "a")
            gateway.release(lease["job"]["req_id"], lease["lease"])
            self.clock.now += 24
        self.assertEqual(gateway.leases_per_hour, gateway_module.LEASES_PER_HOUR)
        for _ in range(gateway.leases_per_hour):
            lease = gateway.lease(ADDRESS, "b")
            gateway.release(lease["job"]["req_id"], lease["lease"])
        with self.assertRaises(Refused) as caught:
            gateway.lease(ADDRESS, "b")
        self.assertEqual(caught.exception.status, 429)

    def test_ipv6_tunnel_cannot_multiply_its_limits(self):
        gateway = Gateway(self.node, "tnoet", GENESIS, leases_per_hour=100, open_per_client=1, open_total=100,
                          clock=self.clock)
        group = client_group(client_key(FakeHandler("2001:db8:1::1"), False))
        clients = [client_key(FakeHandler(f"2001:db8:1:{i:x}::1"), False) for i in range(1, 8)]
        self.assertTrue(all(client_group(c) == group for c in clients))
        for client in clients[:gateway_module.GROUP_FACTOR]:
            gateway.lease(ADDRESS, client, client_group(client))
        with self.assertRaises(Refused) as caught:
            gateway.lease(ADDRESS, clients[-1], client_group(clients[-1]))
        self.assertEqual(caught.exception.status, 429)
        other = client_key(FakeHandler("2001:db8:2::1"), False)
        gateway.lease(ADDRESS, other, client_group(other))

    def test_parallel_leases_cannot_overshoot_the_open_limit(self):
        release, entered = threading.Event(), threading.Semaphore(0)
        node = self.node

        def slow(method, *params):
            if method == "create_mining_work_unit":
                entered.release()
                release.wait(5)
            return node(method, *params)
        gateway = Gateway(slow, "tnoet", GENESIS, leases_per_hour=100, open_per_client=2, open_total=100,
                          clock=self.clock)
        results = []

        def attempt():
            try:
                results.append(gateway.lease(ADDRESS, "a"))
            except Refused as error:
                results.append(error.status)
        threads = [threading.Thread(target=attempt) for _ in range(6)]
        for thread in threads:
            thread.start()
        entered.acquire(timeout=5)
        entered.acquire(timeout=5)
        time.sleep(0.1)
        release.set()
        for thread in threads:
            thread.join(5)
        self.assertEqual(sum(isinstance(r, dict) for r in results), 2)
        self.assertEqual(results.count(429), 4)
        self.assertEqual(len(gateway.leases), 2)

    def test_a_failed_node_call_frees_the_reserved_slot(self):
        def broken(method, *params):
            if method == "create_mining_work_unit":
                raise RPCError("Node RPC unavailable")
            return self.node(method, *params)
        gateway = Gateway(broken, "tnoet", GENESIS, leases_per_hour=100, open_per_client=1, clock=self.clock)
        with self.assertRaises(RPCError):
            gateway.lease(ADDRESS, "a")
        self.assertEqual(gateway.leases, {})

    def test_info_refuses_a_different_network(self):
        gateway = Gateway(self.node, "tnoet", "00" * 32, clock=self.clock)
        with self.assertRaises(RPCError):
            gateway.info()


class FakeHandler:
    def __init__(self, host, header=None):
        self.client_address = (host, 1)
        self.headers = {"Fly-Client-IP": header} if header else {}


class ClientKeyTests(unittest.TestCase):
    def test_proxy_header_only_when_trusted(self):
        self.assertEqual(client_key(FakeHandler("10.0.0.1", "8.8.8.8"), True), "8.8.8.8")
        self.assertEqual(client_key(FakeHandler("10.0.0.1", "8.8.8.8"), False), "10.0.0.1")

    def test_ipv6_is_grouped_by_prefix(self):
        a = client_key(FakeHandler("2001:db8::1"), False)
        b = client_key(FakeHandler("2001:db8::ffff"), False)
        self.assertEqual(a, b)
        self.assertEqual(client_key(FakeHandler("::ffff:1.2.3.4"), False), "1.2.3.4")
        self.assertEqual(client_key(FakeHandler("garbage"), False), "unknown")


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.node = Node()
        gateway = Gateway(self.node, "tnoet", GENESIS, clock=Clock())
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(gateway, False))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_round_trip(self):
        status, info = self.call("/mining/info")
        self.assertEqual((status, info["height"]), (200, 6))
        status, lease = self.call("/mining/work", {"address": ADDRESS})
        self.assertEqual(status, 200)
        status, result = self.call("/mining/submit", {"reqId": lease["job"]["req_id"], "lease": lease["lease"],
                                                      "proof": proof()})
        self.assertEqual((status, result["status"]), (200, "accepted_pending_connect"))
        status, verdict = self.call(f"/mining/blocks/{BLOCK}")
        self.assertEqual((status, verdict["full"]), (200, "green"))

    def test_release_over_http(self):
        status, lease = self.call("/mining/work", {"address": ADDRESS})
        body = {"reqId": lease["job"]["req_id"], "lease": lease["lease"]}
        self.assertEqual(self.call("/mining/release", body), (200, {"released": True}))
        self.assertEqual(self.call("/mining/release", body)[0], 404)

    def raw(self, head, body=b"", pause=0.0):
        with socket.create_connection(self.server.server_address, timeout=10) as sock:
            sock.sendall(head)
            try:
                for byte in body:
                    if pause:
                        time.sleep(pause)
                    sock.sendall(bytes([byte]))
            except (BrokenPipeError, ConnectionResetError):
                pass  # the server answered and closed before the body finished
            return sock.recv(4096).split(b" ", 2)[1]

    def test_posts_must_be_json_so_other_sites_cannot_lease(self):
        body = json.dumps({"address": ADDRESS}).encode()
        for content_type in ("text/plain", "application/x-www-form-urlencoded", None):
            headers = {"Content-Type": content_type} if content_type else {}
            request = Request(self.base + "/mining/work", data=body, headers=headers)
            if not content_type:
                request.remove_header("Content-type")
            with self.subTest(content_type=content_type), self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=5)
            self.assertEqual(caught.exception.code, 415)
            caught.exception.close()
        self.assertEqual(self.call("/mining/work", {"address": ADDRESS})[0], 200)

    def test_a_trickled_body_is_cut_off(self):
        body = json.dumps({"address": ADDRESS}).encode()
        head = (b"POST /mining/work HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n")
        with mock.patch.object(gateway_module, "BODY_SECONDS", 0.3):
            self.assertEqual(self.raw(head, body, pause=0.05), b"408")

    def test_large_submissions_are_admitted_a_few_at_a_time(self):
        head = (b"POST /mining/submit HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                b"Content-Length: 200000\r\n\r\n")
        socks = []
        try:
            for _ in range(gateway_module.LARGE_BODIES):
                sock = socket.create_connection(self.server.server_address, timeout=10)
                sock.sendall(head + b"{")
                socks.append(sock)
            time.sleep(0.2)
            self.assertEqual(self.raw(head), b"503")
        finally:
            for sock in socks:
                sock.close()

    def test_errors_do_not_leak(self):
        self.assertEqual(self.call("/mining/work", {"address": "nwrk1qq"})[0], 400)
        self.assertEqual(self.call("/mining/work", [1, 2])[0], 400)
        self.assertEqual(self.call("/rpc", {})[0], 404)
        self.assertEqual(self.call("/mining/blocks/xyz")[0], 400)


if __name__ == "__main__":
    unittest.main()
