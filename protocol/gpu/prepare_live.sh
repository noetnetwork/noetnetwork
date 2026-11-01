#!/usr/bin/env bash
# Prepare a GPU host to serve a hosted network with live.py: the build and file
# setup of session.sh, without its research runs. Started as root with
# setsid/nohup so a dropped SSH connection cannot interrupt it. Every stage
# writes its exit status to output/steps.jsonl; the last step is "ready".
#
# A host staged with model.json (gpu/stage.py --switch-profile) is a verifier
# for both sides of a model switch, such as an 80 GB GPU for Qwen3-14B from
# block 3,500 (design/MODEL-SWITCH.md): it checks it can hold the larger
# verifier, builds for its own GPU, and fetches and converts both pinned
# models here (the snapshots go to model/ and model-2/, run.sh hands both to
# the verifier). Such a host runs with live.json "mine": false.
set -uo pipefail
umask 022
R=/workspace/noet-gpu
O=$R/output
mkdir -p "$O"
chmod 755 "$O"
exec >>"$O/prepare-live.log" 2>&1
step() { printf '{"step":"%s","exit":%s,"at":"%s"}\n' "$1" "${2:-0}" "$(date -u +%FT%TZ)" >>"$O/steps.jsonl"; }
cd "$R"
step start

GGUF=qwen3-0.6b-bf16.gguf MANIFEST=qwen3-06b.candidate.json GGUF2=- MANIFEST2=-
if [[ -f model.json ]]; then
  read -r GGUF MANIFEST SHA PIN GGUF2 MANIFEST2 SHA2 PIN2 < <(python3 -c '
import json; m = json.load(open("model.json")); s = m.get("switch") or {}
print(m["gguf"], m["manifestFile"], m["manifestSha256"], m["ggufSha256"],
      s.get("gguf", "-"), s.get("manifestFile", "-"), s.get("manifestSha256", "-"), s.get("ggufSha256", "-"))')
  # Live hosts serve pinned models only: every conversion must match its pin.
  [[ $PIN =~ ^[0-9a-f]{64}$ && ( $GGUF2 == - || $PIN2 =~ ^[0-9a-f]{64}$ ) ]] \
    || { echo "model.json needs pinned GGUF digests on a live host"; step model-profile 1; exit 1; }
  PLAN_MANIFEST=$MANIFEST; [[ $GGUF2 != - ]] && PLAN_MANIFEST=$MANIFEST2
  converted=; [[ -f "$GGUF" && ( $GGUF2 == - || -f "$GGUF2" ) ]] && converted=--converted
  python3 plan_host.py --manifest "$PLAN_MANIFEST" --disk /workspace --verifier-only ${converted:+"$converted"} \
    --out "$O/host-plan.json"
  code=$?; step preflight "$code"; [[ $code == 0 ]] || exit 1
  # Build for this host's GPU; build.sh refuses a mismatch.
  NOET_CUDA_ARCH=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | tr -d . | sort -u)
  [[ $NOET_CUDA_ARCH =~ ^[0-9]+$ ]] || { echo "Mixed or unknown GPU architectures: $NOET_CUDA_ARCH"; step build 1; exit 1; }
  export NOET_CUDA_ARCH
fi

if [[ ! -f .built ]]; then
  bash build.sh >"$O/build.log" 2>&1; code=$?; step build "$code"; [[ $code == 0 ]] || exit 1
  bash build_native.sh >"$O/build-native.log" 2>&1; code=$?; step build-native "$code"; [[ $code == 0 ]] || exit 1
  touch .built
fi
cp pkg/noet/*.py verifier/noet/ && cp pkg/lab.network.json verifier/

# Fetch and convert one pinned model, keeping the hash-checked snapshot for the verifier.
convert_model() {  # label gguf manifest manifest-sha pin snapshot-dir
  local label=$1 gguf=$2 manifest=$3 sha=$4 pin=$5 keep=$6 code
  [[ -f "$gguf" && -d "$keep" ]] && return 0
  rm -rf convert-1 "$keep" && mkdir -p /workspace/tmp
  NOET_CONVERT_TMP=/workspace/tmp NOET_KEEP_SNAPSHOT=$keep \
    bash convert.sh "$R" "$R/convert-1" "$R/$manifest" "$sha" "$gguf" "$pin" >"$O/convert-$label.log" 2>&1
  code=$?; step "convert-$label" "$code"; [[ $code == 0 ]] || return 1
  [[ $(sha256sum "convert-1/$gguf" | cut -d' ' -f1) == "$pin" ]]; code=$?
  step "conversion-pinned-$label" "$code"; [[ $code == 0 ]] || return 1
  mv "convert-1/$gguf" "$gguf" && rmdir convert-1
}
if [[ -f model.json ]]; then
  convert_model model "$GGUF" "$MANIFEST" "$SHA" "$PIN" "$R/model" || exit 1
  if [[ $GGUF2 != - ]]; then
    convert_model switch "$GGUF2" "$MANIFEST2" "$SHA2" "$PIN2" "$R/model-2" || exit 1
  fi
fi

# Model snapshots: root-owned and unwritable by the worker, as in session.sh.
chown root:root /workspace "$R" && chmod 755 /workspace
chown -R root:root model "$GGUF" "$MANIFEST"
chmod 755 "$R" model && chmod -R a+rX,go-w model && chmod 644 "$GGUF" "$MANIFEST"
if [[ -f model.json ]]; then
  chown root:root model.json && chmod 644 model.json
fi
if [[ $GGUF2 != - ]]; then
  chown -R root:root model-2 "$GGUF2" "$MANIFEST2"
  chmod 755 model-2 && chmod -R a+rX,go-w model-2 && chmod 644 "$GGUF2" "$MANIFEST2"
fi
chmod -R a+rX checks relay
install -d -o 10001 -g 10001 -m 700 /tmp/noet-gpu-home

"$R/runtime/bin/python" "$R/checks/test_response.py" --source "$R/engine/llama" --build "$R/engine/build" >"$O/response-test.log" 2>&1
code=$?; step response-regression "$code"; [[ $code == 0 ]] || exit 1
step ready
