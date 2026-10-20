"""Development-network faucet. Sends small amounts of no-value test NOET.

Separate from the read-only explorer: this process alone holds the faucet
wallet passphrase. Only the website's server calls it, with its own bearer
token and an opaque account hash from a verified sign-in. Limits are durable
on the node: one payment per account and per address each day, plus a global
daily cap. The wallet is unlocked for a few seconds per payment and relocked.
"""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from .indexer import atoms
from .rpc import NodeRPC, RPCError, RPCRejected
from .serve import bind_address

STATUS_SECONDS = 5
DAY = 86400
ACCOUNT = re.compile(r"[a-f0-9]{64}")
SCHEMA = """
CREATE TABLE IF NOT EXISTS payments(
 id INTEGER PRIMARY KEY, account TEXT NOT NULL, address TEXT NOT NULL,
 txid TEXT, atoms INTEGER NOT NULL, created INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS payments_created ON payments(created);
"""


class Refused(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Broadcaster:
    """Relays wallet-signed transactions after the node's own mempool check.
    It never signs, holds keys or reads wallets."""

    def __init__(self, rpc, hourly_limit=600, clock=time.time):
        self.rpc, self.hourly_limit, self.clock = rpc, hourly_limit, clock
        self.recent = []
        self.lock = threading.Lock()

    def broadcast(self, raw):
        if not isinstance(raw, str) or not re.fullmatch(r"(?:[0-9a-f]{2}){60,200000}", raw):
            raise Refused(400, "Invalid signed transaction")
        with self.lock:
            now = self.clock()
            self.recent = [t for t in self.recent if t > now - 3600]
            if len(self.recent) >= self.hourly_limit:
                raise Refused(429, "The network is busy. Try again shortly.")
            self.recent.append(now)
        try:
            verdict = self.rpc("testmempoolaccept", [raw])[0]
        except RPCError:
            # The node refuses transactions it cannot decode before any policy check.
            raise Refused(400, "The node could not decode this transaction") from None
        if not verdict.get("allowed"):
            reason = str(verdict.get("reject-reason") or "rejected")
            raise Refused(400, "The node rejected this transaction: " + reason[:120])
        txid = self.rpc("sendrawtransaction", raw)
        if txid != verdict.get("txid"):
            raise RPCError("Unexpected broadcast response")
        return {"txid": txid}


class Faucet:
    def __init__(self, rpc, wallet_rpc, passphrase, db_path, amount="1", daily_limit=100, clock=time.time,
                 hrp="nwrk"):
        self.hrp = hrp
        self.address = re.compile(re.escape(hrp) + r"1[023456789acdefghjklmnpqrstuvwxyz]{8,85}")
        if len(passphrase) < 16:
            raise ValueError("Faucet wallet passphrase is too short")
        self.rpc, self.wallet, self.passphrase = rpc, wallet_rpc, passphrase
        self.amount = atoms(amount)
        if not 0 < self.amount <= 10 * 100_000_000:
            raise ValueError("Faucet amount must be positive and at most 10 test NOET")
        self.daily_limit, self.clock = daily_limit, clock
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()
        self.cached_status = (0.0, None)

    def ensure_wallet(self, name="faucet"):
        """Create or load the encrypted faucet wallet; return its reward address."""
        loaded = self.rpc("listwallets")
        if name not in loaded:
            if name in [w["name"] for w in self.rpc("listwalletdir")["wallets"]]:
                self.rpc("loadwallet", name)
            else:
                self.rpc("createwallet", name, False, False, self.passphrase, False, True)
        labels = self.wallet("getaddressesbylabel", "mining") if "mining" in self.wallet("listlabels") else {}
        if labels:
            return sorted(labels)[0]
        return self.wallet("getnewaddress", "mining", "bech32")

    def status(self):
        """Cached for a few seconds: the public status page must not drive node load."""
        now = self.clock()
        stamp, cached = self.cached_status
        if cached is not None and now - stamp < STATUS_SECONDS:
            return cached
        balances = self.wallet("getbalances")["mine"]
        with self.lock:
            used = self.db.execute("SELECT count(*) FROM payments WHERE created>?", (now - DAY,)).fetchone()[0]
        result = {"amountAtoms": str(self.amount), "availableAtoms": str(atoms(balances["trusted"])),
                "immatureAtoms": str(atoms(balances["immature"])), "dailyLimit": self.daily_limit,
                "remainingToday": max(0, self.daily_limit - used), "currency": "test NOET"}
        self.cached_status = (now, result)
        return result

    def drip(self, account, address):
        if not ACCOUNT.fullmatch(account or ""):
            raise Refused(400, "Invalid account")
        if not self.address.fullmatch(address or ""):
            raise Refused(400, f"Enter a NOET address starting with {self.hrp}1")
        with self.lock:
            now = self.clock()
            # Pending rows (txid NULL) count too: their payment may have gone out.
            recent = self.db.execute(
                "SELECT account,address FROM payments WHERE created>?", (now - DAY,)).fetchall()
            if len(recent) >= self.daily_limit:
                raise Refused(429, "Today's faucet allowance is used up. Try again tomorrow.")
            if any(row[0] == account for row in recent):
                raise Refused(429, "Your account already received test NOET today.")
            if any(row[1] == address for row in recent):
                raise Refused(429, "This address already received test NOET today.")
            info = self.rpc("validateaddress", address)
            if not info.get("isvalid"):
                raise Refused(400, "This address is not valid on this network")
            if self.wallet("getaddressinfo", address).get("ismine"):
                raise Refused(400, "Use your own wallet address")
            if atoms(self.wallet("getbalances")["mine"]["trusted"]) < self.amount + 100_000:
                raise Refused(503, "The faucet is waiting for mined rewards to mature. Try again later.")
            amount = f"{self.amount // 100_000_000}.{self.amount % 100_000_000:08d}"
            # Reserve the payment first. If the node times out after sending, the
            # reservation stays and a retry cannot pay the same person twice.
            with self.db:
                row = self.db.execute("INSERT INTO payments(account,address,txid,atoms,created) VALUES(?,?,NULL,?,?)",
                                      (account, address, self.amount, int(now))).lastrowid
            self.cached_status = (0.0, None)
            try:
                self.wallet("walletpassphrase", self.passphrase, 10)
            except Exception:
                self._release(row)
                raise
            try:
                txid = self.wallet("sendtoaddress", address, amount, "", "", False, True)
            except RPCRejected:
                self._release(row)
                raise
            finally:
                self.wallet("walletlock")
            if not re.fullmatch(r"[a-f0-9]{64}", txid or ""):
                raise RPCError("Unexpected payment response")
            with self.db:
                self.db.execute("UPDATE payments SET txid=? WHERE rowid=?", (txid, row))
            return {"txid": txid, "amountAtoms": str(self.amount), "address": address}

    def _release(self, row):
        with self.db:
            self.db.execute("DELETE FROM payments WHERE rowid=? AND txid IS NULL", (row,))


def main():
    token = os.environ["NOET_FAUCET_TOKEN"]
    if len(token) < 32:
        raise SystemExit("NOET_FAUCET_TOKEN must have at least 32 characters")
    url, cookie = os.environ["NOET_RPC_URL"], os.environ["NOET_RPC_COOKIE"]
    faucet = Faucet(NodeRPC(url, cookie), NodeRPC(url.rstrip("/") + "/wallet/faucet", cookie),
                    os.environ["NOET_FAUCET_PASSPHRASE"], os.environ.get("NOET_FAUCET_DB", "data/faucet.sqlite3"),
                    os.environ.get("NOET_FAUCET_AMOUNT", "1"), int(os.environ.get("NOET_FAUCET_DAILY_LIMIT", "100")),
                    hrp=os.environ.get("NOET_ADDRESS_HRP", "nwrk"))
    broadcaster = Broadcaster(NodeRPC(url, cookie))
    while True:
        try:
            address = faucet.ensure_wallet()
            break
        except RPCError:
            time.sleep(5)
    Path(os.environ.get("NOET_FAUCET_ADDRESS_FILE", "data/faucet-address")).write_text(address + "\n")
    print(json.dumps({"event": "faucet-ready", "rewardAddress": address}), flush=True)

    class Handler(BaseHTTPRequestHandler):
        def authorized(self):
            return hmac.compare_digest(self.headers.get("Authorization", "").encode("latin-1"),
                                       ("Bearer " + token).encode("latin-1"))

        def do_GET(self):
            self.connection.settimeout(10)
            if self.path == "/healthz":
                return self.respond(200, {"ok": True})
            if not self.authorized():
                return self.respond(401, {"error": "Unauthorized"})
            if self.path != "/faucet/status":
                return self.respond(404, {"error": "Not found"})
            try:
                self.respond(200, faucet.status())
            except Exception:
                self.respond(503, {"error": "Faucet temporarily unavailable"})

        def do_POST(self):
            self.connection.settimeout(10)
            if not self.authorized():
                return self.respond(401, {"error": "Unauthorized"})
            if self.path not in ("/faucet/drip", "/tx/broadcast"):
                return self.respond(404, {"error": "Not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= (410_000 if self.path == "/tx/broadcast" else 1024):
                    raise Refused(400, "Invalid request")
                body = json.loads(self.rfile.read(length))
                if self.path == "/tx/broadcast":
                    return self.respond(200, broadcaster.broadcast(body.get("hex")))
                self.respond(200, faucet.drip(body.get("account"), body.get("address")))
            except Refused as error:
                self.respond(error.status, {"error": str(error)})
            except (ValueError, AttributeError):
                self.respond(400, {"error": "Invalid request"})
            except Exception:
                self.respond(503, {"error": "Faucet temporarily unavailable"})

        def respond(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer((bind_address(), int(os.environ.get("NOET_FAUCET_PORT", "8790"))), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
