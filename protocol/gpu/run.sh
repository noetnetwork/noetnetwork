#!/usr/bin/env bash
# Research execution only; deliberately no production calibration approval.
set -euo pipefail
R=/workspace/noet-gpu
MANIFEST=$R/qwen3-06b.candidate.json
MANIFEST_SHA256=c18e242c1f4c2273d89a27a226856ad1a6623a7a0c22839495b45371eefa3ae4
MODEL_ID=Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca
# A research host testing another candidate model has model.json (gpu/stage.py).
if [[ -f $R/model.json ]]; then
  read -r MANIFEST MANIFEST_SHA256 MODEL_ID < <("$R/runtime/bin/python" -c '
import json, re, sys
m = json.load(open(sys.argv[1]))
assert re.fullmatch(r"[a-z0-9.-]+[.]candidate[.]json", m["manifestFile"]) and re.fullmatch(r"[a-f0-9]{64}", m["manifestSha256"])
assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*@[a-f0-9]{40}", m["id"])
print(sys.argv[2] + "/" + m["manifestFile"], m["manifestSha256"], m["id"])' "$R/model.json" "$R")
fi
# model_session.sh may give the producer and verifier separate GPUs (plan_host.py).
VERIFIER_GPU=${NOET_VERIFIER_GPU:-0}
PRODUCER_GPU=${NOET_PRODUCER_GPU:-0}
[[ $VERIFIER_GPU =~ ^[0-9]$ && $PRODUCER_GPU =~ ^[0-9]$ ]] || { echo "GPU index must be a single digit" >&2; exit 2; }
cd /workspace/noet-gpu/verifier/services/verification-api/src
exec setpriv --reuid=10001 --regid=10001 --clear-groups --bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs \
 env -i PATH=/workspace/noet-gpu/runtime/bin:/usr/local/cuda/bin:/usr/bin:/bin \
 HOME=/tmp/noet-gpu-home PYTHONPATH=/workspace/noet-gpu/schemas:/workspace/noet-gpu/native:/workspace/noet-gpu/verifier:/workspace/noet-gpu/verifier/services/verification-api/src:/workspace/noet-gpu/checks \
 LD_LIBRARY_PATH=/usr/local/cuda/lib64:/usr/local/nvidia/lib:/usr/local/nvidia/lib64 \
 CUDA_VISIBLE_DEVICES=$VERIFIER_GPU NOET_PRODUCER_GPU=$PRODUCER_GPU HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 \
 HF_HOME=/tmp/noet-gpu-home/hf MPLCONFIGDIR=/tmp/noet-gpu-home/mpl \
 NOET_MODEL_ROOT=/workspace/noet-gpu/model NOET_MODEL_MANIFEST="$MANIFEST" \
 NOET_MODEL_MANIFEST_SHA256="$MANIFEST_SHA256" NOET_MODEL_ID="$MODEL_ID" \
 NOET_MODEL_PROTECTION=root-owned POW_V4_STRICT_MODE=enforce POW_V2_STREAMED=1 POW_ALLOW_CPU_INFERENCE=0 \
 POW_PROMPT_GIBBERISH_GATE=shadow ${VALIDATOR_SHARED_SECRET:+VALIDATOR_SHARED_SECRET=$VALIDATOR_SHARED_SECRET} \
 POI_DETECTOR_BUNDLE=/workspace/noet-gpu/verifier/services/verification-api/src/poi_detector/assets/detector-amber-red-v2/bundle.json \
 POI_DETECTOR_SHADOW_DIR=/tmp/noet-gpu-home/detector \
 /workspace/noet-gpu/runtime/bin/python "$@"
