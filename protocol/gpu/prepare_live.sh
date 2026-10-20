#!/usr/bin/env bash
# Prepare a GPU host to serve a hosted network with live.py: the build and file
# setup of session.sh, without its research runs. Started as root with
# setsid/nohup so a dropped SSH connection cannot interrupt it. Every stage
# writes its exit status to output/steps.jsonl; the last step is "ready".
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

if [[ ! -f .built ]]; then
  bash build.sh >"$O/build.log" 2>&1; code=$?; step build "$code"; [[ $code == 0 ]] || exit 1
  bash build_native.sh >"$O/build-native.log" 2>&1; code=$?; step build-native "$code"; [[ $code == 0 ]] || exit 1
  touch .built
fi
cp pkg/noet/*.py verifier/noet/ && cp pkg/lab.network.json verifier/

# Model snapshot: root-owned and unwritable by the worker, as in session.sh.
chown root:root /workspace "$R" && chmod 755 /workspace
chown -R root:root model qwen3-0.6b-bf16.gguf qwen3-06b.candidate.json
chmod 755 "$R" model && chmod -R a+rX,go-w model && chmod 644 qwen3-0.6b-bf16.gguf qwen3-06b.candidate.json
chmod -R a+rX checks relay
install -d -o 10001 -g 10001 -m 700 /tmp/noet-gpu-home

"$R/runtime/bin/python" "$R/checks/test_response.py" --source "$R/engine/llama" --build "$R/engine/build" >"$O/response-test.log" 2>&1
code=$?; step response-regression "$code"; [[ $code == 0 ]] || exit 1
step ready
