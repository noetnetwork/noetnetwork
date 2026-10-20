"""Local-only native Core wallet client for the NOET laboratory network.

Private keys stay in the node's encrypted descriptor wallet. No web custody.
PSBT signing and broadcast are deliberately separate operations.
"""
import argparse
import base64
import stat
from getpass import getpass
import json
import os
from pathlib import Path
import re
from .rpc import NodeRPC, verify_network
from .indexer import atoms
from .wallet_review import review_psbt

def write_private(path, content):
    # Never overwrite a backup or transaction file; never follow a symlink.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)

def read_psbt(path):
    # Open once, reject symlinks/FIFOs and bound the actual read (not only stat).
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("PSBT must be a regular file")
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("PSBT too large")
    text = raw.decode("ascii").strip()
    try:
        decoded = base64.b64decode(text, validate=True)
    except ValueError:
        raise ValueError("Invalid PSBT encoding") from None
    if not decoded.startswith(b"psbt\xff"):
        raise ValueError("Invalid PSBT header")
    return text

def require_lab_address(address):
    if not re.fullmatch(r"nlab1[ac-hj-np-z02-9]{8,90}", address):
        raise ValueError("Only NOET lab native SegWit addresses are supported")

def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("receive")
    create = sub.add_parser("create")
    create.add_argument("name")
    backup = sub.add_parser("backup")
    backup.add_argument("destination", help="Absolute, new path on the wallet node")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("address")
    prepare.add_argument("amount", help="Decimal test NOET, max 8 decimal places")
    prepare.add_argument("--fee-rate", required=True, type=int, help="Atoms per virtual byte, 1–100")
    prepare.add_argument("--out", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("psbt")
    inspect.add_argument("--max-fee", default="0.001", help="Maximum total fee in test NOET")
    sign = sub.add_parser("sign")
    sign.add_argument("psbt")
    sign.add_argument("--out", required=True)
    sign.add_argument("--max-fee", default="0.001", help="Maximum total fee in test NOET")
    send = sub.add_parser("broadcast")
    send.add_argument("psbt")
    send.add_argument("--confirm-txid", required=True)
    args = p.parse_args()
    rpc = NodeRPC.from_env()
    verify_network(rpc, os.environ["NOET_GENESIS_HASH"])
    if args.command == "create":
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", args.name):
            raise ValueError("Use a simple wallet name")
        password = getpass("New wallet passphrase: ")
        if len(password) < 16 or password != getpass("Repeat passphrase: "):
            raise ValueError("Passphrases must match and have at least 16 characters")
        result = rpc("createwallet", args.name, False, False, password, False, True)
        print(json.dumps(result))
    elif args.command == "status":
        info = rpc("getwalletinfo")
        print(json.dumps({k: info.get(k) for k in ("walletname", "balance", "unconfirmed_balance", "immature_balance", "txcount", "unlocked_until", "descriptors")}))
    elif args.command == "receive":
        address = rpc("getnewaddress", "", "bech32")
        require_lab_address(address)
        print(address)
    elif args.command == "backup":
        if "unlocked_until" not in rpc("getwalletinfo"):
            raise ValueError("Encrypt the wallet before making a backup")
        path = Path(args.destination)
        if not path.is_absolute():
            raise ValueError("Backup destination must be absolute on the node host")
        rpc("backupwallet", str(path))
        print("Encrypted wallet backup requested on the node. Verify restoration on a separate offline copy.")
    elif args.command == "prepare":
        require_lab_address(args.address)
        value = atoms(args.amount)
        if value <= 0 or not 1 <= args.fee_rate <= 100:
            raise ValueError("Positive amount and fee rate 1–100 required")
        if not rpc("validateaddress", args.address).get("isvalid"):
            raise ValueError("Invalid address checksum")
        # Core amount parser accepts decimal strings without floating point conversion.
        result = rpc("walletcreatefundedpsbt", [], [{args.address: args.amount}], 0, {"fee_rate": args.fee_rate, "replaceable": True, "change_type": "bech32"}, True)
        write_private(args.out, result["psbt"])
        print(json.dumps({"file": args.out, "fee": result["fee"], "changePosition": result["changepos"], "broadcast": False}))
    elif args.command == "inspect":
        print(json.dumps(review_psbt(rpc, read_psbt(args.psbt), args.max_fee), indent=2))
    elif args.command == "sign":
        psbt = read_psbt(args.psbt)
        # Review node-checked inputs, recipients and fees before unlocking.
        print(json.dumps(review_psbt(rpc, psbt, args.max_fee), indent=2))
        if input("Type SIGN to sign this laboratory transaction: ") != "SIGN":
            raise SystemExit("Not signed")
        try:
            rpc("walletpassphrase", getpass("Wallet passphrase: "), 30)
            signed = rpc("walletprocesspsbt", psbt, True, "ALL")
            if not signed.get("complete"):
                raise ValueError("Wallet could not fully sign the transaction")
            write_private(args.out, signed["psbt"])
        finally:
            rpc("walletlock")
        final = rpc("finalizepsbt", signed["psbt"])
        if not final.get("complete"):
            raise ValueError("Signed PSBT saved, but finalization failed; nothing broadcast")
        transaction = rpc("decoderawtransaction", final["hex"])
        print(json.dumps({"file": args.out, "txid": transaction["txid"], "broadcast": False}))
    elif args.command == "broadcast":
        final = rpc("finalizepsbt", read_psbt(args.psbt))
        if not final.get("complete"):
            raise ValueError("PSBT is not fully signed")
        decoded = rpc("decoderawtransaction", final["hex"])
        if args.confirm_txid != decoded["txid"]:
            raise ValueError("Confirmation must match the decoded transaction ID")
        # Cap broadcast fee at 0.001 coin/kB (100 atoms/vB).
        verdict = rpc("testmempoolaccept", [final["hex"]], "0.001")[0]
        if not verdict.get("allowed"):
            raise ValueError("Node rejected transaction; nothing broadcast")
        print(rpc("sendrawtransaction", final["hex"], "0.001"))

if __name__ == "__main__":
    main()
