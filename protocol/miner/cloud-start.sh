#!/bin/bash
# Start script for mining on a rented GPU container (Vast.ai, Runpod and similar):
# paste it as the on-start script, or run it once by hand. It builds whatever is
# missing, then mines, and starts the miner again if it exits. Each container
# start runs it again; finished steps are skipped.
#
#   Image   nvidia/cuda:12.8.1-devel-ubuntu24.04 (any image with the CUDA 12.4+
#           toolkit, git and Python 3.10+ works), driver 570 or newer
#   Disk    150 GB; 50 GB with NOET_14B=0
#   Env     NOET_ADDRESS=noet1...  your reward address (required)
#           NOET_14B=0             24 GB cards: Qwen3-0.6B only, which mines
#                                  until block 3,500
#           NOET_GPUS=all          one miner per GPU (default: one miner, GPU 0)
#           NOET_NOTIFY=https://.. webhook for alerts (Slack, Discord, ntfy)
#           NOET_DIR=/root         where the source, build and models go
#           NOET_REPO=<git URL>    source to build (default: the public repository)
#
# Progress: tail -f $NOET_DIR/noet.log. Status page: ssh -L 8090:127.0.0.1:8090
# to the machine, then open http://127.0.0.1:8090 (GPU n on port 8090 + n).
DIR=${NOET_DIR:-/root}
mkdir -p "$DIR"
cat > "$DIR/noet-run.sh" <<'RUN'
#!/bin/bash
set -euo pipefail
DIR=${NOET_DIR:-/root}
exec 9>"$DIR/noet.lock"
flock -n 9 || { echo "Already running"; exit 0; }
: "${NOET_ADDRESS:?Set NOET_ADDRESS=noet1... in the instance environment}"
SRC=$DIR/noet-source
MINER=$DIR/noet-miner
export DEBIAN_FRONTEND=noninteractive
echo "$(date -u +%FT%TZ) start"
command -v git >/dev/null && command -v python3 >/dev/null \
  || { apt-get update; apt-get install -y --no-install-recommends git ca-certificates python3; }
[ -d "$SRC/.git" ] || git clone --depth 1 "${NOET_REPO:-https://github.com/noetnetwork/noetnetwork}" "$SRC"
if [ ! -f "$MINER/context.json" ]; then
  python3 "$SRC/protocol/bootstrap.py" --engines
  rm -rf "$MINER"
  python3 "$SRC/protocol/miner/prepare.py" --out "$MINER"
fi
cd "$MINER"
if [ "$(id -u)" = 0 ]; then ./setup.sh; fi   # system packages
./setup.sh --build                            # engine for this GPU, VDF binding, Qwen3-0.6B
if [ "${NOET_14B:-1}" != 0 ]; then
  mkdir -p "$DIR/tmp"
  NOET_CONVERT_TMP=$DIR/tmp ./convert.sh . models qwen3-14b
fi
# A failed check says what to fix; try again every five minutes (the gateway may be restarting).
until ./mine.sh "$NOET_ADDRESS" --check; do sleep 300; done
EXTRA=()
if [ -n "${NOET_NOTIFY:-}" ]; then EXTRA+=(--notify "$NOET_NOTIFY"); fi
mine_on() {  # $1: GPU index
  while true; do
    ./mine.sh "$NOET_ADDRESS" --gpu "$1" --out "$DIR/work-gpu$1" --dashboard "127.0.0.1:$((8090 + $1))" \
      "${EXTRA[@]}" || true
    echo "$(date -u +%FT%TZ) miner on GPU $1 exited; starting again in 60 s"
    sleep 60
  done
}
COUNT=1
if [ "${NOET_GPUS:-1}" = all ]; then COUNT=$(nvidia-smi -L | grep -c '^GPU'); fi
for ((gpu = 1; gpu < COUNT; gpu++)); do mine_on "$gpu" & done
mine_on 0
RUN
chmod +x "$DIR/noet-run.sh"
nohup setsid "$DIR/noet-run.sh" >> "$DIR/noet.log" 2>&1 < /dev/null &
