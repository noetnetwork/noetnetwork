#!/usr/bin/env bash
# Rebuild the pinned NOET model file: fetch Qwen/Qwen3-0.6B at the pinned
# revision (every file hash-checked), convert it to bf16 GGUF with the pinned
# engine's converter and pinned packages, then require the pinned SHA-256.
#   ./convert.sh <context dir> <output dir>
# A research host converts another candidate (protocol/models/profiles.json):
#   ./convert.sh <context dir> <output dir> <manifest> <manifest sha256> <gguf name> [<gguf sha256>]
# Without a pinned GGUF digest it records the result's digest instead of checking it.
set -euo pipefail
CONTEXT=$(cd "$1" && pwd)
OUT=$2
NAME=qwen3-0.6b-bf16.gguf
GGUF_SHA256=8fa11f4d815167c4f6e3c91b25aebde25d8346a8f11228e744016592532b0700
FETCH=(python3 "$CONTEXT/app/fetch_model.py")
if [[ $# -ge 5 ]]; then
  NAME=$5
  GGUF_SHA256=${6:-}
  FETCH+=(--manifest "$3" --sha256 "$4" --max-gb 80)
fi
WORK=$(mktemp -d "${NOET_CONVERT_TMP:-/tmp}/noet-convert.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT"
if [[ -n "$GGUF_SHA256" && -f "$OUT/$NAME" ]] && echo "$GGUF_SHA256  $OUT/$NAME" | sha256sum -c --status; then
  echo "Model already present and verified."
  exit 0
fi
python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install --quiet --only-binary=:all: --index-url https://download.pytorch.org/whl/cpu torch==2.8.0
"$WORK/venv/bin/pip" install --quiet --only-binary=:all: numpy==1.26.4 transformers==4.57.1 sentencepiece==0.2.1 \
  protobuf==4.25.8 PyYAML==6.0.2 tqdm==4.67.1
"${FETCH[@]}" --out "$WORK/model"
mkdir "$WORK/engine"
tar -xf "$CONTEXT/engine.tar" -C "$WORK/engine"
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH="$WORK/engine/llama/gguf-py" \
  "$WORK/venv/bin/python" "$WORK/engine/llama/convert_hf_to_gguf.py" "$WORK/model" --outtype bf16 \
  --outfile "$WORK/model-bf16.gguf"
if [[ -n "$GGUF_SHA256" ]]; then
  echo "$GGUF_SHA256  $WORK/model-bf16.gguf" | sha256sum -c
  mv "$WORK/model-bf16.gguf" "$OUT/$NAME"
  echo "Model verified: $OUT/$NAME"
else
  mv "$WORK/model-bf16.gguf" "$OUT/$NAME"
  echo "Model converted, digest not yet pinned: $(sha256sum "$OUT/$NAME")"
fi
# A research host also keeps the verified snapshot for its verifier.
if [[ -n "${NOET_KEEP_SNAPSHOT:-}" ]]; then
  mv "$WORK/model" "$NOET_KEEP_SNAPSHOT"
fi
