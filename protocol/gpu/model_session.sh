#!/usr/bin/env bash
# Test another candidate model (protocol/models/profiles.json) on a temporary GPU
# host: fetch and convert it twice, then mine a private chain with real
# verification. Started as root with setsid/nohup, like session.sh. Every stage
# writes its exit status; nothing is retried with another seed or threshold.
# plan_host.py first checks GPU memory and disk; with two large enough GPUs the
# producer runs on GPU 0 and the verifier on GPU 1.
#   NOET_BLOCKS (default 30), NOET_SECONDS (default 28800), NOET_CONVERT_TWICE (default 1)
set -uo pipefail
umask 022
R=/workspace/noet-gpu
O=$R/output
mkdir -p "$O"
chmod 755 "$O"
exec >>"$O/session.log" 2>&1
step() { printf '{"step":"%s","exit":%s,"at":"%s"}\n' "$1" "${2:-0}" "$(date -u +%FT%TZ)" >>"$O/steps.jsonl"; }
cd "$R"
step start
[[ -f model.json ]] || { echo "model.json missing: stage with gpu/stage.py --profile"; step model-profile 1; exit 1; }
read -r GGUF MANIFEST SHA < <(python3 -c 'import json; m = json.load(open("model.json")); print(m["gguf"], m["manifestFile"], m["manifestSha256"])')

# Before building or downloading anything: stop at once on a host that is too small.
converted=; [[ -f "$GGUF" ]] && converted=--converted
plan=$(python3 plan_host.py --manifest "$MANIFEST" --disk /workspace ${converted:+"$converted"} --out "$O/host-plan.json")
code=$?; step preflight "$code"; [[ $code == 0 ]] || exit 1
read -r NOET_PRODUCER_GPU NOET_VERIFIER_GPU <<<"$plan"
export NOET_PRODUCER_GPU NOET_VERIFIER_GPU

if [[ ! -f .built ]]; then
  bash build.sh >"$O/build.log" 2>&1; code=$?; step build "$code"; [[ $code == 0 ]] || exit 1
  bash build_native.sh >"$O/build-native.log" 2>&1; code=$?; step build-native "$code"; [[ $code == 0 ]] || exit 1
  touch .built
fi
cp pkg/noet/*.py verifier/noet/ && cp pkg/lab.network.json verifier/
cp binaries.sha256 native-binaries.sha256 runtime.json gpu.csv requirements.resolved.txt model.json "$O/" 2>/dev/null || true

if [[ ! -f "$GGUF" ]]; then
  # The verifier needs the snapshot and the miner its GGUF. A second, independent
  # conversion must give the same bytes before the GGUF digest can be pinned.
  mkdir -p /workspace/tmp
  NOET_CONVERT_TMP=/workspace/tmp NOET_KEEP_SNAPSHOT=$R/model \
    bash convert.sh "$R" "$R/convert-1" "$R/$MANIFEST" "$SHA" "$GGUF" >"$O/convert-1.log" 2>&1
  code=$?; step convert "$code"; [[ $code == 0 ]] || exit 1
  first=$(sha256sum "convert-1/$GGUF" | cut -d' ' -f1)
  second=
  if [[ ${NOET_CONVERT_TWICE:-1} == 1 ]]; then
    NOET_CONVERT_TMP=/workspace/tmp bash convert.sh "$R" "$R/convert-2" "$R/$MANIFEST" "$SHA" "$GGUF" >"$O/convert-2.log" 2>&1
    code=$?; step convert-repeat "$code"
    [[ $code == 0 ]] && second=$(sha256sum "convert-2/$GGUF" | cut -d' ' -f1)
    rm -rf convert-2
    [[ "$first" == "$second" ]]; step conversion-reproducible $?
  fi
  printf '{"gguf":"%s","first":"%s","second":"%s"}\n' "$GGUF" "$first" "$second" >"$O/conversion.json"
  mv "convert-1/$GGUF" "$GGUF" && rmdir convert-1
fi

# Same protections as session.sh: the model tree is root-owned and unwritable by the worker.
chown root:root /workspace "$R" && chmod 755 /workspace
chown -R root:root model "$GGUF" "$MANIFEST" model.json
chmod 755 "$R" model && chmod -R a+rX,go-w model && chmod 644 "$GGUF" "$MANIFEST" model.json
chmod -R a+rX checks
install -d -o 10001 -g 10001 -m 700 /tmp/noet-gpu-home "$O/runs"

nvidia-smi --query-gpu=timestamp,index,memory.used,memory.total,utilization.gpu --format=csv -l 5 >"$O/gpu-memory.csv" 2>&1 &
sampler=$!
trap 'kill $sampler 2>/dev/null' EXIT

run() { local name=$1; shift; bash run.sh "$@" >"$O/runs/$name.log" 2>&1; local code=$?; step "$name" "$code"; return $code; }
run model "$R/checks/native.py" --out "$O/runs/model" --blocks "${NOET_BLOCKS:-30}" --continue-after-rejection \
  --attempts 400 --seconds "${NOET_SECONDS:-28800}"
run audit "$R/checks/audit.py" --run "$O/runs/model" --out "$O/runs/model-audit.json" --require-captures
run summary "$R/checks/summarize.py" --run "$O/runs/model" --out "$O/runs/model-summary.json"
step finished
