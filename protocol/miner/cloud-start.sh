#!/bin/bash
# Start script for mining on a rented GPU container (Vast.ai, Runpod and similar):
# paste it as the on-start script, or run it once by hand. It builds whatever is
# missing, then mines, and starts the miner again if it exits. Each container
# start runs it again; finished steps are skipped.
#
#   Image   nvidia/cuda:12.8.1-devel-ubuntu24.04 (any image with the CUDA 12.4+
#           toolkit, git and Python 3.10+ works), one GPU, driver 570 or newer
#   Disk    150 GB; 50 GB with NOET_14B=0
#   Env     NOET_ADDRESS=noet1...  your reward address (required)
#           NOET_14B=0             24 GB cards: Qwen3-0.6B only, which mines
#                                  until block 3,500
#           NOET_DIR=/root         where the source, build and models go
#           NOET_REPO=<git URL>    source to build (default: the public repository)
#
# Progress: tail -f $NOET_DIR/noet.log
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
while true; do
  ./mine.sh "$NOET_ADDRESS" || true
  echo "$(date -u +%FT%TZ) miner exited; starting again in 60 s"
  sleep 60
done
RUN
chmod +x "$DIR/noet-run.sh"
nohup setsid "$DIR/noet-run.sh" >> "$DIR/noet.log" 2>&1 < /dev/null &
