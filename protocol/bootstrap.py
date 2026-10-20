#!/usr/bin/env python3
"""Fetch pinned sources; apply reviewed lab, miner and optional engine patches.

Does not build, run a node, download models, or execute upstream setup scripts.
"""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent

def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()

def apply_reviewed_patch(source, patch, *, ignore_submodules=False):
    reverse = subprocess.run(["git", "-C", str(source), "apply", "--reverse", "--check", str(patch)], capture_output=True)
    if reverse.returncode != 0:
        git(source, "apply", "--check", str(patch))
        git(source, "apply", str(patch))
    options = ["--ignore-submodules=all"] if ignore_submodules else []
    actual = git(source, "diff", "--no-ext-diff", "--binary", *options, "HEAD")
    if actual != patch.read_text().strip():
        raise SystemExit("Source contains changes outside the reviewed NOET patch: " + str(source))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engines", action="store_true", help="Also fetch pinned inference engine sources")
    args = parser.parse_args()
    lock = json.loads((ROOT / "upstream.lock.json").read_text())
    dest = ROOT / ".upstream"
    if not dest.exists():
        subprocess.run(["git", "clone", "--no-checkout", "--depth", "1", "--branch", lock["release"], lock["repository"], str(dest)], check=True)
        git(dest, "checkout", "--detach", lock["commit"])
    if git(dest, "rev-parse", "HEAD") != lock["commit"]:
        raise SystemExit("Upstream checkout does not match the lock; refusing to modify it.")
    apply_reviewed_patch(dest, ROOT / "patches/noet-miner.patch", ignore_submodules=True)
    components = ["services/core-node/bcore", "shared-utils/liboqs", "shared-utils/secp256k1-zkp"]
    if args.engines:
        components += ["services/miner-api/llama.cpp", "services/miner-api/vllm-v016"]
    git(dest, "submodule", "update", "--init", "--depth", "1", "--", *components)
    for path in components:
        if path in lock["components"] and git(dest / path, "rev-parse", "HEAD") != lock["components"][path]:
            raise SystemExit("Submodule pin mismatch: " + path)
    core = dest / "services/core-node/bcore"
    patch = ROOT / "patches/noet-lab.patch"
    # A pinned commit is insufficient if unrelated local modifications enter the build.
    apply_reviewed_patch(core, patch)
    engine = "services/miner-api/llama.cpp"
    if args.engines:
        apply_reviewed_patch(dest / engine, ROOT / "patches/noet-engine.patch")
    for path in components:
        if path not in ("services/core-node/bcore", engine) and git(dest / path, "status", "--porcelain", "--untracked-files=no"):
            raise SystemExit("Pinned dependency has local modifications: " + path)
    print("Pinned source prepared with the reviewed NOET patches.")

if __name__ == "__main__":
    main()
