#!/usr/bin/env bash
# One detached research session on the temporary GPU host. Started as root with
# setsid/nohup so a dropped SSH connection cannot interrupt it. Every stage
# writes its exit status; a failed stage is recorded, never retried with a
# different seed or threshold. Evidence lands under output/ for collection.
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

if [[ ! -f .built ]]; then
  bash build.sh >"$O/build.log" 2>&1; code=$?; step build "$code"; [[ $code == 0 ]] || exit 1
  bash build_native.sh >"$O/build-native.log" 2>&1; code=$?; step build-native "$code"; [[ $code == 0 ]] || exit 1
  touch .built
fi
cp pkg/noet/*.py verifier/noet/ && cp pkg/lab.network.json verifier/
cp binaries.sha256 native-binaries.sha256 runtime.json gpu.csv requirements.resolved.txt "$O/" 2>/dev/null || true

# Model snapshot: root-owned and unwritable by the worker (run.sh selects
# NOET_MODEL_PROTECTION=root-owned). Fresh worker-owned output and cache only.
# Uploads keep the operator's UID; every ancestor of the model must be root-owned.
chown root:root /workspace "$R" && chmod 755 /workspace
chown -R root:root model qwen3-0.6b-bf16.gguf qwen3-06b.candidate.json
chmod 755 "$R" model && chmod -R a+rX,go-w model && chmod 644 qwen3-0.6b-bf16.gguf qwen3-06b.candidate.json
chmod -R a+rX checks
install -d -o 10001 -g 10001 -m 700 /tmp/noet-gpu-home "$O/runs"

"$R/runtime/bin/python" "$R/checks/test_response.py" --source "$R/engine/llama" --build "$R/engine/build" >"$O/response-test.log" 2>&1
step response-regression $?

run() { local name=$1; shift; bash run.sh "$@" >"$O/runs/$name.log" 2>&1; local code=$?; step "$name" "$code"; return $code; }
run maturity "$R/checks/native.py" --out "$O/runs/maturity" --blocks 101 --spend --check-indexer --restart \
  --attempts 400 --seconds 64800
run audit "$R/checks/audit.py" --run "$O/runs/maturity" --out "$O/runs/maturity-audit.json" --require-captures
run summary "$R/checks/summarize.py" --run "$O/runs/maturity" --out "$O/runs/maturity-summary.json"
run wallet "$R/checks/wallet_acceptance.py" --source "$O/runs/maturity" --out "$O/runs/wallet"
run peer "$R/checks/peer.py" --source "$O/runs/maturity" --out "$O/runs/peer" --seconds 7200
step finished
