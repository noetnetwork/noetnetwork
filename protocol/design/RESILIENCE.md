# Resilience plan

Draft, 29 October 2026. Today `noet-mainnet-1` runs on one Fly node
(`noet-mainnet`, region yyz, one 10 GB volume) with no peers, and one GPU
verifier on a rented host. If the verifier stops, no block is accepted; if the
node or its volume is lost, the chain survives only on the verifier's and
miners' copies. This plan removes those single points in order of cost. Items
marked **owner** spend money or change production and are not done by code
alone.

## 1. Standby verifier on a second provider (owner)

What exists: the node's request relay takes up to four verifier endpoints
(`NOET_VERIFIER_ENDPOINT=a:p,b:p`, `deploy/fly/noet_node/supervise.py`). It
uses the first while it answers and skips one for 60 seconds when it refuses,
fails its pin or closes a fresh link without a byte. Verdicts from any listed
host are accepted. The runbook is "Standby verifier" in
`deploy/HOSTED-NETWORK.md`; `deploy/rotate.py --apply` takes one `--gpu-host`
per host.

What is missing:

- A second host with another provider than the first, so one account balance or
  one provider outage cannot stop verdicts. From block 3,500 it needs 80 GB of
  GPU memory for Qwen3-14B (`MODEL-SWITCH.md`).
- **Verdict attribution.** Every verifier presents the same pinned identity
  and the same shared secret, so the node cannot say which host gave a
  verdict. Proposed: each verifier adds a host label (not a secret) to its
  verdict record, inside the signed part, and the explorer shows it. This
  changes the verdict format, so it ships with the next verifier and node
  release, not as a hot change.
- A monitor check that each listed endpoint answers, not only the first (the
  monitor today alerts when verdicts stop, which is late).

## 2. A second, independent node (owner)

The P2P port 33440 is public and `mainnet/network.json` lists one seed,
`noet-mainnet.fly.dev`. A second node needs:

- its own volume and a region and provider other than Fly yyz;
- its own Full verifier. The release build refuses mock or disabled external
  validation (`WHITEPAPER.md` section 8), so a validating node without a GPU
  verifier is not possible today. Two nodes can share the verifier pool only
  if both hold relay certificates the verifiers pin, which means issuing a
  second node identity in `deploy/certs.py` and adding its pin to every
  verifier bundle;
- its name added to `seeds` in `mainnet/network.json` and a DNS seed record
  (for example `seed.noet.network`, one A record per node) once two nodes run.

## 3. Running a node from outside

A guide for outsiders belongs in `mainnet/README.md` once item 2 works for
the operator first. It must say plainly that a validating node needs a GPU
verifier with the approved model files, and that a node without one can only
follow headers it cannot check.

## 4. Snapshots and fast sync

A signed chain snapshot lets a new node start from a recent height:

1. Stop the node cleanly (or use a filesystem snapshot), archive `blocks/`
   and `chainstate/` at a height at least 100 blocks deep.
2. Publish `noet-mainnet-<height>.tar.zst`, its SHA-256 and the block hash at
   that height. Sign the checksum file the way releases are signed
   (`design/SECURITY-PROGRAM.md`).
3. A node that loads a snapshot must still check the headers from genesis to
   the snapshot height against the published hash before it serves data.

Snapshots are a convenience for followers, never a substitute for replay:
the snapshot cannot carry the verifier's work.

## 5. Backups and restore drills

Fly takes automatic daily snapshots of volumes and keeps them for 5 days by
default. They stay with Fly and in one region, and none has been restored.
Proposed:

- A daily job (in the monitor app, which already runs every 2 minutes) that
  asks the node for a consistent copy, encrypts it with an age public key
  whose private key is held off-line by the owner, and uploads it to object
  storage with another provider. Cost at today's chain size: cents a month.
- A monthly restore drill on a throwaway machine: restore, start the node
  without peers, compare tip hash and the UTXO set hash
  (`gettxoutsetinfo`, if the node build keeps it) with production, then delete the machine. Record each
  drill in `validation/` with `productionReady: false` until three pass.

## 6. Restart and standby on Fly

The explorer service has an HTTP health check (`/healthz`); the gateway and
relay services have none. Fly service checks take a machine out of routing
but do not restart it, and with one machine there is nowhere else to route.
Proposed, in order:

1. A process-level watchdog inside the node machine: `supervise.py` already
   restarts its children; add a check that the gateway answers
   `/mining/info` locally and restart the gateway alone when it does not.
2. A cold standby: a stopped machine in a second region with its own volume,
   kept in sync from the daily backup. Starting it is a manual step, because
   two nodes with the same relay identity must never run at once.

## 7. Chaos drills

Once items 1 and 5 exist, rehearse on a schedule and write down how long
recovery took:

| Drill | Expected recovery |
|---|---|
| Stop the first verifier | relay moves to the standby within 60 s; no gap in verdicts |
| Stop the gateway process | supervisor restarts it; miners back off and resume |
| Restart the node machine | blocks resume after start-up; no reorganisation |
| Restore the volume from backup | tip and UTXO hash match production |
| Expire a relay certificate on a copy | monitor alerts 3 days ahead; `rotate.py` renews |

Drills on mainnet only stop components that have a working standby.

## 8. Already in place

- Monitor alerts: verdict wait, certificate expiry (urgent at 3 days),
  GPU host credit (daily, then at 24, 12, 6, 3 and 1 hours), reorganisations
  (`deploy/monitor/monitor.py`).
- Gateway load: limits hold under parallel load at about 6,500 to 7,600 leases
  a second on one core (`validation/gateway-load-20261029.json`).
- Block intervals: recent windows average 607 and 619 s against the 600 s
  target (`validation/block-intervals-20261029.json`).
