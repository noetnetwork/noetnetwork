# NOET protocol

The node patches, verifier, miner and services behind NOET Mainnet (`noet-mainnet-1`), live since 15 October 2026. Status and open work: [MAINNET.md](MAINNET.md). How it works: [WHITEPAPER.md](WHITEPAPER.md).

## Components

- `upstream.lock.json`: pinned upstream node and inference engine sources.
- `patches/noet-lab.patch`: network identities, genesis timestamps, address prefixes, P2P port and removal of upstream snapshot commitments.
- `patches/noet-miner.patch`: explicit test-mode opt-in, local-only VDF proving, synchronized state updates, immutable registered model selection and guarded local model startup. See [MINER-RUNTIME.md](MINER-RUNTIME.md).
- `patches/noet-engine.patch`: preserve optional node-issued job heights through the inference engine, reject malformed heights, and retain absence across slot reuse. Applied by `bootstrap.py --engines` and recorded in the CPU source export.
- `docker/Node.Dockerfile`: node, native descriptor wallet, CLI and C++ tests, without the upstream mining supervisor or desktop/VNC services.
- `noet/wallet.py`: local native wallet operations and separate prepare / inspect / sign / broadcast steps.
- `noet/indexer.py`: canonical blocks, transactions and address history, exact integer amounts, atomic updates and reorganization rollback.
- `noet/serve.py`: authenticated, loopback-only, read-only explorer HTTP service.
- `mainnet/`, `testnet/`: network profiles, genesis and block-1 allocations.
- `miner/`: the standalone GPU miner.
- `noet/gateway.py`: the public mining gateway; `noet/tasks.py`: the request queue and open dataset that miners answer.

**The local laboratory network (`lab.network.json`) uses explicit mock inference verification.** Its blocks are integration-test fixtures, not evidence that useful-work mining is secure, and it keeps inherited emission rules. Mainnet's release build refuses mock validation.

## Build and test

Python 3.12+, Git with LFS, Docker and sufficient build disk/RAM are required. Binaries are not signed, and build dependencies are not yet fully reproducibly locked.

```sh
python3 protocol/bootstrap.py
python3 protocol/source_bundle.py --out /tmp/noet-node-context.tar
docker build -t noet-core:lab - < /tmp/noet-node-context.tar
cd protocol
python3 -m unittest discover -s tests -v
PYTHONPATH=. python3 tests/node_smoke.py --reindex
```

See [SOURCE-BUILDS.md](SOURCE-BUILDS.md) for source and image provenance. The source export excludes untracked and ignored checkout files.

The smoke test creates two disposable nodes on a Docker **internal** network, creates test wallets, mines laboratory blocks, prepares/signs/broadcasts a native PSBT, checks indexer rollback, restores an encrypted wallet backup and exercises restart/reindex. It removes only resources it creates. It never connects to upstream peers or starts a public network. Set `NOET_DOCKER_CONTEXT` if using a non-default Docker context.

The genesis in `lab.network.json` was observed on two independently initialized lab nodes. It is not a public-testnet genesis.

Initialize a persistent isolated lab only on a chosen development host:

```sh
docker compose -f protocol/deploy/lab.compose.yml up -d
```

No ports are published by this composition. Use `docker compose ... exec node-a bitcoin-cli -chain=tensor-reg -rpcport=29443 ...` for local operations. Stop with `docker compose ... down`; keep the volumes unless deliberately discarding test wallets.

## Explorer service

Run alongside a fully indexed, unpruned node with `txindex=1`. Set:

```sh
NOET_RPC_URL=http://127.0.0.1:29443
NOET_RPC_COOKIE=/absolute/node-data/tensor-reg/.cookie
NOET_GENESIS_HASH=fc0ac105e8adb2401780e6fd54ec48650912c5e460d0a4ffe14ba1b83e2df359
NOET_INDEX_PATH=/persistent/noet/explorer.sqlite3
NOET_EXPLORER_TOKEN=<random-secret-at-least-32-characters>
```

Then run `python3 -m noet.serve` from this directory. These are environment settings, not files to commit. The process binds to loopback port 8788. Put an authenticated HTTPS reverse proxy in front of it; keep raw RPC private. The Vercel website uses only `NOET_EXPLORER_URL` and `NOET_EXPLORER_TOKEN`, never the node's RPC cookie or wallet credentials.

The indexer must start at genesis and fails rather than inventing balances if a previous output is missing. It reports stale data, indexing lag and initial node synchronization. Address balances exclude mempool changes and separate immature rewards. Genesis outputs are excluded from spendable balance. Block and transaction detail lists are bounded and indicate truncation. This is a canonical-chain view: orphaned transactions disappear until mined again.

## Native wallet

Use a separate local wallet node, not the public explorer node. RPC authentication uses the node cookie. For a selected wallet, append `/wallet/<wallet-name>` to `NOET_RPC_URL` after creating it. Pin `NOET_GENESIS_HASH` first. Private keys remain in the encrypted native Core wallet; they are never passed to Privy or the website.

