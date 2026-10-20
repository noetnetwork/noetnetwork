"""Private explorer service, intended behind an authenticated HTTPS proxy.

No wallet methods, transaction broadcasting or arbitrary node RPC are exposed.
"""
import hmac
import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit, parse_qs, unquote
from .indexer import Index, StaleCursor
from .rpc import NodeRPC

def lookup(index, path, query):
    if any(key not in ("before", "cursor") or len(values) != 1 for key, values in query.items()):
        raise ValueError("Invalid pagination parameters")
    cursor = query.get("cursor", [None])[0]
    transaction_page = path == "/transactions" or re.fullmatch(r"/block/([0-9]{1,10}|[a-f0-9]{64})/transactions", path)
    if cursor is not None and (not re.fullmatch(r"[a-f0-9]{64}:[a-f0-9]{64}", cursor) or not (path.startswith("/address/") or transaction_page) or "before" in query):
        raise ValueError("Invalid transaction cursor")
    before = query.get("before", [None])[0]
    if before is not None:
        if not re.fullmatch(r"[0-9]{1,10}", before) or not (path == "/blocks" or path.startswith("/address/")):
            raise ValueError("Invalid pagination cursor")
        before = int(before)
    if path == "/status":
        return index.status()
    if path == "/statistics":
        return index.statistics()
    if path == "/mempool":
        return index.pending()
    if path == "/holders":
        return index.holders()
    if path == "/producers":
        return index.producers()
    if transaction_page:
        return index.transactions(cursor, path.split("/")[2] if path.startswith("/block/") else None)
    if path == "/blocks":
        return index.blocks(before)
    if re.fullmatch(r"/block/([0-9]{1,10}|[a-f0-9]{64})", path):
        return index.block(path.split("/")[-1])
    if re.fullmatch(r"/proof/[a-f0-9]{64}", path):
        return index.proof(path.split("/")[-1])
    if re.fullmatch(r"/tx/[a-f0-9]{64}", path):
        return index.transaction(path.split("/")[-1])
    if re.fullmatch(r"/wallet/[a-z0-9]{14,100}(,[a-z0-9]{14,100}){0,39}", path):
        return index.wallet(path.split("/")[2].split(","))
    if re.fullmatch(r"/address/[a-zA-Z0-9]{14,100}", path):
        return index.address(path.split("/")[-1], before, cursor)
    return None

CERT_WARN_SECONDS = 7 * 86400


def chain_health(status, now, stall_seconds, cert_expires=None):
    """A chain is healthy when indexed, fresh and producing blocks, with relay
    certificates more than a week from expiry (then proofs stop being verified)."""
    last = status.get("lastBlockTime")
    verdict = status.get("lastVerdictTime")
    block_age = int(now - last) if last else None
    problems = []
    if status.get("stale"):
        problems.append("index stale")
    if block_age is None or block_age > stall_seconds:
        problems.append("no recent block")
    result = {"ok": False, "problems": problems, "height": status.get("height"),
              "lastBlockAgeSeconds": block_age, "lastVerdictAgeSeconds": int(now - verdict) if verdict else None}
    if cert_expires:
        days = int((cert_expires - now) // 86400)
        result["relayCertificateDaysLeft"] = days
        if cert_expires - now < CERT_WARN_SECONDS:
            problems.append("relay certificates expired" if cert_expires <= now else "relay certificates expire soon")
    result["ok"] = not problems
    return result

def bind_address():
    host = os.environ.get("NOET_EXPLORER_BIND", "127.0.0.1")
    address = ipaddress.ip_address(host)
    if not address.is_loopback and os.environ.get("NOET_EXPLORER_TLS_PROXY") != "1":
        raise SystemExit("Non-loopback explorer binding requires NOET_EXPLORER_TLS_PROXY=1")
    return str(address)

def main():
    token = os.environ["NOET_EXPLORER_TOKEN"]
    stall_seconds = int(os.environ.get("NOET_STALL_SECONDS", "3600"))
    cert_expires = int(os.environ.get("NOET_RELAY_CERT_EXPIRES") or 0) or None
    genesis = os.environ["NOET_GENESIS_HASH"]
    path = Path(os.environ.get("NOET_INDEX_PATH", "data/explorer.sqlite3"))
    if len(token) < 32:
        raise SystemExit("NOET_EXPLORER_TOKEN must have at least 32 characters")
    path.parent.mkdir(parents=True, exist_ok=True)
    index = Index(path, genesis)
    index.db.close()
    stopped = threading.Event()

    def sync():
        idx = Index(path, genesis)
        rpc = NodeRPC.from_env()
        while not stopped.is_set():
            try:
                idx.sync(rpc)
            except Exception as error:
                idx.mark_error()
                # Log class only; never disclose credentials or node responses.
                print(json.dumps({"event": "index_sync_failed", "type": type(error).__name__}), flush=True)
            stopped.wait(5)
        idx.db.close()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.connection.settimeout(10)
            if self.path == "/healthz":
                # Liveness only for the platform proxy: no chain data, no auth.
                return self.respond(200, {"ok": True})
            if self.path == "/healthz/chain":
                # For external uptime monitors: public height and ages only.
                idx = Index(path, genesis, readonly=True)
                try:
                    health = chain_health(idx.status(), time.time(), stall_seconds, cert_expires)
                finally:
                    idx.db.close()
                return self.respond(200 if health["ok"] else 503, health)
            if not hmac.compare_digest(self.headers.get("Authorization", "").encode("latin-1"),
                                       ("Bearer " + token).encode("latin-1")):
                return self.respond(401, {"error": "Unauthorized"})
            if len(self.path) > (2048 if self.path.startswith("/wallet/") else 384):
                return self.respond(400, {"error": "Invalid path"})
            idx = Index(path, genesis, readonly=True)
            try:
                url = urlsplit(self.path)
                # One read snapshot across all constituent queries.
                idx.db.execute("BEGIN")
                result = lookup(idx, unquote(url.path), parse_qs(url.query, max_num_fields=2, keep_blank_values=True))
                self.respond(404 if result is None else 200, {"error": "Not found"} if result is None else result)
            except StaleCursor:
                self.respond(409, {"error": "History changed. Restart from the latest transactions."})
            except ValueError:
                self.respond(400, {"error": "Invalid request"})
            except Exception:
                self.respond(503, {"error": "Explorer temporarily unavailable"})
            finally:
                idx.db.close()

        def respond(self, status, body):
            raw = json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    worker = threading.Thread(target=sync, daemon=True)
    worker.start()
    # Loopback by default. A hosted machine whose platform proxy terminates TLS
    # may bind its private interface; bearer authentication still applies.
    server = ThreadingHTTPServer((bind_address(), int(os.environ.get("NOET_EXPLORER_PORT", "8788"))), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set()
        server.server_close()
        worker.join(timeout=35)

if __name__ == "__main__":
    main()
