"""Canonical-chain explorer index. Atomic block application and reorg rollback.

Amounts are stored in integer atoms and serialized as strings, never floats.
This service indexes a node's decisions; it is NOT a consensus implementation.
"""
from decimal import Decimal, InvalidOperation
import json
import re
from pathlib import Path
import sqlite3
import time
from .rpc import verify_network

SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS blocks(
 height INTEGER PRIMARY KEY, hash TEXT UNIQUE NOT NULL, parent TEXT,
 timestamp INTEGER NOT NULL, difficulty TEXT NOT NULL, size INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions(
 txid TEXT PRIMARY KEY, height INTEGER NOT NULL REFERENCES blocks(height) ON DELETE CASCADE,
 position INTEGER NOT NULL, coinbase INTEGER NOT NULL, fee TEXT
);
CREATE TABLE IF NOT EXISTS outputs(
 txid TEXT NOT NULL REFERENCES transactions(txid) ON DELETE CASCADE,
 n INTEGER NOT NULL, address TEXT, atoms INTEGER NOT NULL, script TEXT NOT NULL,
 PRIMARY KEY(txid,n)
);
CREATE INDEX IF NOT EXISTS output_address ON outputs(address);
CREATE TABLE IF NOT EXISTS inputs(
 txid TEXT NOT NULL REFERENCES transactions(txid) ON DELETE CASCADE,
 n INTEGER NOT NULL, prev_txid TEXT NOT NULL, prev_n INTEGER NOT NULL,
 PRIMARY KEY(txid,n), UNIQUE(prev_txid,prev_n)
);
CREATE INDEX IF NOT EXISTS tx_height ON transactions(height);
CREATE TABLE IF NOT EXISTS verdicts(
 block_hash TEXT PRIMARY KEY, quick_status TEXT, full_status TEXT, recorded_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS proofs(
 height INTEGER PRIMARY KEY REFERENCES blocks(height) ON DELETE CASCADE,
 model TEXT, pow_hash TEXT
);
CREATE TABLE IF NOT EXISTS work(
 height INTEGER PRIMARY KEY REFERENCES blocks(height) ON DELETE CASCADE,
 data TEXT NOT NULL
);
"""
WORK_BACKFILL = 20  # older blocks decoded per poll
PROFILES = {
    "fc0ac105e8adb2401780e6fd54ec48650912c5e460d0a4ffe14ba1b83e2df359": ("noet-lab", "nlab",
        "Laboratory chain with mock proof validation. Blocks are integration fixtures, not useful work."),
    "301587d540aafc312cf4606e700d9662e038e7e43ca6cca92779d6b4baa81b4e": ("noet-private-work-v1", "nwrk",
        "Development network. This node accepts a block only after its verifier fully replays the useful-work proof. Test coins have no value."),
    "2da97e8dadb7fe27645263d3e732211cb67650adbc02a016102cc7390785cdaa": ("noet-testnet-1", "tnoet",
        "Public testnet with the proposed NOET economics. Blocks are accepted only after the verifier fully replays the useful-work proof. Test coins have no value."),
    "fc3e73901e2831df8457ea56f56cf92cd23018453439a0c1d402cbab61f45a54": ("noet-mainnet-1", "noet",
        "NOET Mainnet. Blocks are accepted only after the verifier fully replays the useful-work proof."),
}
HEX64 = re.compile(r"[a-f0-9]{64}")

def atoms(value):
    try:
        scaled = Decimal(str(value)) * 100_000_000
        if not scaled.is_finite() or scaled < 0 or scaled != scaled.to_integral_value() or scaled > 9_000_000_000_000_000_000:
            raise ValueError("Invalid coin amount")
        return int(scaled)
    except InvalidOperation:
        raise ValueError("Invalid coin amount") from None

class StaleCursor(ValueError):
    pass


class Index:
    def __init__(self, path, genesis, readonly=False):
        self.db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) if readonly else sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        if readonly:
            self.db.execute("PRAGMA query_only=ON")
        else:
            self.db.executescript(SCHEMA)
        existing = self.db.execute("SELECT value FROM meta WHERE key='genesis'").fetchone()
        if existing and existing[0] != genesis:
            self.db.close()
            raise ValueError("Index belongs to a different network")
        self.genesis = genesis
        if not readonly:
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO meta VALUES('genesis',?)", (genesis,))

    def tip(self):
        row = self.db.execute("SELECT height,hash FROM blocks ORDER BY height DESC LIMIT 1").fetchone()
        return dict(row) if row else {"height": -1, "hash": None}

    def apply(self, block):
        tip = self.tip()
        height = block["height"]
        if height != tip["height"] + 1 or (height and block.get("previousblockhash") != tip["hash"]):
            raise ValueError("Block does not extend the indexed chain")
        if height == 0 and block["hash"] != self.genesis:
            raise ValueError("Wrong genesis")
        # A malformed transaction, unknown prevout or double spend rolls back the ENTIRE block.
        with self.db:
            self.db.execute("INSERT INTO blocks VALUES(?,?,?,?,?,?)", (height, block["hash"], block.get("previousblockhash"), block["time"], str(block.get("difficulty", "0")), block.get("size", 0)))
            model, pow_hash = block.get("tst"), block.get("hashPoW")
            self.db.execute("INSERT INTO proofs VALUES(?,?,?)", (height,
                model if isinstance(model, str) and 0 < len(model) <= 200 else None,
                pow_hash if isinstance(pow_hash, str) and HEX64.fullmatch(pow_hash) else None))
            for position, tx in enumerate(block["tx"]):
                is_coinbase = len(tx["vin"]) == 1 and "coinbase" in tx["vin"][0]
                self.db.execute("INSERT INTO transactions VALUES(?,?,?,?,NULL)", (tx["txid"], height, position, int(is_coinbase)))
                input_value = 0
                if not is_coinbase:
                    for n, vin in enumerate(tx["vin"]):
                        prev = self.db.execute("SELECT atoms FROM outputs WHERE txid=? AND n=?", (vin["txid"], vin["vout"])).fetchone()
                        if not prev:
                            raise ValueError("Missing previous output; refusing an incomplete balance index")
                        input_value += prev[0]
                        self.db.execute("INSERT INTO inputs VALUES(?,?,?,?)", (tx["txid"], n, vin["txid"], vin["vout"]))
                output_value = 0
                for out in tx["vout"]:
                    amount = atoms(out["value"])
                    output_value += amount
                    script = out["scriptPubKey"]
                    self.db.execute("INSERT INTO outputs VALUES(?,?,?,?,?)", (tx["txid"], out["n"], script.get("address"), amount, script.get("hex", "")))
                if not is_coinbase:
                    if output_value > input_value:
                        raise ValueError("Transaction spends more than its inputs")
                    self.db.execute("UPDATE transactions SET fee=? WHERE txid=?", (str(input_value - output_value), tx["txid"]))

    def _record_work(self, rpc, height, block_hash):
        """Decode the prompt and proven output carried in the block's proof. Never blocks indexing."""
        if height == 0:
            return
        try:
            from .blockwork import Detokenizer, block_work
            if not hasattr(self, "_detokenize"):
                self._detokenize = Detokenizer()
            work = block_work(bytes.fromhex(rpc("getblock", block_hash, 0)), self._detokenize)
        except Exception:
            return
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO work VALUES(?,?)", (height, json.dumps(work)))

    def _backfill_work(self, rpc):
        rows = self.db.execute("SELECT b.height,b.hash FROM blocks b LEFT JOIN work w ON w.height=b.height "
                               "WHERE w.height IS NULL AND b.height>0 ORDER BY b.height DESC LIMIT ?", (WORK_BACKFILL,)).fetchall()
        for row in rows:
            self._record_work(rpc, row["height"], row["hash"])

    def work(self, height):
        row = self.db.execute("SELECT data FROM work WHERE height=?", (height,)).fetchone()
        return json.loads(row[0]) if row else None

    def sync(self, rpc, limit=100):
        info = verify_network(rpc, self.genesis)
        height = info["blocks"]
        tip = self.tip()
        # Find a common ancestor BEFORE making changes. RPC failure leaves the index intact.
        common = min(tip["height"], height)
        while common >= 0:
            local = self.db.execute("SELECT hash FROM blocks WHERE height=?", (common,)).fetchone()
            if local and local[0] == rpc("getblockhash", common):
                break
            common -= 1
        if tip["height"] >= 0 and common < 0:
            raise ValueError("No common genesis; refusing chain replacement")
        with self.db:
            self.db.execute("DELETE FROM blocks WHERE height>?", (common,))
        for h in range(common + 1, min(height, common + limit) + 1):
            block_hash = rpc("getblockhash", h)
            block = rpc("getblock", block_hash, 2)
            if block["hash"] != block_hash or block["height"] != h or rpc("getblockhash", h) != block_hash:
                raise ValueError("Chain changed during indexing; retry on next poll")
            self.apply(block)
            self._record_work(rpc, h, block_hash)
        self._backfill_work(rpc)
        final = rpc("getblockchaininfo")
        tip = self.tip()
        if tip["height"] >= 0 and (tip["height"] > final["blocks"] or rpc("getblockhash", tip["height"]) != tip["hash"]):
            raise ValueError("Chain changed during indexing; status will remain stale until resync")
        mempool = rpc("getmempoolinfo")
        network = rpc("getnetworkinfo")
        self._record_pending(rpc)
        self._record_verdicts(rpc)
        self._record_holders()
        snapshot = {"nodeHeight": final["blocks"], "nodeTip": final["bestblockhash"], "initialSync": final["initialblockdownload"],
                    "peers": network["connections"], "mempoolTransactions": mempool["size"], "checkedAt": int(time.time()), "error": False}
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('status',?)", (json.dumps(snapshot),))
        return self.status()

    def _record_pending(self, rpc):
        """Bounded snapshot of unconfirmed transactions, newest first."""
        entries = rpc("getrawmempool", True)
        rows = []
        for txid, entry in entries.items() if isinstance(entries, dict) else ():
            if not HEX64.fullmatch(txid):
                continue
            fee = (entry.get("fees") or {}).get("base", entry.get("fee", 0))
            rows.append({"txid": txid, "vsize": int(entry.get("vsize", 0)), "feeAtoms": str(atoms(fee)),
                         "time": int(entry.get("time", 0))})
        rows.sort(key=lambda r: (-r["time"], r["txid"]))
        # Outputs of the newest unconfirmed transactions, so payment pages can
        # show a payment as seen before it is mined. Bounded and best effort.
        for row in rows[:100]:
            try:
                tx = rpc("getrawtransaction", row["txid"], True)
                row["outputs"] = [{"address": o["scriptPubKey"].get("address"), "atoms": str(atoms(o["value"]))}
                                  for o in tx.get("vout", [])[:20]]
            except Exception:
                row["outputs"] = []
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('pending',?)",
                            (json.dumps({"transactions": rows[:100], "count": len(rows), "truncated": len(rows) > 100}),))

    def _record_verdicts(self, rpc):
        """Keep the node's own Quick/Full results; they age out of its recent list."""
        try:
            rows = rpc("getrecentvalidations", 100)
        except Exception:
            return
        final = ("green", "red", "amber", "pass", "fail")
        with self.db:
            for row in rows if isinstance(rows, list) else ():
                block = row.get("block_hash", "")
                quick, full = str(row.get("quick_status", "")), str(row.get("full_status", ""))
                if not HEX64.fullmatch(block) or not (quick.lower() in final or full.lower() in final):
                    continue
                self.db.execute("""INSERT INTO verdicts VALUES(?,?,?,?) ON CONFLICT(block_hash) DO UPDATE SET
                    quick_status=COALESCE(NULLIF(excluded.quick_status,''),quick_status),
                    full_status=CASE WHEN excluded.full_status IN ('green','red','amber') THEN excluded.full_status ELSE full_status END,
                    recorded_at=excluded.recorded_at""",
                    (block, quick[:24], full[:24] if full.lower() in final else None, int(row.get("timestamp") or time.time())))

    def _record_holders(self):
        """Largest unspent balances, recomputed only when the tip changes."""
        tip = self.tip()
        cached = self.db.execute("SELECT value FROM meta WHERE key='holders'").fetchone()
        if cached and json.loads(cached[0]).get("tip") == tip["hash"]:
            return
        totals = {}
        for address, amount in self.db.execute("SELECT o.address,o.atoms FROM outputs o JOIN transactions t ON t.txid=o.txid WHERE o.address IS NOT NULL AND t.height>0 AND NOT EXISTS(SELECT 1 FROM inputs i WHERE i.prev_txid=o.txid AND i.prev_n=o.n)"):
            totals[address] = totals.get(address, 0) + amount
        supply = sum(totals.values())
        top = sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:25]
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('holders',?)", (json.dumps({"tip": tip["hash"], "height": tip["height"],
                "addresses": len(totals), "unspentAtoms": str(supply),
                "holders": [{"address": a, "balanceAtoms": str(v)} for a, v in top]}),))

    def producers(self):
        """All-time block producers by first reward output, with first and last block."""
        rows = self.db.execute("""SELECT o.address, COUNT(*) AS blocks, MIN(t.height) AS first, MAX(t.height) AS last
            FROM transactions t JOIN outputs o ON o.txid=t.txid
            WHERE t.coinbase=1 AND t.height>0 AND o.atoms>0
              AND o.n=(SELECT MIN(n) FROM outputs WHERE txid=t.txid AND atoms>0)
            GROUP BY o.address ORDER BY blocks DESC, first ASC LIMIT 50""").fetchall()
        total = self.db.execute("SELECT COUNT(*) FROM blocks WHERE height>0").fetchone()[0]
        return {"blocks": total, "producers": [{"address": r["address"] or "non-standard", "blocks": r["blocks"],
                "firstHeight": r["first"], "lastHeight": r["last"]} for r in rows]}

    def pending(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='pending'").fetchone()
        return json.loads(row[0]) if row else {"transactions": [], "count": 0, "truncated": False}

    def holders(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='holders'").fetchone()
        return json.loads(row[0]) if row else {"holders": [], "addresses": 0, "unspentAtoms": "0", "height": self.tip()["height"]}

    def mark_error(self):
        status = self._snapshot()
        status["error"] = True
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('status',?)", (json.dumps(status),))

    def _snapshot(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='status'").fetchone()
        return json.loads(row[0]) if row else {}

    def status(self):
        status = self._snapshot()
        tip = self.tip()
        checked = status.get("checkedAt", 0)
        network, prefix, validation = PROFILES.get(self.genesis, ("unrecognized-development-chain", None, "Unrecognized development chain."))
        latest = self.db.execute("SELECT timestamp FROM blocks WHERE height>0 ORDER BY height DESC LIMIT 1").fetchone()
        verdict = self.db.execute("SELECT MAX(recorded_at) FROM verdicts WHERE full_status IS NOT NULL").fetchone()
        currency = "NOET" if network == "noet-mainnet-1" else "test NOET"
        return {**status, "network": network, "addressPrefix": prefix, "currency": currency, "genesis": self.genesis,
                "height": tip["height"], "tip": tip["hash"], "indexed": tip["height"] >= 0,
                "stale": bool(status.get("error", True)) or time.time() - checked > 45 or (tip["height"] == status.get("nodeHeight") and tip["hash"] != status.get("nodeTip")),
                "lag": max(0, status.get("nodeHeight", 0) - tip["height"]), "validation": validation,
                "lastBlockTime": latest[0] if latest else None, "lastVerdictTime": verdict[0] if verdict else None}

    def statistics(self):
        # Bounded window, labelled as observed samples rather than a target rate.
        # Each sample also carries its Full verdict and when it was recorded, and the
        # address its reward paid first, so charts need no per-block reads.
        rows = self.db.execute("""SELECT b.*, (SELECT COUNT(*) FROM transactions t WHERE t.height=b.height) AS transactions,
                v.full_status AS verdict, v.recorded_at AS verdictAt,
                (SELECT o.address FROM transactions t JOIN outputs o ON o.txid=t.txid
                 WHERE t.height=b.height AND t.coinbase=1 AND o.atoms>0 ORDER BY o.n LIMIT 1) AS producer
                FROM blocks b LEFT JOIN verdicts v ON v.block_hash=b.hash
                WHERE b.height>0 ORDER BY height DESC LIMIT 144""").fetchall()
        intervals = [max(0, rows[i]["timestamp"] - rows[i + 1]["timestamp"]) for i in range(len(rows) - 1)]
        fees = self.db.execute("SELECT fee FROM transactions WHERE height>=? AND coinbase=0", (rows[-1]["height"] if rows else 1,)).fetchall()
        producers = {}
        if rows:
            for address, count in self.db.execute("""SELECT o.address, COUNT(DISTINCT t.height) FROM transactions t
                    JOIN outputs o ON o.txid=t.txid WHERE t.coinbase=1 AND t.height>=? AND o.atoms>0
                    AND o.n=(SELECT MIN(n) FROM outputs WHERE txid=t.txid AND atoms>0) GROUP BY o.address""",
                    (rows[-1]["height"],)):
                producers[address or "non-standard"] = count
        return {"tip": self.tip(), "sampleBlocks": len(rows), "window": 144,
                "producers": [{"address": a, "blocks": n} for a, n in sorted(producers.items(), key=lambda i: (-i[1], i[0]))[:10]],
                "averageBlockSeconds": sum(intervals) / len(intervals) if intervals else None,
                "averageBlockBytes": sum(r["size"] for r in rows) // len(rows) if rows else None,
                "transactions": sum(r["transactions"] for r in rows),
                "feesAtoms": str(sum(int(r[0]) for r in fees if r[0] is not None)),
                "series": [dict(r) for r in reversed(rows)]}

    def transactions(self, cursor=None, block_key=None):
        boundary = (self.tip()["height"] + 1, 0)
        height = None
        if block_key is not None:
            block = self.db.execute("SELECT height FROM blocks WHERE " + ("height=?" if block_key.isdigit() and len(block_key) < 12 else "hash=?"), (block_key,)).fetchone()
            if not block:
                return None
            height = block[0]
        if cursor:
            if not re.fullmatch(r"[a-f0-9]{64}:[a-f0-9]{64}", cursor):
                raise ValueError("Invalid transaction cursor")
            block_hash, txid = cursor.split(":")
            anchor = self.db.execute("SELECT t.height,t.position FROM transactions t JOIN blocks b ON b.height=t.height WHERE t.txid=? AND b.hash=?", (txid, block_hash)).fetchone()
            if not anchor or (height is not None and anchor["height"] != height):
                raise StaleCursor("History changed; restart from the latest transactions")
            boundary = (anchor["height"], anchor["position"])
        rows = self.db.execute("SELECT t.*,b.hash AS blockHash,b.timestamp FROM transactions t JOIN blocks b ON b.height=t.height WHERE (t.height<? OR (t.height=? AND t.position<?)) AND (? IS NULL OR t.height=?) ORDER BY t.height DESC,t.position DESC LIMIT 51", (boundary[0], boundary[0], boundary[1], height, height)).fetchall()
        return {"transactions": [dict(r) for r in rows[:50]], "nextCursor": rows[49]["blockHash"] + ":" + rows[49]["txid"] if len(rows) > 50 else None, "tip": self.tip()}

    def blocks(self, before=None):
        before = before if before is not None else self.tip()["height"] + 1
        rows = self.db.execute("SELECT b.*, p.model, (SELECT count(*) FROM transactions t WHERE t.height=b.height) AS transactions FROM blocks b LEFT JOIN proofs p ON p.height=b.height WHERE b.height<? ORDER BY b.height DESC LIMIT 25", (before,)).fetchall()
        return [dict(r) for r in rows]

    def block(self, key):
        row = self.db.execute("SELECT * FROM blocks WHERE " + ("height=?" if key.isdigit() and len(key) < 12 else "hash=?"), (key,)).fetchone()
        if not row:
            return None
        txs = self.db.execute("SELECT txid,coinbase,fee FROM transactions WHERE height=? ORDER BY position LIMIT 100", (row["height"],)).fetchall()
        count = self.db.execute("SELECT count(*) FROM transactions WHERE height=?", (row["height"],)).fetchone()[0]
        proof = self.db.execute("SELECT model,pow_hash AS powHash FROM proofs WHERE height=?", (row["height"],)).fetchone()
        verdict = self.db.execute("SELECT quick_status AS quick,full_status AS full,recorded_at AS recordedAt FROM verdicts WHERE block_hash=?", (row["hash"],)).fetchone()
        reward = self.db.execute("SELECT o.address,CAST(o.atoms AS TEXT) AS atoms FROM outputs o JOIN transactions t ON t.txid=o.txid WHERE t.height=? AND t.coinbase=1 AND o.atoms>0 ORDER BY o.n LIMIT 4", (row["height"],)).fetchall()
        return {**dict(row), "proof": dict(proof) if proof else None, "verdict": dict(verdict) if verdict else None, "rewardOutputs": [dict(r) for r in reward], "work": self.work(row["height"]), "confirmations": self.tip()["height"] - row["height"] + 1, "transactions": [dict(t) for t in txs], "transactionCount": count, "transactionsTruncated": count > 100}

    def proof(self, pow_hash):
        row = self.db.execute("SELECT b.height,b.hash FROM proofs p JOIN blocks b ON b.height=p.height WHERE p.pow_hash=?", (pow_hash,)).fetchone()
        return {"height": row["height"], "hash": row["hash"], "powHash": pow_hash} if row else None

    def transaction(self, txid):
        tx = self.db.execute("SELECT * FROM transactions WHERE txid=?", (txid,)).fetchone()
        if not tx:
            return None
        outputs = self.db.execute("SELECT o.n,o.address,CAST(o.atoms AS TEXT) AS atoms,o.script,i.txid AS spentBy FROM outputs o LEFT JOIN inputs i ON i.prev_txid=o.txid AND i.prev_n=o.n WHERE o.txid=? ORDER BY o.n LIMIT 100", (txid,)).fetchall()
        inputs = self.db.execute("SELECT i.n,i.prev_txid,i.prev_n,o.address,CAST(o.atoms AS TEXT) AS atoms FROM inputs i JOIN outputs o ON i.prev_txid=o.txid AND i.prev_n=o.n WHERE i.txid=? ORDER BY i.n LIMIT 100", (txid,)).fetchall()
        counts = self.db.execute("SELECT (SELECT count(*) FROM inputs WHERE txid=?), (SELECT count(*) FROM outputs WHERE txid=?)", (txid, txid)).fetchone()
        block = self.db.execute("SELECT hash,timestamp FROM blocks WHERE height=?", (tx["height"],)).fetchone()
        return {**dict(tx), "blockHash": block["hash"], "timestamp": block["timestamp"], "confirmations": self.tip()["height"] - tx["height"] + 1, "outputs": [dict(o) for o in outputs], "inputs": [dict(i) for i in inputs], "inputCount": counts[0], "outputCount": counts[1], "inputsTruncated": counts[0] > 100, "outputsTruncated": counts[1] > 100}

    def wallet(self, addresses):
        """Balances, spendable coins and merged history for one wallet's addresses."""
        if not 1 <= len(addresses) <= 40 or len(set(addresses)) != len(addresses):
            raise ValueError("Request between 1 and 40 distinct addresses")
        tip = self.tip()["height"]
        marks = ",".join("?" * len(addresses))
        rows = self.db.execute(f"SELECT o.address,o.txid,o.n,o.atoms,t.coinbase,t.height FROM outputs o JOIN transactions t ON t.txid=o.txid WHERE o.address IN ({marks}) AND t.height>0 AND NOT EXISTS(SELECT 1 FROM inputs i WHERE i.prev_txid=o.txid AND i.prev_n=o.n) ORDER BY t.height,t.position,o.n", addresses).fetchall()
        used = {r[0] for r in self.db.execute(f"SELECT DISTINCT address FROM outputs WHERE address IN ({marks})", addresses)}
        coins = [{"address": r["address"], "txid": r["txid"], "vout": r["n"], "atoms": str(r["atoms"]),
                  "confirmations": tip - r["height"] + 1, "mature": not r["coinbase"] or tip - r["height"] >= 100}
                 for r in rows[:500]]
        balance = sum(r["atoms"] for r in rows)
        immature = sum(r["atoms"] for r in rows if r["coinbase"] and tip - r["height"] < 100)
        history = self.db.execute(f"""SELECT t.txid,t.height,t.coinbase,t.fee,b.timestamp,
            (SELECT COALESCE(SUM(o.atoms),0) FROM outputs o WHERE o.txid=t.txid AND o.address IN ({marks})) AS received,
            (SELECT COALESCE(SUM(p.atoms),0) FROM inputs i JOIN outputs p ON p.txid=i.prev_txid AND p.n=i.prev_n WHERE i.txid=t.txid AND p.address IN ({marks})) AS sent
            FROM transactions t JOIN blocks b ON b.height=t.height
            WHERE t.txid IN (SELECT txid FROM outputs WHERE address IN ({marks})
                             UNION SELECT i.txid FROM inputs i JOIN outputs p ON p.txid=i.prev_txid AND p.n=i.prev_n WHERE p.address IN ({marks}))
            ORDER BY t.height DESC,t.position DESC LIMIT 50""", addresses * 4).fetchall()
        return {"tip": self.tip(), "balanceAtoms": str(balance), "immatureAtoms": str(immature),
                "matureAtoms": str(balance - immature), "usedAddresses": sorted(used), "coins": coins,
                "coinsTruncated": len(rows) > 500,
                "history": [{"txid": h["txid"], "height": h["height"], "timestamp": h["timestamp"], "coinbase": bool(h["coinbase"]),
                             "fee": h["fee"], "netAtoms": str(h["received"] - h["sent"])} for h in history]}

    def address(self, address, before=None, cursor=None):
        tip = self.tip()["height"]
        # Stream aggregates as Python integers. Lifetime receipts can exceed a
        # signed SQL integer even when every individual output is valid.
        received = sum(row[0] for row in self.db.execute("SELECT atoms FROM outputs WHERE address=?", (address,)))
        unspent = []
        balance = immature = unspent_count = 0
        for row in self.db.execute("SELECT o.txid,o.n,o.atoms,t.coinbase,t.height FROM outputs o JOIN transactions t ON t.txid=o.txid WHERE o.address=? AND t.height>0 AND NOT EXISTS(SELECT 1 FROM inputs i WHERE i.prev_txid=o.txid AND i.prev_n=o.n) ORDER BY t.height DESC,t.position DESC,o.n", (address,)):
            balance += row["atoms"]
            unspent_count += 1
            # Match the native wallet's COINBASE_MATURITY + 1 depth rule:
            # 100 blocks AFTER the reward, i.e. 101 confirmations including it.
            if row["coinbase"] and tip-row["height"] < 100:
                immature += row["atoms"]
            if len(unspent) < 100:
                unspent.append(row)
        # A transaction cursor includes its position implicitly, so a page may
        # end inside a block without dropping the remaining transactions.
        boundary = (before if before is not None else tip + 1, 0)
        if cursor is not None:
            if before is not None:
                raise ValueError("Choose one pagination cursor")
            if not re.fullmatch(r"[a-f0-9]{64}:[a-f0-9]{64}", cursor):
                raise ValueError("Invalid transaction cursor")
            block_hash, txid = cursor.split(":")
            anchor = self.db.execute("SELECT t.height,t.position FROM transactions t JOIN blocks b ON b.height=t.height WHERE t.txid=? AND b.hash=?", (txid, block_hash)).fetchone()
            if anchor is None:
                raise StaleCursor("History changed; restart from the latest transactions")
            boundary = (anchor["height"], anchor["position"])
        rows = self.db.execute("SELECT t.txid,t.height,t.coinbase,t.fee,b.hash AS blockHash,b.timestamp FROM transactions t JOIN blocks b ON b.height=t.height WHERE (t.height<? OR (t.height=? AND t.position<?)) AND (EXISTS(SELECT 1 FROM outputs o WHERE o.txid=t.txid AND o.address=?) OR EXISTS(SELECT 1 FROM inputs i JOIN outputs o ON o.txid=i.prev_txid AND o.n=i.prev_n WHERE i.txid=t.txid AND o.address=?)) ORDER BY t.height DESC,t.position DESC LIMIT 101", (boundary[0], boundary[0], boundary[1], address, address)).fetchall()
        return {"address": address, "receivedAtoms": str(received), "balanceAtoms": str(balance), "immatureAtoms": str(immature),
                "matureAtoms": str(balance - immature), "mempoolIncluded": False,
                "utxos": [{**dict(r), "atoms": str(r["atoms"]), "confirmations": tip-r["height"]+1, "mature": not r["coinbase"] or tip-r["height"] >= 100} for r in unspent[:100]],
                "utxoCount": unspent_count, "utxosTruncated": unspent_count > 100,
                "transactions": [dict(r) for r in rows[:100]], "transactionsTruncated": len(rows) > 100, "nextCursor": self.db.execute("SELECT hash FROM blocks WHERE height=?", (rows[99]["height"],)).fetchone()[0] + ":" + rows[99]["txid"] if len(rows) > 100 else None}