```sh
python3 -m noet.wallet create personal
python3 -m noet.wallet receive
python3 -m noet.wallet status
python3 -m noet.wallet backup /absolute/new/path/personal.backup
python3 -m noet.wallet prepare nlab1... 1.25 --fee-rate 2 --out payment.psbt
python3 -m noet.wallet inspect payment.psbt
python3 -m noet.wallet sign payment.psbt --out signed.psbt
python3 -m noet.wallet broadcast signed.psbt --confirm-txid <txid-printed-by-sign>
```

The prepare command is not a broadcast. Inspection and signing accept unsigned native P2WPKH transactions only. Before asking for a passphrase, signing checks each input against the connected node, confirms wallet ownership and reward maturity, and displays every recipient, amount, fee, locktime and replacement setting. It rejects unknown extensions, partially signed transactions and signature modes other than SIGHASH_ALL. The default total fee ceiling is 0.001 test NOET (adjustable with `--max-fee`); the fee rate cannot exceed 100 atoms per virtual byte. Check the displayed recipients yourself. Signing saves a private file and prints the transaction ID for the separate broadcast command. PSBT files must be regular files, no larger than 1 MiB; symlinks are rejected. Broadcasting separately requires the exact decoded transaction ID and node mempool acceptance. Protect and restore-test backups. The initial wallet is a developer CLI, not a finished consumer browser/mobile wallet. Privy remains application sign-in, including Ethereum wallet identity; it is not NOET key custody.

## Mining integration

`python3 protocol/bootstrap.py --engines` fetches the pinned proof-capable llama.cpp and vLLM sources. The verifier source is already present in the umbrella checkout. **Do not run upstream deployment defaults unchanged**: some use public upstream endpoints, moving image tags, old proof versions and example secrets.

Bootstrap applies the reviewed miner patch and checks the umbrella checkout for unrelated tracked edits. Run the Python tests after preparing sources to exercise the actual miner configuration. The patch disables remote VDF fallback and unverified proof mode and enforces fixed model identity; it does not demonstrate GPU inference-proof generation or verification. Mainnet's GPU verifier runs on a separate host and reaches the node over the private mutual-TLS relay (`deploy/transport/README.md`).

A normal OpenAI-compatible endpoint or OpenAI API key cannot supply the required internal inference evidence.

See [NETWORK-PLAN.md](NETWORK-PLAN.md) for the architecture and the release gates set before launch, and [VALIDATION.md](VALIDATION.md) and `validation/` for test evidence and limitations.

## Licenses

NOET's wrapper code and patches do not remove upstream notices. The pinned umbrella `LICENSE` / `LICENSING.md` specify Apache-2.0 for its first-party files, while its README says MIT. Treat that discrepancy as an upstream clarification item. bcore retains its own MIT license; third-party submodules and model weights have separate licenses. Review the exact distribution manifest before releasing binaries.

### Address history pagination

`/address/<address>` returns up to 100 transactions in descending block and transaction order, with `nextCursor` when another page exists. Request the next page with `?cursor=<URL-encoded nextCursor>`. The cursor binds the last transaction to its indexed block, so pages can split a busy block without skipping records. If a reorganization removes that anchor, the service returns HTTP 409 and the client must restart from the first page. New blocks do not invalidate existing anchors. Balances always reflect the current indexed chain, not a frozen historical snapshot. The older `before` height filter remains supported but cannot be combined with a transaction cursor.

### Miner proof-source policy

The reviewed miner patch disables remote VDF proof fallback. `VDF_FALLBACK_ENABLED` and `VDF_FALLBACK_ALLOW_UNVERIFIED` must be unset or explicitly false (`false`, `0` or `no`). A nonempty `VDF_FALLBACK_URL` is rejected. Errors identify the setting without echoing its value. The VDF service refuses startup if its local `chiavdf` dependency is unavailable; it does not silently switch to remote proofs.

This policy applies to the pinned miner sources prepared by `protocol/bootstrap.py`. It does not modify running containers or prove GPU inference correctness. Synthetic jobs remain opt-in through `TEST_MODE=true` for isolated testing only. No production miner image is released by this change.

Mining context updates now serialize block changes and proof updates. A proof for the previous block is rejected, and competing proof updates cannot reduce the current tick. Readers receive immutable snapshots. The concurrency regressions run as part of the protocol test suite against the pinned, patched miner source.

### Registered model selection

Ordinary mining must find the requested model in the connected registry. Name-based selection requires exactly one registered revision; multiple revisions require an explicit model pin. Pinned selection checks that the registry returns the exact requested name and revision. Missing or incomplete identities, lookup failures and mismatched hashes are rejected. The proxy does not silently replace a mining request with a default model.

The separately enabled genesis-generation path still has an explicit built-in model configuration. This change does not approve that model, validate downloaded weight files, or establish a production registry. Inference-only and non-mining audit routes are outside this mining-selection policy.

## One-command acceptance

`python3 protocol/validate.py --mysql --native` from the repository root runs the complete local acceptance sequence using a disposable database and isolated native nodes. Set `NOET_DOCKER_CONTEXT` when needed. Prepare pinned sources and images first; the command does not start Docker or rent infrastructure. See `RELEASE-STATUS.md` for the acceptance record up to launch, and `MODEL-ARTIFACTS.md` for model-file verification.

Guarded verifier preparation and its acceptance limits are documented in [VERIFIER-RUNTIME.md](VERIFIER-RUNTIME.md).
