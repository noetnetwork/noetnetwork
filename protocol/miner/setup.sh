#!/usr/bin/env bash
# Install the NOET miner natively on Ubuntu 22.04/24.04 with an NVIDIA GPU and
# the CUDA toolkit (12.4 or newer). Run from the prepared miner directory:
#   sudo ./setup.sh            # system packages (once)
#   ./setup.sh --build         # engine, VDF binding and model, as your own user
# Everything is installed under ./build and ./models; nothing else is changed.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
if [[ "${1:-}" != "--build" ]]; then
  [[ $EUID -eq 0 ]] || { echo "Run 'sudo ./setup.sh' for system packages, then './setup.sh --build'."; exit 1; }
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends build-essential cmake ninja-build python3-dev python3-venv \
    pybind11-dev libgmp-dev ca-certificates
  echo "System packages installed. Now run ./setup.sh --build as your normal user."
  exit 0
fi
command -v nvcc >/dev/null || export PATH=/usr/local/cuda/bin:$PATH
command -v nvcc >/dev/null || { echo "CUDA toolkit (nvcc) not found. Install CUDA 12.4+ first."; exit 1; }
nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader
ARCH=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d '.')
sha256sum -c <(python3 -c "import json;m=json.load(open('context.json'))['files'];print(''.join(f'{v}  {k}\n' for k,v in m.items() if k!='context.json'))") >/dev/null
mkdir -p build
if [[ ! -x build/engine/build/bin/llama-server ]]; then
  rm -rf build/engine && mkdir build/engine && tar -xf engine.tar -C build/engine
  cmake -S build/engine/llama -B build/engine/build -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON \
    "-DCMAKE_CUDA_ARCHITECTURES=$ARCH" -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_CURL=OFF \
    -DLLAMA_OPENSSL=OFF -DBUILD_SHARED_LIBS=OFF
  cmake --build build/engine/build --target llama-server -j"$(nproc)"
fi
(cd chiavdf/src && cc -O2 -fPIC -c refcode/lzcnt.c -o "$HERE/build/lzcnt.o" \
  && c++ -O2 -std=c++17 -shared -fPIC $(python3-config --includes) -I/usr/include/pybind11 -I. \
     python_bindings/fastvdf.cpp "$HERE/build/lzcnt.o" -lgmp -lgmpxx -pthread \
     -o "$HERE/build/chiavdf$(python3-config --extension-suffix)")
./convert.sh "$HERE" "$HERE/models"
cat > mine.sh <<EOF
#!/usr/bin/env bash
# Start mining: ./mine.sh noet1yourwalletaddress
exec env PYTHONPATH="$HERE/build" python3 "$HERE/app/noet_miner.py" --engine "$HERE/build/engine/build/bin/llama-server" \\
  --model "$HERE/models/qwen3-0.6b-bf16.gguf" --out "$HERE/work" --address "\$@"
EOF
chmod +x mine.sh
echo "Ready. Start mining with: ./mine.sh <your noet1 address>"
