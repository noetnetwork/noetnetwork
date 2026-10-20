import unittest
from noet.faucet import Faucet, Refused
from noet.rpc import RPCError, RPCRejected

ACCOUNT = "a" * 64
OTHER = "b" * 64
ADDRESS = "nwrk1q" + "q" * 38
SECOND = "nwrk1q" + "p" * 38


class FakeWallet:
    def __init__(self, trusted="5.00000000"):
        self.calls, self.trusted, self.locked, self.sent = [], trusted, True, []

    def __call__(self, method, *params):
        self.calls.append(method)
        if method == "getbalances":
            return {"mine": {"trusted": self.trusted, "immature": "715.00000000"}}
        if method == "getaddressinfo":
            return {"ismine": params[0] == "nwrk1qmmmm" + "q" * 34}
        if method == "walletpassphrase":
            self.locked = False
            return None
        if method == "walletlock":
            self.locked = True
            return None
        if method == "sendtoaddress":
            if self.locked:
                raise AssertionError("send while locked")
            self.sent.append(params)
            return f"{len(self.sent):064x}"
        if method in ("listlabels",):
            return []
        if method == "getnewaddress":
            return "nwrk1qreward" + "q" * 30
        raise AssertionError(method)


def node(method, *params):
    if method == "validateaddress":
        return {"isvalid": params[0].startswith("nwrk1")}
    if method == "listwallets":
        return []
    if method == "listwalletdir":
        return {"wallets": []}
    if method == "createwallet":
        return {"name": params[0]}
    raise AssertionError(method)


class FaucetTests(unittest.TestCase):
    def setUp(self):
        self.now = [1_800_000_000]
        self.wallet = FakeWallet()
        self.faucet = Faucet(node, self.wallet, "p" * 32, ":memory:", "1", daily_limit=3, clock=lambda: self.now[0])

    def test_pays_once_per_account_and_address_and_relocks(self):
        result = self.faucet.drip(ACCOUNT, ADDRESS)
        self.assertEqual(result["amountAtoms"], "100000000")
        self.assertEqual(self.wallet.sent[0][:2], (ADDRESS, "1.00000000"))
        self.assertTrue(self.wallet.locked)
        with self.assertRaisesRegex(Refused, "account already"):
            self.faucet.drip(ACCOUNT, SECOND)
        with self.assertRaisesRegex(Refused, "address already"):
            self.faucet.drip(OTHER, ADDRESS)
        self.now[0] += 86401
        self.faucet.drip(ACCOUNT, ADDRESS)
        self.assertEqual(len(self.wallet.sent), 2)

    def test_global_daily_cap_and_input_validation(self):
        for i in range(3):
            self.faucet.drip(f"{i:064x}", "nwrk1q" + "acdefgh"[i] * 38)
        with self.assertRaisesRegex(Refused, "used up") as caught:
            self.faucet.drip(OTHER, SECOND)
        self.assertEqual(caught.exception.status, 429)
        for account, address in (("short", SECOND), (OTHER, "nlab1q" + "q" * 38), (OTHER, "bc1qxyz"),
                                 (OTHER, None), (None, SECOND)):
            with self.subTest(address=address), self.assertRaises(Refused):
                self.faucet.drip(account, address)

    def test_refuses_own_address_and_empty_balance_without_unlocking(self):
        with self.assertRaisesRegex(Refused, "own wallet"):
            self.faucet.drip(ACCOUNT, "nwrk1qmmmm" + "q" * 34)
        self.wallet.trusted = "0.50000000"
        with self.assertRaisesRegex(Refused, "mature") as caught:
            self.faucet.drip(ACCOUNT, ADDRESS)
        self.assertEqual(caught.exception.status, 503)
        self.assertNotIn("walletpassphrase", self.wallet.calls)

    def failing_send(self, error):
        def wallet(method, *params):
            if method == "sendtoaddress":
                raise error
            return FakeWallet.__call__(self.wallet, method, *params)
        return wallet

    def test_relocks_and_allows_a_retry_when_the_node_refuses_payment(self):
        self.faucet.wallet = self.failing_send(RPCRejected("Node rejected RPC: sendtoaddress"))
        with self.assertRaises(RPCRejected):
            self.faucet.drip(ACCOUNT, ADDRESS)
        self.assertTrue(self.wallet.locked)
        self.faucet.wallet = self.wallet
        self.faucet.drip(ACCOUNT, ADDRESS)
        self.assertEqual(len(self.wallet.sent), 1)

    def test_an_unanswered_payment_is_never_retried_the_same_day(self):
        # The node may have sent before timing out; a retry must not pay twice.
        self.faucet.wallet = self.failing_send(RPCError("Node RPC unavailable: sendtoaddress"))
        with self.assertRaises(RPCError):
            self.faucet.drip(ACCOUNT, ADDRESS)
        self.assertTrue(self.wallet.locked)
        self.faucet.wallet = self.wallet
        with self.assertRaisesRegex(Refused, "account already"):
            self.faucet.drip(ACCOUNT, SECOND)
        with self.assertRaisesRegex(Refused, "address already"):
            self.faucet.drip(OTHER, ADDRESS)
        self.assertEqual(self.faucet.status()["remainingToday"], 2)

    def test_a_failed_unlock_releases_the_reservation(self):
        def locked_wallet(method, *params):
            if method == "walletpassphrase":
                raise RPCError("Node RPC unavailable: walletpassphrase")
            return FakeWallet.__call__(self.wallet, method, *params)
        self.faucet.wallet = locked_wallet
        with self.assertRaises(RPCError):
            self.faucet.drip(ACCOUNT, ADDRESS)
        self.faucet.wallet = self.wallet
        self.faucet.drip(ACCOUNT, ADDRESS)

    def test_status_and_wallet_setup(self):
        status = self.faucet.status()
        self.assertEqual(status["availableAtoms"], "500000000")
        self.assertEqual(status["immatureAtoms"], "71500000000")
        self.assertEqual(status["remainingToday"], 3)
        self.assertTrue(self.faucet.ensure_wallet().startswith("nwrk1qreward"))

    def test_rejects_weak_configuration(self):
        with self.assertRaises(ValueError):
            Faucet(node, self.wallet, "short", ":memory:")
        with self.assertRaises(ValueError):
            Faucet(node, self.wallet, "p" * 32, ":memory:", "11")


