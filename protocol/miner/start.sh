#!/bin/sh
# Entry point of the miner image. With NOET_14B=1 it first builds the Qwen3-14B
# model file into /work/models (once: a 31 GB download and about 90 GB of free
# disk on the /work volume while it converts), so an image without the 14B file
# mines across the switch at block 3,500. Then it runs the miner.
set -e
case " $* " in *" --check "*|*" --help "*|*" -h "*) ;; *)
  if [ "${NOET_14B:-0}" = 1 ] && [ ! -f /models/qwen3-14b-bf16.gguf ] && [ ! -f /work/models/qwen3-14b-bf16.gguf ]; then
    mkdir -p /work/models /work/tmp
    NOET_CONVERT_TMP=/work/tmp /opt/noet/convert.sh /opt/noet /work/models qwen3-14b
  fi ;;
esac
exec python3 /opt/noet/app/noet_miner.py "$@"
