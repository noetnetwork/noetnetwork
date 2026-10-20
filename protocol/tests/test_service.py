"""HTTP boundary test: private RPC -> index -> authenticated read-only API."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from test_indexer import FakeNode, GENESIS, block


class ServiceTests(unittest.TestCase):
    def test_authenticated_service_indexes_without_exposing_wallet_rpc(self):
        node = FakeNode([block(0, GENESIS), block(1, "b" * 64, GENESIS)])
        cookie = "__cookie__:" + secrets.token_hex(32)
        authorization = "Basic " + base64.b64encode(cookie.encode()).decode()
        calls = []

        class RPCHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.headers.get("Authorization") != authorization:
                    self.send_error(401)
                    return
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(request["method"])
                result = node(request["method"], *request["params"])
                raw = json.dumps({"result": result, "error": None, "id": "noet"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        rpc_server = ThreadingHTTPServer(("127.0.0.1", 0), RPCHandler)
        rpc_thread = threading.Thread(target=rpc_server.serve_forever, daemon=True)
        rpc_thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="noet-service-") as folder:
                cookie_path = Path(folder) / "cookie"
                cookie_path.write_text(cookie)
                cookie_path.chmod(0o600)
                with socket.socket() as reservation:
                    reservation.bind(("127.0.0.1", 0))
                    port = reservation.getsockname()[1]
                token = secrets.token_hex(32)
                env = {**os.environ, "NOET_RPC_URL": f"http://127.0.0.1:{rpc_server.server_port}",
                       "NOET_RPC_COOKIE": str(cookie_path), "NOET_GENESIS_HASH": GENESIS,
                       "NOET_EXPLORER_PORT": str(port), "NOET_EXPLORER_TOKEN": token,
                       "NOET_INDEX_PATH": str(Path(folder) / "index.sqlite3")}
                process = subprocess.Popen([sys.executable, "-m", "noet.serve"], env=env,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    def read(path, bearer=token):
                        request = Request(f"http://127.0.0.1:{port}{path}", headers={"Authorization": "Bearer " + bearer})
                        with urlopen(request, timeout=2) as response:
                            return json.load(response)
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        self.assertIsNone(process.poll(), "Explorer service exited during startup")
                        try:
                            if read("/status")["height"] == 1:
                                break
                        except OSError:
                            pass
                        time.sleep(.1)
                    self.assertEqual(read("/status")["height"], 1)
                    self.assertEqual(read("/blocks")[0]["hash"], "b" * 64)
                    self.assertEqual(read("/healthz", "wrong"), {"ok": True})
                    for path, bearer, status in [("/status", "wrong", 401), ("/healthz/../status", "wrong", 401), ("/walletpassphrase", token, 404), ("/blocks?before=-1", token, 400), ("/address/nlab1qqqqqqqqqqqqqqqq?cursor=" + "a" * 64 + "%3A" + "b" * 64, token, 409)]:
                        with self.assertRaises(HTTPError) as error:
                            read(path, bearer)
                        self.assertEqual(error.exception.code, status)
                        error.exception.close()
                    self.assertNotIn("walletpassphrase", calls)
                finally:
                    process.terminate()
                    process.wait(timeout=10)
        finally:
            rpc_server.shutdown()
            rpc_server.server_close()
            rpc_thread.join(timeout=5)


class HealthTests(unittest.TestCase):
    def test_chain_health_reports_stalls_and_staleness(self):
        from noet.serve import chain_health
        fresh = {"stale": False, "height": 9, "lastBlockTime": 1000, "lastVerdictTime": 990}
        self.assertEqual(chain_health(fresh, 1100, 3600), {"ok": True, "problems": [], "height": 9,
                         "lastBlockAgeSeconds": 100, "lastVerdictAgeSeconds": 110})
        self.assertEqual(chain_health(fresh, 1000 + 3601, 3600)["problems"], ["no recent block"])
        self.assertEqual(chain_health({**fresh, "stale": True}, 1100, 3600)["problems"], ["index stale"])
        week = 7 * 86400
        self.assertTrue(chain_health(fresh, 1100, 3600, 1100 + week + 1)["ok"])
        soon = chain_health(fresh, 1100, 3600, 1100 + week - 1)
        self.assertEqual((soon["problems"], soon["relayCertificateDaysLeft"]), (["relay certificates expire soon"], 6))
        self.assertEqual(chain_health(fresh, 1100, 3600, 1000)["problems"], ["relay certificates expired"])
        empty = chain_health({"stale": False, "height": 0, "lastBlockTime": None}, 5, 3600)
        self.assertFalse(empty["ok"])
        self.assertIsNone(empty["lastBlockAgeSeconds"])


class BindTests(unittest.TestCase):
    def test_non_loopback_binding_requires_explicit_tls_proxy(self):
        from unittest import mock
        from noet.serve import bind_address
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(bind_address(), "127.0.0.1")
        with mock.patch.dict(os.environ, {"NOET_EXPLORER_BIND": "0.0.0.0"}, clear=True):
            with self.assertRaises(SystemExit):
                bind_address()
        with mock.patch.dict(os.environ, {"NOET_EXPLORER_BIND": "0.0.0.0", "NOET_EXPLORER_TLS_PROXY": "1"}, clear=True):
            self.assertEqual(bind_address(), "0.0.0.0")
        with mock.patch.dict(os.environ, {"NOET_EXPLORER_BIND": "explorer.example"}, clear=True):
            with self.assertRaises(ValueError):
                bind_address()
