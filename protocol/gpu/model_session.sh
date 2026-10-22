#!/usr/bin/env bash
# Test another candidate model (protocol/models/profiles.json) on a temporary GPU
# host: fetch and convert it twice, then mine a private chain with real
# verification. Started as root with setsid/nohup, like session.sh. Every stage
# writes its exit status; nothing is retried with another seed or threshold.
# plan_host.py first checks GPU memory and disk; with two large enough GPUs the
# producer runs on GPU 0 and the verifier on GPU 1. A switch rehearsal (model.json
# "switch", design/MODEL-SWITCH.md) fetches and converts the second model too.
#   NOET_BLOCKS (default 30), NOET_SECONDS (default 28800), NOET_CONVERT_TWICE (default 1),
#   NOET_RELABEL (switch rehearsal: forged attempts after the switch, native.py --relabel-after-switch)
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
read -r GGUF MANIFEST SHA PIN GGUF2 MANIFEST2 SHA2 PIN2 < <(python3 -c '
import json; m = json.load(open("model.json")); s = m.get("switch") or {}
print(m["gguf"], m["manifestFile"], m["manifestSha256"], m.get("ggufSha256") or "-",
      s.get("gguf", "-"), s.get("manifestFile", "-"), s.get("manifestSha256", "-"), s.get("ggufSha256") or "-")')

# Before building or downloading anything: stop at once on a host that is too small
# (for a switch rehearsal, for the larger model).
PLAN_MANIFEST=$MANIFEST; [[ $GGUF2 != - ]] && PLAN_MANIFEST=$MANIFEST2
converted=; [[ -f "$GGUF" && ( $GGUF2 == - || -f "$GGUF2" ) ]] && converted=--converted
plan=$(python3 plan_host.py --manifest "$PLAN_MANIFEST" --disk /workspace ${converted:+"$converted"} --out "$O/host-plan.json")
code=$?; step preflight "$code"; [[ $code == 0 ]] || exit 1
read -r NOET_PRODUCER_GPU NOET_VERIFIER_GPU <<<"$plan"
export NOET_PRODUCER_GPU NOET_VERIFIER_GPU

if [[ ! -f .built ]]; then
  # Build for this host's GPU (an 80 GB or larger card for 14B and 32B), not the
  # A40 architecture the live hosts use. Both GPUs of a two-GPU host must match.
  NOET_CUDA_ARCH=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | tr -d . | sort -u)
  [[ $NOET_CUDA_ARCH =~ ^[0-9]+$ ]] || { echo "Mixed or unknown GPU architectures: $NOET_CUDA_ARCH"; step build 1; exit 1; }
  export NOET_CUDA_ARCH
  bash build.sh >"$O/build.log" 2>&1; code=$?; step build "$code"; [[ $code == 0 ]] || exit 1
  bash build_native.sh >"$O/build-native.log" 2>&1; code=$?; step build-native "$code"; [[ $code == 0 ]] || exit 1
  touch .built
fi
cp pkg/noet/*.py verifier/noet/ && cp pkg/lab.network.json verifier/
cp binaries.sha256 native-binaries.sha256 runtime.json gpu.csv requirements.resolved.txt model.json "$O/" 2>/dev/null || true

# The verifier needs the snapshot and the miner its GGUF. A pinned GGUF digest
# (models/profiles.json) must come out again here; an unpinned one needs a second,
# independent conversion with the same bytes before it can be pinned.
convert_model() {  # label gguf manifest manifest-sha pin snapshot-dir
  local label=$1 gguf=$2 manifest=$3 sha=$4 pin=$5 keep=$6 first second code
  [[ -f "$gguf" ]] && return 0
  mkdir -p /workspace/tmp
  NOET_CONVERT_TMP=/workspace/tmp NOET_KEEP_SNAPSHOT=$keep \
    bash convert.sh "$R" "$R/convert-1" "$R/$manifest" "$sha" "$gguf" >"$O/convert-$label-1.log" 2>&1
  code=$?; step "convert-$label" "$code"; [[ $code == 0 ]] || return 1
  first=$(sha256sum "convert-1/$gguf" | cut -d' ' -f1)
  second=
  if [[ $pin != - ]]; then
    [[ "$first" == "$pin" ]]; code=$?; step "conversion-pinned-$label" "$code"; [[ $code == 0 ]] || return 1
  elif [[ ${NOET_CONVERT_TWICE:-1} == 1 ]]; then
    NOET_CONVERT_TMP=/workspace/tmp bash convert.sh "$R" "$R/convert-2" "$R/$manifest" "$sha" "$gguf" >"$O/convert-$label-2.log" 2>&1
    code=$?; step "convert-repeat-$label" "$code"
    [[ $code == 0 ]] && second=$(sha256sum "convert-2/$gguf" | cut -d' ' -f1)
    rm -rf convert-2
    [[ "$first" == "$second" ]]; step "conversion-reproducible-$label" $?
  fi
  printf '{"gguf":"%s","pinned":"%s","first":"%s","second":"%s"}\n' "$gguf" "$pin" "$first" "$second" >"$O/conversion-$label.json"
  mv "convert-1/$gguf" "$gguf" && rmdir convert-1
}
convert_model model "$GGUF" "$MANIFEST" "$SHA" "$PIN" "$R/model" || exit 1
if [[ $GGUF2 != - ]]; then
  convert_model switch "$GGUF2" "$MANIFEST2" "$SHA2" "$PIN2" "$R/model-2" || exit 1
fi

# Same protections as session.sh: the model tree is root-owned and unwritable by the worker.
chown root:root /workspace "$R" && chmod 755 /workspace
chown -R root:root model "$GGUF" "$MANIFEST" model.json
chmod 755 "$R" model && chmod -R a+rX,go-w model && chmod 644 "$GGUF" "$MANIFEST" model.json
if [[ $GGUF2 != - ]]; then
  chown -R root:root model-2 "$GGUF2" "$MANIFEST2"
  chmod 755 model-2 && chmod -R a+rX,go-w model-2 && chmod 644 "$GGUF2" "$MANIFEST2"
fi
chmod -R a+rX checks
install -d -o 10001 -g 10001 -m 700 /tmp/noet-gpu-home "$O/runs"

nvidia-smi --query-gpu=timestamp,index,memory.used,memory.total,utilization.gpu --format=csv -l 5 >"$O/gpu-memory.csv" 2>&1 &
sampler=$!
trap 'kill $sampler 2>/dev/null' EXIT

run() { local name=$1; shift; bash run.sh "$@" >"$O/runs/$name.log" 2>&1; local code=$?; step "$name" "$code"; return $code; }
run model "$R/checks/native.py" --out "$O/runs/model" --blocks "${NOET_BLOCKS:-30}" --continue-after-rejection \
  --attempts 400 --seconds "${NOET_SECONDS:-28800}" ${NOET_RELABEL:+--relabel-after-switch "$NOET_RELABEL"}
run audit "$R/checks/audit.py" --run "$O/runs/model" --out "$O/runs/model-audit.json" --require-captures
run summary "$R/checks/summarize.py" --run "$O/runs/model" --out "$O/runs/model-summary.json"
step finished