if __name__ == "__main__":
    unittest.main()


class BroadcastTests(unittest.TestCase):
    def setUp(self):
        from noet.faucet import Broadcaster
        self.calls = []
        self.allowed = True

        def node(method, *params):
            self.calls.append(method)
            if method == "testmempoolaccept":
                return [{"txid": "c" * 64, "allowed": self.allowed, "reject-reason": "bad-txns-inputs-missingorspent"}]
            if method == "sendrawtransaction":
                return "c" * 64
            raise AssertionError(method)
        self.now = [0]
        self.relay = Broadcaster(node, hourly_limit=2, clock=lambda: self.now[0])

    def test_relays_only_after_mempool_acceptance(self):
        self.assertEqual(self.relay.broadcast("02" * 100), {"txid": "c" * 64})
        self.assertEqual(self.calls, ["testmempoolaccept", "sendrawtransaction"])
        self.allowed = False
        with self.assertRaisesRegex(Refused, "missingorspent"):
            self.relay.broadcast("02" * 100)
        self.assertEqual(self.calls.count("sendrawtransaction"), 1)

    def test_undecodable_transaction_is_a_client_error(self):
        from noet.faucet import Broadcaster
        from noet.rpc import RPCError
        def node(method, *params):
            raise RPCError("Node rejected RPC: " + method)
        with self.assertRaises(Refused) as caught:
            Broadcaster(node).broadcast("02" * 100)
        self.assertEqual(caught.exception.status, 400)

    def test_rejects_malformed_input_and_limits_rate(self):
        for bad in ("zz" * 100, "02", None, "02" * 200001, "0" * 201):
            with self.subTest(bad=str(bad)[:10]), self.assertRaises(Refused):
                self.relay.broadcast(bad)
        self.relay.broadcast("02" * 100)
        self.relay.broadcast("02" * 100)
        with self.assertRaises(Refused) as caught:
            self.relay.broadcast("02" * 100)
        self.assertEqual(caught.exception.status, 429)
        self.now[0] = 3601
        self.relay.broadcast("02" * 100)
