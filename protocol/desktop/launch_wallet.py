#!/usr/bin/env python3
"""Launch only the isolated NOET wallet; never inherit TensorCash peers/data."""
import os
from pathlib import Path
import subprocess
import sys


def command(data, executable="noet-wallet"):
    data = Path(data).expanduser().resolve()
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    if data.stat().st_mode & 0o077:
        raise ValueError("Wallet data directory must be private (mode 0700)")
    # Explicit flags prevent upstream first-run defaults from selecting another chain.
    return [executable, "-chain=tensor-reg", "-datadir=" + str(data), "-conf=noet.conf",
            "-dnsseed=0", "-fixedseeds=0", "-discover=0", "-listenonion=0", "-listen=0",
            "-server=0", "-useextapi=0", "-validationapi=mock", "-lang=en", "-choosedatadir=0"]


def main():
    if len(sys.argv) != 1:
        raise SystemExit("Configure explicit NOET lab peers in the private noet.conf; command-line overrides are not accepted.")
    data = os.environ.get("NOET_WALLET_DATA", str(Path.home() / ".noet-wallet-lab"))
    try:
        raise SystemExit(subprocess.call(command(data)))
    except (OSError, ValueError):
        raise SystemExit("Wallet could not start. Check the installation and private data directory.") from None

if __name__ == "__main__":
    main()
