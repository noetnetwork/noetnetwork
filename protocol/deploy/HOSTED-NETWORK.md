# Hosted private-work development network

> Status, 16 Oct 2026: NOET Mainnet runs on the Fly app `noet-mainnet` (`launch-mainnet.sh`). The development network (`noet-chain`) and the public testnet (`noet-testnet`) were archived and shut down; this page describes how they ran and how to start them again.

This was the development network: the separately identified `noet-private-work-v1` chain, running with real useful-work verification. Its test coins had no value, the research model's prompt calibration was unapproved (shadow mode) and it kept the inherited laboratory reward schedule, not the NOET tokenomics.

## Topology

```text
                 Fly.io app noet-chain (persistent)                      Temporary GPU host (Runpod)
 ┌─────────────────────────────────────────────────────────┐   ┌────────────────────────────────────┐
 │ native node (genesis 301587d5…, -validationapi=real)     │   │ guarded research verifier (fp32    │
 │   RPC 127.0.0.1:30443 · ZMQ push :18555 / pull :18556    │   │   replay, sealed detector)         │
 │ indexer + explorer API  :8788  ── Fly TLS :443           │   │ continuous miner (pinned engine,   │
 │ faucet (own wallet)      :8790  ── Fly TLS :8443          │   │   flash attention off)             │
 │ relay node-request  → GPU verifier-request :16443  ─────────▶ relay verifier-request             │
 │ relay node-verdict  ◀── :17443 ◀─────────────────────────────── relay verifier-verdict           │
 │ relay node-rpc      ◀── :18443 ◀─────────────────────────────── relay miner-rpc (whitelisted)    │
 └─────────────────────────────────────────────────────────┘   └────────────────────────────────────┘
          ▲ bearer token                                    
 www.noet.network (Vercel): /api/chain/* read-only gateway, /api/faucet (Privy sign-in)
```

Every plaintext socket stays on loopback. The three cross-provider links are mutual TLS 1.3 with a private CA, exact peer-certificate pins and DNS server names; the relay only accepts a public address on its encrypted side when `public_tls` is set explicitly. Verdicts additionally carry the shared HMAC. The miner's RPC user is restricted by `-rpcwhitelist` to work leases, proof submission and verdict reads.

The node accepts a block only after its verifier returns an authenticated Full verdict. When no GPU host is connected, the node and explorer stay online and simply produce no new blocks.

## Operating it

Secrets are generated locally into a private operator directory and never committed:

```sh
python3 protocol/deploy/certs.py --out PRIVATE_DIR/certs        # CA + six relay identities, 30 days
```

Fly secrets: `NOET_EXPLORER_TOKEN`, `VALIDATOR_SHARED_SECRET`, `NOET_TLS_BUNDLE` (node bundle), `NOET_MINER_RPCAUTH`, `NOET_FAUCET_TOKEN`, `NOET_FAUCET_PASSPHRASE` and, while a GPU host is attached, `NOET_VERIFIER_ENDPOINT=<gpu-ip>:<mapped 16443 port>`. Build and deploy with:

```sh
python3 protocol/deploy/fly/prepare.py --out /tmp/noet-fly-context
cd /tmp/noet-fly-context && fly deploy --remote-only --app noet-chain
```

Vercel production needs `NOET_EXPLORER_URL`, `NOET_EXPLORER_TOKEN`, `NOET_FAUCET_URL` and `NOET_FAUCET_TOKEN`.

GPU host: stage inputs with `protocol/gpu/stage.py`, create the Pod with `protocol/gpu/control.py create` (spending cap, deadline, CUDA 12.x host, extra TCP port 16443), start the detached `watchdog` and `sync` processes, build with `session.sh` or `build.sh`/`build_native.sh`, then place `gpu-bundle.b64`, `live.json`, `shared-secret` and `miner-credentials` in `/workspace/noet-live` and run `python3 checks/live.py` as root. It drops every service to UID 10001. `control.py delete` refuses to delete a reachable Pod until collected evidence matches the remote manifest.

## Attaching a GPU host (runbook)

