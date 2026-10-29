import sqlite3
import unittest
from noet.indexer import Index, atoms, StaleCursor
from noet.rpc import NodeRPC, RPCError, verify_network
from noet.serve import lookup
from noet.wallet import require_lab_address

GENESIS = "a" * 64
ADDRESS = "nlab1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"

def coinbase(txid, amount="50.00000000", address=ADDRESS):
    return {"txid": txid, "vin": [{"coinbase": "00"}], "vout": [{"n": 0, "value": amount, "scriptPubKey": {"address": address, "hex": "001400"}}]}

def block(height, name, parent=None, txs=None):
    return {"height": height, "hash": name, "previousblockhash": parent, "time": 1000 + height, "difficulty": "0.5", "size": 500, "tx": txs or [coinbase("reward-" + name)]}

def spend(txid, parent, amount="49.90000000", address=ADDRESS):
    return {"txid": txid, "vin": [{"txid": parent, "vout": 0}], "vout": [{"n": 0, "value": amount, "scriptPubKey": {"address": address, "hex": "001400"}}]}

class FakeNode:
    def __init__(self, blocks, mempool=None):
        self.blocks = blocks
        self.mempool = mempool or {}
    def __call__(self, method, *args):
        if method == "getblockchaininfo":
            return {"chain": "tensor-reg", "blocks": len(self.blocks) - 1, "bestblockhash": self.blocks[-1]["hash"], "initialblockdownload": False}
        if method == "getblockhash": return self.blocks[args[0]]["hash"]
        if method == "getblock": return next(b for b in self.blocks if b["hash"] == args[0])
        if method == "getmempoolinfo": return {"size": 3}
        if method == "getnetworkinfo": return {"connections": 2}
        if method == "getrawmempool": return self.mempool
        if method == "getrecentvalidations": return getattr(self, "validations", [])
        if method == "getrawtransaction":
            if args[0] not in getattr(self, "raw", {}): raise RuntimeError("unknown")
            return self.raw[args[0]]
        raise AssertionError(method)

