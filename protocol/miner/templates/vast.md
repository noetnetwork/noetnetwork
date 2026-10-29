# Vast.ai template

Create it once under Templates, then rent from it with one click.

| Field | Value |
| --- | --- |
| Image | `nvidia/cuda:12.8.1-devel-ubuntu24.04` |
| Launch mode | SSH (direct) |
| Disk | 150 GB (50 GB with `NOET_14B=0`) |
| Environment | `-e NOET_ADDRESS=noet1... -e NOET_14B=1 -e NOET_GPUS=all` |
| On-start script | the contents of `protocol/miner/cloud-start.sh` |
| Search filter | `num_gpus>=1 gpu_ram>=46 compute_cap>=800 cuda_vers>=12.8 disk_space>=150 reliability>0.98` |

Leave out `NOET_14B=1` and pick `gpu_ram>=22` for a 24 GB card that mines
Qwen3-0.6B until block 3,500 only.

The gateway allows each IPv4 address 360 work leases an hour. All GPUs in one
instance share its address, so `NOET_GPUS=all` helps up to two GPUs before
block 3,500 and three after; rent more instances for more.

The status page of GPU n is on port 8090 + n inside the instance:
`ssh -p <port> root@<host> -L 8090:127.0.0.1:8090`, then open
http://127.0.0.1:8090. Add `-e NOET_NOTIFY=https://ntfy.sh/<topic>` (or a
Slack or Discord webhook) for alerts.

`cloud-start.sh` was validated on one GPU of a Runpod host
(`protocol/validation/cloud-start-20261024.json`), not yet on Vast.ai. Several
GPUs per instance, the status page and alerts are tested offline only.