1. Create the Pod with `gpu/control.py create … --tcp-port 16443 --cuda 12.8 --cuda 12.9 --volume 0` and start the detached `watchdog` and `sync` processes.
2. Stage and upload inputs (`gpu/stage.py`, model snapshot, GGUF) and build with `session.sh` or `build.sh` + `build_native.sh`. Make `/workspace` and every model ancestor root-owned.
3. Copy `gpu-bundle.b64`, `shared-secret`, `miner-credentials` and `live.json` (`{"node": "<node IPv4>", "rewardAddress": "<faucet mining address>"}`) to `/workspace/noet-live` (root, 0700/0600).
4. Start `setsid nohup python3 /workspace/noet-gpu/checks/live.py &` as root.
5. Read the Pod's public mapping for port 16443 from the provider API and set `NOET_VERIFIER_ENDPOINT=<pod ip>:<mapped port>` on the node with `fly secrets set` (this restarts the node).
6. Watch `/workspace/noet-gpu/output/live/supervisor.jsonl`, the miner log and `https://noet-chain.fly.dev/healthz/chain`. The explorer shows each block's verdict.

When the Pod is deleted, unset `NOET_VERIFIER_ENDPOINT` or leave it; the node keeps serving the chain and simply produces no new blocks.

### Standby verifier

A second GPU host, ideally with another provider, runs the same steps 1-4 with the same `gpu-bundle.b64` and shared secret, so it presents the same pinned identity. List it after the first: `NOET_VERIFIER_ENDPOINT=<first ip:port>,<standby ip:port>` (up to four). The node's request relay uses the first endpoint while it answers. When it refuses, fails its pin, or closes a fresh link without sending a byte (its relay is up but its verifier is not), the relay skips it for 60 seconds and connects to the next. Verdicts from either host reach the node's verdict relay, which accepts both. The standby's miner mines as usual. `deploy/rotate.py --apply` takes one `--gpu-host` per host and installs new certificates on all of them with the node.

## Faucet

The faucet is a separate process with its own encrypted wallet; the explorer service never holds a passphrase. It unlocks for ten seconds per payment and relocks. Limits are stored on the node: one payment per signed-in account and per address per day, and a global daily cap (default 1 test NOET, 100 per day). The website sends only a salted hash of the Privy subject. Mined rewards are paid to the faucet's `mining` address, so it becomes usable once rewards mature (101 confirmations).

## Costs and limits

Fly: shared-cpu-1x 2 GB, 10 GB volume with snapshots, dedicated IPv4 (about $14/month). Desktop wallet downloads are served by a separate `noet-downloads` app that stops when idle (`deploy/downloads`). Relay certificates last 30 days. `deploy/rotate.py --secrets DIR --check` reports the days left (exit 1 under 7); `deploy/rotate.py --secrets DIR --app APP --apply --gpu-ssh WRAPPER --gpu-host USER@HOST` issues a new set into `DIR/certs` and installs it on the node and the GPU host together. Uptime monitors can poll the public `https://noet-chain.fly.dev/healthz/chain`, which returns 503 when no block has arrived within `NOET_STALL_SECONDS` (default one hour). GPU time is the variable cost; within the owner's $100/month cap a GPU can run part-time only. The chain is a single node operated by one party; independent peers, monitoring ownership and independent security review remain release gates (see `../NETWORK-PLAN.md`).

## Public testnet (noet-testnet-1)

The same supervisor runs the public testnet when the build context is made with
`prepare.py --profile testnet` (release variant: real external verification
only, proposed economics, `tnoet` addresses). It differs from the development
node in three ways: it listens for peers on TCP 33441, it uses its own Fly app
(`noet-testnet`, `fly.testnet.toml`) and volume, and it has its own secret set
(`network_secrets.py`), so the two networks share no keys, tokens or
certificates. Its mining gateway was `https://noet-testnet.fly.dev:9443` until the testnet was shut down on 16 Oct 2026; the commands below start it again.

```sh
python3 protocol/deploy/network_secrets.py --out PRIVATE_DIR       # once
protocol/deploy/launch-testnet.sh node PRIVATE_DIR                 # app, IPs, volume, secrets, deploy
protocol/deploy/launch-testnet.sh gpu  PRIVATE_DIR SSH USER@HOST GPU_IP:PORT
protocol/deploy/launch-testnet.sh web  PRIVATE_DIR                 # Vercel env + deploy
```

A GPU host serves one network at a time; the `gpu` stage keeps the previous
configuration in `/workspace/noet-live/previous/`. Expected hosting cost:
about $14/month for the node and $2/month for its dedicated IPv4 address.