class IndexTests(unittest.TestCase):
    def setUp(self):
        self.index = Index(":memory:", GENESIS)
        self.genesis = block(0, GENESIS)
    def tearDown(self): self.index.db.close()
    def test_exact_amounts(self):
        self.assertEqual(atoms("0.00000001"), 1)
        self.assertEqual(atoms("21184153.03530240"), 2118415303530240)
        for value in ("-1", "0.000000001", "NaN", "Infinity", "1000000000000"):
            with self.assertRaises(ValueError): atoms(value)
    def test_lifetime_totals_remain_exact_beyond_sql_integer_range(self):
        self.index.apply(block(0,GENESIS,txs=[coinbase('genesis',address='other')]))
        for height in (1,2):
            self.index.apply(block(height,str(height),GENESIS if height==1 else '1',[coinbase('large'+str(height),amount='90000000000')]))
        value=self.index.address(ADDRESS)
        self.assertEqual(value['receivedAtoms'],'18000000000000000000')
        self.assertEqual(value['balanceAtoms'],'18000000000000000000')
        self.assertEqual(value['utxoCount'],2)

    def test_mainnet_reports_real_noet(self):
        self.assertEqual(self.index.status()["currency"], "test NOET")
        mainnet = Index(":memory:", "fc3e73901e2831df8457ea56f56cf92cd23018453439a0c1d402cbab61f45a54")
        try:
            self.assertEqual(mainnet.status()["currency"], "NOET")
        finally:
            mainnet.db.close()

    def test_genesis_not_spendable_balance(self):
        self.index.apply(self.genesis)
        self.assertEqual(self.index.address(ADDRESS)["balanceAtoms"], "0")
    def test_block_applied_atomically(self):
        self.index.apply(self.genesis)
        bad = block(1, "b", GENESIS, [coinbase("new"), spend("broken", "unknown")])
        with self.assertRaises(ValueError): self.index.apply(bad)
        self.assertEqual(self.index.tip()["height"], 0)
        self.assertIsNone(self.index.transaction("new"))
    def test_double_spend_rolls_back(self):
        self.index.apply(self.genesis)
        self.index.apply(block(1, "b", GENESIS, [coinbase("reward")]))
        with self.assertRaises(sqlite3.IntegrityError):
            self.index.apply(block(2, "c", "b", [coinbase("reward2"), spend("spend1", "reward"), spend("spend2", "reward")]))
        self.assertEqual(self.index.tip()["height"], 1)
        self.assertIsNone(self.index.transaction("spend1"))
    def test_wrong_parent(self):
        self.index.apply(self.genesis)
        with self.assertRaises(ValueError): self.index.apply(block(1, "b", "wrong"))
    def test_reorg_restores_spent_outputs(self):
        b = block(1, "b", GENESIS, [coinbase("reward")])
        c = block(2, "c", "b", [coinbase("reward2", address="other"), spend("spent", "reward", address="other")])
        node = FakeNode([self.genesis, b, c])
        self.index.sync(node)
        self.assertEqual(self.index.address(ADDRESS)["balanceAtoms"], "0")
        node.blocks = [self.genesis, b, block(2, "d", "b", [coinbase("newfork", address="other")])]
        self.index.sync(node)
        self.assertIsNone(self.index.transaction("spent"))
        self.assertEqual(self.index.address(ADDRESS)["balanceAtoms"], "5000000000")
        self.assertEqual(self.index.tip()["hash"], "d")
    def test_shorter_chain_rolls_back(self):
        node = FakeNode([self.genesis, block(1, "b", GENESIS)])
        self.index.sync(node)
        node.blocks = [self.genesis]
        self.index.sync(node)
        self.assertEqual(self.index.tip()["height"], 0)
    def test_failed_rpc_during_ancestor_search_preserves_index(self):
        node = FakeNode([self.genesis, block(1, "b", GENESIS)])
        self.index.sync(node)
        def failing(method, *args):
            if method == "getblockhash" and args[0] == 1: raise RPCError("down")
            return node(method, *args)
        with self.assertRaises(RPCError): self.index.sync(failing)
        self.assertEqual(self.index.tip()["hash"], "b")
    def test_wrong_network_rejected(self):
        with self.assertRaises(RPCError): verify_network(FakeNode([block(0, "wrong")]), GENESIS)
        self.assertEqual(self.index.tip()["height"], -1)
    def test_partial_sync_reports_lag(self):
        node = FakeNode([self.genesis, block(1, "b", GENESIS), block(2, "c", "b")])
        status = self.index.sync(node, limit=1)
        self.assertEqual(status["lag"], 2)
        self.assertEqual(status["height"], 0)
        self.assertFalse(status["stale"])
    def test_failure_marks_stale(self):
        self.index.sync(FakeNode([self.genesis]))
        self.index.mark_error()
        self.assertTrue(self.index.status()["stale"])
    def test_coinbase_maturity(self):
        self.index.apply(self.genesis)
        previous = GENESIS
        for h in range(1, 102):
            self.index.apply(block(h, str(h), previous, [coinbase("reward" + str(h), address=ADDRESS if h == 1 else "other")]))
            previous = str(h)
            if h in (99, 100):
                self.assertEqual(self.index.address(ADDRESS)["immatureAtoms"], "5000000000")
                self.assertFalse(self.index.address(ADDRESS)["utxos"][0]["mature"])
        self.assertEqual(self.index.address(ADDRESS)["immatureAtoms"], "0")
        self.assertEqual(self.index.address(ADDRESS)["matureAtoms"], "5000000000")
        self.assertTrue(self.index.address(ADDRESS)["utxos"][0]["mature"])
        self.assertEqual(self.index.address(ADDRESS)["utxos"][0]["confirmations"], 101)
    def test_fee_exact_and_address_send_history(self):
        self.index.apply(self.genesis)
        self.index.apply(block(1, "b", GENESIS, [coinbase("reward")]))
        self.index.apply(block(2, "c", "b", [coinbase("r2", address="other"), spend("spent", "reward", address="other")]))
        self.assertEqual(self.index.transaction("spent")["fee"], "10000000")
        self.assertEqual(self.index.address(ADDRESS)["transactions"][0]["txid"], "spent")
    def test_address_pages_do_not_skip_transactions_inside_a_block(self):
        self.index.apply(self.genesis)
        txs = [coinbase(f"{1:064x}")]
        for i in range(2, 206):
            txs.append(spend(f"{i:064x}", f"{i-1:064x}", amount="50"))
        self.index.apply(block(1, "b" * 64, GENESIS, txs))
        first = self.index.address(ADDRESS)
        second = lookup(self.index, "/address/" + ADDRESS, {"cursor": [first["nextCursor"]]})
        third = self.index.address(ADDRESS, cursor=second["nextCursor"])
        ids = [t["txid"] for page in (first, second, third) for t in page["transactions"]]
        self.assertEqual(ids, [f"{i:064x}" for i in range(205, 0, -1)] + ["reward-" + GENESIS])
        self.assertIsNone(third["nextCursor"])
        with self.assertRaises(StaleCursor):
            self.index.address(ADDRESS, cursor="f" * 64 + ":" + first["nextCursor"].split(":")[1])
        self.assertEqual(first["balanceAtoms"], second["balanceAtoms"])
        # Extending the chain does not invalidate a transaction-position cursor.
        self.index.apply(block(2, "c" * 64, "b" * 64, [coinbase("new")]))
        self.assertEqual(self.index.address(ADDRESS, cursor=first["nextCursor"])["transactions"], second["transactions"])
        self.index.sync(FakeNode([self.genesis]))
        with self.assertRaises(StaleCursor):
            self.index.address(ADDRESS, cursor=first["nextCursor"])

    def test_reject_ambiguous_or_malformed_history_cursors(self):
        cursor = "a" * 64 + ":" + "b" * 64
        for query in ({"cursor": ["bad"]}, {"cursor": [cursor], "before": ["1"]}, {"cursor": [cursor, cursor]}, {"before": [""]}, {"other": ["1"]}):
            with self.subTest(query=query), self.assertRaises(ValueError):
                lookup(self.index, "/address/" + ADDRESS, query)
        with self.assertRaises(ValueError):
            lookup(self.index, "/blocks", {"cursor": [cursor]})

    def test_bounded_statistics_and_profile_identity(self):
        self.index.apply(self.genesis)
        self.assertIsNone(self.index.statistics()['averageBlockSeconds'])
        previous = GENESIS
        for height in range(1, 161):
            name = f'{height:064x}'
            self.index.apply(block(height, name, previous))
            previous = name
        stats = lookup(self.index, '/statistics', {})
        self.assertEqual(stats['sampleBlocks'], 144)
        self.assertEqual(stats['averageBlockSeconds'], 1)
        self.assertEqual(stats['transactions'], 144)
        self.assertEqual(stats['feesAtoms'], '0')
        self.assertEqual(stats['series'][0]['height'], 17)
        self.assertEqual(stats['series'][0]['producer'], ADDRESS)
        self.assertIsNone(stats['series'][0]['verdict'])
        self.assertIn('verdictAt', stats['series'][0])
        self.assertEqual(stats['producers'], [{'address': ADDRESS, 'blocks': 144}])
        board = lookup(self.index, '/producers', {})
        self.assertEqual(board['blocks'], 160)
        self.assertEqual(board['producers'], [{'address': ADDRESS, 'blocks': 160, 'firstHeight': 1, 'lastHeight': 160}])
        self.assertEqual(self.index.status()['network'], 'unrecognized-development-chain')
        self.assertIsNone(self.index.status()['addressPrefix'])
        self.index.genesis = '301587d540aafc312cf4606e700d9662e038e7e43ca6cca92779d6b4baa81b4e'
        self.assertEqual(self.index.status()['addressPrefix'], 'nwrk')

    def test_block_proof_reward_pending_and_holders(self):
        pow_hash = "c" * 64
        first = dict(block(1, "b" * 64, GENESIS), tst="Qwen/Qwen3-0.6B@c1899de", hashPoW=pow_hash)
        second = dict(block(2, "d" * 64, "b" * 64, [coinbase("reward-2", "25.00000000", "nlab1other")]), tst="x" * 300, hashPoW="not-hex")
        pending = {"e" * 64: {"vsize": 141, "fees": {"base": "0.00000141"}, "time": 50},
                   "f" * 64: {"vsize": 200, "fees": {"base": "0.00000200"}, "time": 60}, "bad": {"time": 99}}
        node = FakeNode([self.genesis, first, second], pending)
        self.index.sync(node)
        detail = lookup(self.index, "/block/1", {})
        self.assertEqual(detail["proof"], {"model": "Qwen/Qwen3-0.6B@c1899de", "powHash": pow_hash})
        self.assertEqual(detail["rewardOutputs"], [{"address": ADDRESS, "atoms": "5000000000"}])
        self.assertEqual(lookup(self.index, "/block/2", {})["proof"], {"model": None, "powHash": None})
        self.assertEqual(lookup(self.index, "/proof/" + pow_hash, {}), {"height": 1, "hash": "b" * 64, "powHash": pow_hash})
        self.assertIsNone(lookup(self.index, "/proof/" + "e" * 64, {}))
        self.assertEqual(lookup(self.index, "/blocks", {})[0]["model"], None)
        node.raw = {"f" * 64: {"vout": [{"value": "1.50000000", "scriptPubKey": {"address": ADDRESS}}]}}
        self.index.sync(node)
        mempool = lookup(self.index, "/mempool", {})
        self.assertEqual(mempool["transactions"][0]["outputs"], [{"address": ADDRESS, "atoms": "150000000"}])
        self.assertEqual(mempool["transactions"][1]["outputs"], [])
        self.assertEqual([row["txid"] for row in mempool["transactions"]], ["f" * 64, "e" * 64])
        self.assertEqual(mempool["transactions"][0]["feeAtoms"], "200")
        self.assertEqual(mempool["count"], 2)
        holders = lookup(self.index, "/holders", {})
        self.assertEqual(holders["holders"][0], {"address": ADDRESS, "balanceAtoms": "5000000000"})
        self.assertEqual(holders["unspentAtoms"], "7500000000")
        self.assertEqual(holders["height"], 2)
        # Reorganization removes the proof row with its block.
        replacement = FakeNode([self.genesis, first])
        self.index.sync(replacement)
        self.assertIsNone(lookup(self.index, "/block/2", {}))
        self.assertEqual(self.index.db.execute("SELECT count(*) FROM proofs").fetchone()[0], 2)
        self.assertEqual(lookup(self.index, "/holders", {})["unspentAtoms"], "5000000000")

    def test_wallet_batch_balances_coins_and_net_history(self):
        other = "nlab1q" + "p" * 38
        first = block(1, "b" * 64, GENESIS)
        pay = spend("pay", "reward-" + "b" * 64, "49.90000000", other)
        second = block(2, "d" * 64, "b" * 64, [coinbase("reward-2", "25.00000000"), pay])
        self.index.sync(FakeNode([self.genesis, first, second]))
        result = lookup(self.index, f"/wallet/{ADDRESS},{other}", {})
        self.assertEqual(result["balanceAtoms"], str(2_500_000_000 + 4_990_000_000))
        self.assertEqual(result["immatureAtoms"], "2500000000")
        self.assertEqual({c["txid"] for c in result["coins"]}, {"reward-2", "pay"})
        self.assertEqual(sorted(result["usedAddresses"]), sorted([ADDRESS, other]))
        mine = lookup(self.index, f"/wallet/{ADDRESS}", {})
        net = {h["txid"]: h["netAtoms"] for h in mine["history"]}
        self.assertEqual(net["pay"], "-5000000000")
        self.assertEqual(net["reward-2"], "2500000000")
        self.assertEqual(net["reward-" + "b" * 64], "5000000000")
        with self.assertRaises(ValueError):
            self.index.wallet([ADDRESS, ADDRESS])
        with self.assertRaises(ValueError):
            self.index.wallet([])
        self.assertIsNone(lookup(self.index, "/wallet/" + ",".join(["nlab1qqqqqqqqqqqqqq"] * 41), {}))

    def test_records_the_nodes_final_verdicts_by_block_hash(self):
        first = block(1, "b" * 64, GENESIS)
        node = FakeNode([self.genesis, first])
        node.validations = [
            {"block_hash": "b" * 64, "type": "quick", "quick_status": "pass", "full_status": "pending", "timestamp": 10},
            {"block_hash": "b" * 64, "type": "full", "quick_status": "pass", "full_status": "green", "timestamp": 12},
            {"block_hash": "not-a-hash", "full_status": "green"},
            {"block_hash": "c" * 64, "quick_status": "pending", "full_status": "pending"}]
        self.index.sync(node)
        self.assertEqual(lookup(self.index, "/block/1", {})["verdict"], {"quick": "pass", "full": "green", "recordedAt": 12})
        node.validations = [{"block_hash": "b" * 64, "quick_status": "pass", "full_status": "pending", "timestamp": 20}]
        self.index.sync(node)
        self.assertEqual(lookup(self.index, "/block/1", {})["verdict"]["full"], "green")
        self.assertEqual(self.index.db.execute("SELECT count(*) FROM verdicts").fetchone()[0], 1)

    def test_private_work_status_describes_full_verification(self):
        self.index.genesis = "301587d540aafc312cf4606e700d9662e038e7e43ca6cca92779d6b4baa81b4e"
        self.assertIn("fully replays the useful-work proof", self.index.status()["validation"])

    def test_transaction_pages_and_spend_links_survive_extension_not_reorg(self):
        self.index.apply(self.genesis)
        txs = [coinbase(f'{1:064x}')]
        txs += [spend(f'{i:064x}', f'{i-1:064x}', amount='50') for i in range(2, 77)]
        self.index.apply(block(1, 'b'*64, GENESIS, txs))
        page = lookup(self.index, '/block/1/transactions', {})
        self.assertEqual(len(page['transactions']), 50)
        next_page = lookup(self.index, '/block/1/transactions', {'cursor':[page['nextCursor']]})
        self.assertEqual(len(next_page['transactions']), 26)
        self.assertIsNone(next_page['nextCursor'])
        self.assertEqual([t['txid'] for p in (page,next_page) for t in p['transactions']], [f'{i:064x}' for i in range(76,0,-1)])
        self.assertEqual(self.index.transaction(f'{1:064x}')['outputs'][0]['spentBy'], f'{2:064x}')
        self.assertIsNone(self.index.transaction(f'{76:064x}')['outputs'][0]['spentBy'])
        self.assertEqual(self.index.address(ADDRESS)['utxoCount'],1)
        self.assertTrue(self.index.address(ADDRESS)['utxos'][0]['mature'])
        self.index.apply(block(2,'c'*64,'b'*64))
        self.assertEqual(self.index.transactions(page['nextCursor'], '1'), next_page | {'tip':self.index.tip()})
        with self.assertRaises(StaleCursor): self.index.transactions(page['nextCursor'], '2')
        self.index.sync(FakeNode([self.genesis]))
        with self.assertRaises(StaleCursor): self.index.transactions(page['nextCursor'])
        self.assertIsNone(self.index.transactions(block_key='999'))

    def test_new_routes_reject_misplaced_cursors(self):
        for path in ('/status','/statistics','/transactions','/tx/'+'a'*64):
            with self.subTest(path=path), self.assertRaises(ValueError): lookup(self.index,path,{'before':['1']})

    def test_public_routes_do_not_expose_rpc(self):
        self.assertIsNone(lookup(self.index, "/walletpassphrase", {}))
        self.assertIsNone(lookup(self.index, "/tx/../../wallet", {}))
        with self.assertRaises(ValueError): lookup(self.index, "/blocks", {"before": ["-1"]})
    def test_remote_plaintext_rpc_refused(self):
        with self.assertRaises(ValueError): NodeRPC("http://example.com:123", "unused")
        with self.assertRaises(ValueError): NodeRPC("https://user:password@example.com", "unused")
        NodeRPC("http://127.0.0.1:29443", "unused")
    def test_wallet_rejects_other_network_prefixes(self):
        require_lab_address(ADDRESS)
        for address in ("bc1qqqqqqqqqqqq", "tcrt1qqqqqqqqqqqq", "0x1234"):
            with self.assertRaises(ValueError): require_lab_address(address)

if __name__ == "__main__": unittest.main()
