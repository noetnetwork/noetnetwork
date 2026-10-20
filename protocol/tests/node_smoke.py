#!/usr/bin/env python3
"""Two-node disposable LAB test. Requires noet-core:lab. No useful-work claim.

Creates its own internal Docker network and removes only its own containers.
Tests actual node agreement, native wallet transfer and indexer rollback.
"""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
from noet.indexer import Index
from noet.wallet_review import review_psbt
from noet.native_host import handle as native_request

DOCKER = ["docker"] + (["--context", os.environ["NOET_DOCKER_CONTEXT"]] if os.environ.get("NOET_DOCKER_CONTEXT") else [])
IMAGE = os.environ.get("NOET_NODE_IMAGE", "noet-core:lab")
prefix = "noet-test-" + uuid.uuid4().hex[:8]
created = []

def cmd(*args):
    result = subprocess.run(DOCKER + list(args), capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-1500:])
    return result.stdout.strip()

def rpc(node, method, *params):
    result = cmd("exec", node, "bitcoin-cli", "-chain=tensor-reg", "-rpcport=29443", method,
                 *(p if isinstance(p, str) else json.dumps(p) for p in params))
    try: return json.loads(result, parse_float=str)
    except json.JSONDecodeError: return result

def wait_for(check, seconds=90):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            value = check()
            if value: return value
        except Exception as error: last = error
        time.sleep(.5)
    raise RuntimeError("Timed out waiting for node state: " + str(last))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reindex", action="store_true", help="Also exercise the upstream full block-file recovery path")
    parser.add_argument("--partition", action="store_true", help="Exercise competing branches, reconnection and abrupt crash recovery")
    args = parser.parse_args()
    image_id = cmd("image", "inspect", "--format", "{{.Id}}", IMAGE)
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise RuntimeError("An existing immutable local node image is required")
    a, b = prefix + "-a", prefix + "-b"
    cmd("network", "create", "--internal", prefix)
    try:
        for node in (a, b):
            extra = ["-connect=" + a + ":29441"] if node == b else []
            cmd("run", "-d", "--name", node, "--network", prefix, "--pull=never", image_id, "-server=1", "-txindex=1", "-validationapi=mock", "-useextapi=0", "-port=29441", "-rpcport=29443", "-fallbackfee=0.0002", "-spv-asn-min=1", "-spv-hysteresis-base-bps=0", *extra)
            created.append(node)
            wait_for(lambda: rpc(node, "getblockchaininfo"))
        genesis = rpc(a, "getblockhash", 0)
        assert rpc(b, "getblockhash", 0) == genesis
        assert genesis == json.loads((Path(__file__).resolve().parents[1] / "lab.network.json").read_text())["genesis"]
        password = uuid.uuid4().hex + uuid.uuid4().hex
        rpc(a, "createwallet", "lab-a", False, False, password, False, True)
        rpc(b, "createwallet", "lab-b")
        address_a, address_b = rpc(a, "getnewaddress", "", "bech32"), rpc(b, "getnewaddress", "", "bech32")
        assert address_a.startswith("nlab1") and address_b.startswith("nlab1")
        wait_for(lambda: rpc(a, "getconnectioncount") >= 1)
        rpc(a, "generatetoaddress", 101, address_a)
        wait_for(lambda: rpc(a, "getbestblockhash") == rpc(b, "getbestblockhash"))
        funded = native_request({"method":"prepare", "address":address_b, "amount":"1.25"},
                                lambda method, *params: rpc(a, method, *params), genesis)
        assert funded["broadcast"] is False and funded["testCoinsOnly"] is True
        assert rpc(a, "getwalletinfo")["unlocked_until"] == 0
        assert rpc(a, "getmempoolinfo")["size"] == 0
        review = review_psbt(lambda method, *params: rpc(a, method, *params), funded["psbt"])
        assert any(row["address"] == address_b and row["testNOET"] == "1.25000000" and row["ownership"] == "External recipient" for row in review["outputs"])
        assert review["broadcast"] is False
        assert rpc(a, "getwalletinfo")["unlocked_until"] == 0
        rpc(a, "walletpassphrase", password, 30)
        signed = rpc(a, "walletprocesspsbt", funded["psbt"])
        rpc(a, "walletlock")
        assert signed["complete"]
        final = rpc(a, "finalizepsbt", signed["psbt"])
        assert final["complete"]
        decoded = rpc(a, "decoderawtransaction", final["hex"])
        assert rpc(a, "testmempoolaccept", [final["hex"]], "0.001")[0]["allowed"]
        txid = rpc(a, "sendrawtransaction", final["hex"], "0.001")
        assert txid == decoded["txid"]
        rpc(a, "generatetoaddress", 1, address_a)
        wait_for(lambda: rpc(b, "gettransaction", txid)["confirmations"] >= 1)
        with tempfile.TemporaryDirectory(prefix="noet-index-") as folder:
            index = Index(Path(folder) / "chain.sqlite3", genesis)
            while index.tip()["height"] < rpc(a, "getblockcount"):
                index.sync(lambda method, *params: rpc(a, method, *params))
            assert index.address(address_b)["balanceAtoms"] == "125000000"
            tip = rpc(a, "getbestblockhash")
            rpc(a, "invalidateblock", tip)
            index.sync(lambda method, *params: rpc(a, method, *params))
            assert index.transaction(txid) is None
            assert index.address(address_b)["balanceAtoms"] == "0"
            rpc(a, "reconsiderblock", tip)
            index.sync(lambda method, *params: rpc(a, method, *params))
            assert index.address(address_b)["balanceAtoms"] == "125000000"
            index.db.close()
        # Restore an encrypted backup into a separate wallet and compare ownership/balance.
        before_backup = rpc(a, "getbalances")
        rpc(a, "backupwallet", "/tmp/lab-a.backup")
        rpc(a, "unloadwallet", "lab-a")
        rpc(a, "restorewallet", "lab-restored", "/tmp/lab-a.backup")
        assert rpc(a, "getwalletinfo")["unlocked_until"] == 0
        assert rpc(a, "getaddressinfo", address_a)["ismine"]
        assert rpc(a, "getbalances") == before_backup
        # A balance alone does not establish that a backup restored spend authority.
        restored_psbt = rpc(a, "walletcreatefundedpsbt", [], [{address_b: "0.1"}], 0, {"fee_rate": 2, "change_type": "bech32"}, True)
        review_psbt(lambda method, *params: rpc(a, method, *params), restored_psbt["psbt"])
        try:
            rpc(a, "walletpassphrase", "deliberately-wrong-test-passphrase", 30)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Wrong passphrase unlocked the restored wallet")
        assert rpc(a, "getwalletinfo")["unlocked_until"] == 0
        native_rpc = lambda method, *params: rpc(a, method, *params)
        assert native_request({"method": "status"}, native_rpc, genesis)["locked"]
        cancelled = native_request({"method": "sign", "psbt": restored_psbt["psbt"]}, native_rpc, genesis, approve=lambda review: None)
        assert cancelled["cancelled"] and rpc(a, "getwalletinfo")["unlocked_until"] == 0
        # Test-only dependency injection; the production IPC has no approval bypass.
        recovered_signature = native_request({"method": "sign", "psbt": restored_psbt["psbt"]}, native_rpc, genesis, approve=lambda review: password)
        assert recovered_signature["broadcast"] is False
        assert recovered_signature["testCoinsOnly"] is True
        assert rpc(a, "getwalletinfo")["unlocked_until"] == 0
        restored_final = rpc(a, "finalizepsbt", recovered_signature["psbt"])
        assert restored_final["complete"]
        assert rpc(a, "testmempoolaccept", [restored_final["hex"]], "0.001")[0]["allowed"]
        assert rpc(a, "getmempoolinfo")["size"] == 0  # signing never broadcasts
        cmd("exec", a, "python3", "-c", "from pathlib import Path; Path('/tmp/corrupted.backup').write_bytes(b'invalid test backup')")
        try:
            rpc(a, "restorewallet", "must-not-restore", "/tmp/corrupted.backup")
        except RuntimeError:
            pass
        else:
            raise AssertionError("Corrupted backup was accepted")
        assert rpc(a, "getwalletinfo")["walletname"] == "lab-restored"
        rpc(a, "unloadwallet", "lab-restored")
        rpc(a, "loadwallet", "lab-a")
        # A normal clean restart must preserve canonical history and wallet state.
        expected_tip = rpc(a, "getbestblockhash")
        cmd("restart", a)
        wait_for(lambda: rpc(a, "getbestblockhash") == expected_tip)
        rpc(a, "loadwallet", "lab-a")
        assert rpc(a, "gettransaction", txid)["confirmations"] == 1
        if args.reindex:
            cmd("exec", a, "python3", "-c", "from pathlib import Path; Path('/home/noet/.tensorcash/bitcoin.conf').write_text('reindex=1\\n')")
            cmd("restart", a)
            wait_for(lambda: rpc(a, "getbestblockhash") == expected_tip, seconds=120)
        if args.partition:
            rpc(b, "setnetworkactive", False)
            wait_for(lambda: rpc(a, "getconnectioncount") == 0 and rpc(b, "getconnectioncount") == 0)
            rpc(a, "generatetoaddress", 2, address_a)
            orphan_tip = rpc(a, "getbestblockhash")
            rpc(b, "generatetoaddress", 3, address_b)
            winning_tip = rpc(b, "getbestblockhash")
            assert orphan_tip != winning_tip
            with tempfile.TemporaryDirectory(prefix="noet-partition-index-") as folder:
                index = Index(Path(folder) / "chain.sqlite3", genesis)
                while index.tip()["height"] < rpc(a, "getblockcount"):
                    index.sync(lambda method, *params: rpc(a, method, *params))
                assert index.block(orphan_tip) is not None
                rpc(b, "setnetworkactive", True)
                rpc(b, "addnode", a + ":29441", "onetry")
                wait_for(lambda: rpc(a, "getbestblockhash") == winning_tip)
                index.sync(lambda method, *params: rpc(a, method, *params))
                assert index.tip()["hash"] == winning_tip
                assert index.block(orphan_tip) is None
                assert index.transaction(txid)["confirmations"] == 4
                index.db.close()
            # SIGKILL rather than a graceful stop exercises node DB recovery.
            cmd("kill", "--signal", "KILL", a)
            cmd("start", a)
            wait_for(lambda: rpc(a, "getbestblockhash") == winning_tip)
            wait_for(lambda: rpc(a, "getconnectioncount") >= 1)
        print(json.dumps({"imageId": image_id, "partitionRecovery": "passed" if args.partition else "not run", "crashRecovery": "passed" if args.partition else "not run", "encryptedBackupRestore": "passed", "restoredSpendAuthority": "passed", "nativeBridgeSigning": "passed (approval callback, not desktop UI)", "nativeBridgePreparation": "passed without unlocking", "wrongPassphraseRejected": "passed", "corruptedBackupRejected": "passed", "reindex": "passed" if args.reindex else "not run", "restart": "passed", "network": "noet-lab", "genesis": genesis, "nodes": 2, "height": rpc(a, "getblockcount"), "transfer": "1.25 test NOET", "addressPrefix": "nlab1", "indexerRollback": "passed", "validation": "mock—NOT useful-work mining"}, indent=2))
    finally:
        for node in created:
            subprocess.run(DOCKER + ["rm", "-f", node], capture_output=True)
        subprocess.run(DOCKER + ["network", "rm", prefix], capture_output=True)

if __name__ == "__main__": main()
