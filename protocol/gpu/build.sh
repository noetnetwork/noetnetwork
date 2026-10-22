#!/usr/bin/env bash
# Bounded research build on the owner's temporary CUDA host. No service exposure.
set -euo pipefail
umask 022
cd /workspace/noet-gpu
sha256sum -c inputs.sha256
# Live and mainnet hosts build for the A40 architecture (8.6). A candidate-model
# test on a larger card sets NOET_CUDA_ARCH to that card's (model_session.sh).
export NOET_CUDA_ARCH=${NOET_CUDA_ARCH:-86}
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends build-essential cmake ninja-build pkg-config flatbuffers-compiler libflatbuffers-dev libargon2-dev libzmq3-dev cppzmq-dev libssl-dev libgmp-dev python3-dev python3-venv pybind11-dev libboost-dev
python3 - <<'PY'
import tarfile
from pathlib import Path
for name in ('engine','verifier'):
    root=Path(name);root.mkdir(exist_ok=False)
    with tarfile.open(name+'.tar') as archive: archive.extractall(root,filter='data')
PY
python3 -m venv --system-site-packages runtime
export PATH="/workspace/noet-gpu/runtime/bin:/usr/local/cuda/bin:$PATH"
export CUDACXX=/usr/local/cuda/bin/nvcc
python3 -m pip install --only-binary=:all: -r verifier/services/verification-api/requirements.txt
python3 -m pip check
python3 -m pip freeze > requirements.resolved.txt
python3 - <<'PY'
import json,os,platform,torch
from pathlib import Path
if not torch.cuda.is_available():raise RuntimeError('CUDA unavailable')
if '%d%d'%torch.cuda.get_device_capability()!=os.environ['NOET_CUDA_ARCH']:raise RuntimeError('GPU architecture differs from NOET_CUDA_ARCH')
Path('runtime.json').write_text(json.dumps({'machine':platform.machine(),'python':platform.python_version(),
    'torch':torch.__version__,'cuda':torch.version.cuda,'gpu':torch.cuda.get_device_name(),
    'productionReady':False},indent=2)+'\n')
PY
cmake -S engine/llama -B engine/build -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=$NOET_CUDA_ARCH -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_CURL=OFF -DLLAMA_OPENSSL=OFF -DBUILD_SHARED_LIBS=OFF
cmake --build engine/build --target llama-server -j8
mkdir -p native schemas
cd verifier/shared-utils/fb-schemas
flatc --python --cpp validation.fbs blockheader.fbs proof.fbs
flatc --python --gen-object-api -o /workspace/noet-gpu/schemas validation.fbs blockheader.fbs proof.fbs
c++ -O2 -std=c++17 -shared -fPIC $(python3-config --includes) -I/usr/include/pybind11 -I. ../pow-utils/pfunpack/pfunpack.cpp -o /workspace/noet-gpu/native/pfunpack.so
cd ../chiavdf/src
cc -O2 -fPIC -c refcode/lzcnt.c -o /workspace/noet-gpu/native/lzcnt.o
c++ -O2 -std=c++17 -shared -fPIC $(python3-config --includes) -I/usr/include/pybind11 -I. python_bindings/fastvdf.cpp /workspace/noet-gpu/native/lzcnt.o -lgmp -lgmpxx -pthread -o /workspace/noet-gpu/native/chiavdf.so
cd /workspace/noet-gpu
bash verifier/shared-utils/pow-utils/build_credit_v4_lib.sh native
cp verifier/shared-utils/pow-utils/*.py verifier/services/verification-api/src/
cp verifier/shared-utils/pow-utils/*.py verifier/services/verification-api/src/utils/
cp -R verifier/shared-utils/fb-schemas/proof verifier/services/verification-api/src/
cp native/libtc_credit_v4.so verifier/services/verification-api/src/utils/
python3 checks/fix_object_bindings.py --bindings schemas
chmod -R a+rX engine verifier native schemas runtime checks
sha256sum engine/build/bin/llama-server native/*.so > binaries.sha256
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv > gpu.csv
printf 'NOET_GPU_BUILD_COMPLETE\n'
