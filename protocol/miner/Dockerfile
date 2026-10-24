# NOET miner image. Build from the context made by protocol/miner/prepare.py:
#   docker build -t noet-miner .
#   docker run --gpus all -v noet-mining:/work noet-miner --address tnoet1...
# An image built without WITH_14B builds Qwen3-14B into the /work volume on its
# first start when run with -e NOET_14B=1 (start.sh).
#
# Every base image is pinned by digest. The model is rebuilt from the pinned
# Hugging Face revision with the engine's own converter and must hash to the
# pinned GGUF; the build fails otherwise. No weights are redistributed here.

# 1. Model: fetch the pinned snapshot and convert it to the pinned GGUF. With
# --build-arg WITH_14B=1 also Qwen3-14B (design/MODEL-SWITCH.md): a 29.6 GB file,
# so the image can mine across the switch.
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS model
ARG WITH_14B=0
WORKDIR /build
COPY engine.tar convert.sh ./
COPY app/fetch_model.py app/qwen3-06b.candidate.json app/qwen3-14b.candidate.json app/
COPY app/noet/ app/noet/
RUN ./convert.sh /build /models && if [ "$WITH_14B" = 1 ]; then ./convert.sh /build /models qwen3-14b; fi

# 2. Engine and VDF binding, compiled for current NVIDIA architectures.
FROM nvidia/cuda:12.8.1-devel-ubuntu24.04@sha256:520292dbb4f755fd360766059e62956e9379485d9e073bbd2f6e3c20c270ed66 AS build
ARG CUDA_ARCHITECTURES="80;86;89;90;120"
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake ninja-build pkg-config python3-dev pybind11-dev libgmp-dev \
      flatbuffers-compiler libflatbuffers-dev libssl-dev libzmq3-dev cppzmq-dev libargon2-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY engine.tar ./
COPY chiavdf/ chiavdf/
RUN mkdir engine && tar -xf engine.tar -C engine \
 && cmake -S engine/llama -B engine/build -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON \
      "-DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCHITECTURES}" -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
      -DLLAMA_CURL=OFF -DLLAMA_OPENSSL=OFF -DBUILD_SHARED_LIBS=OFF \
 && cmake --build engine/build --target llama-server -j"$(nproc)"
RUN cd chiavdf/src \
 && cc -O2 -fPIC -c refcode/lzcnt.c -o /build/lzcnt.o \
 && c++ -O2 -std=c++17 -shared -fPIC $(python3-config --includes) -I/usr/include/pybind11 -I. \
      python_bindings/fastvdf.cpp /build/lzcnt.o -lgmp -lgmpxx -pthread \
      -o /build/chiavdf$(python3-config --extension-suffix)

# 3. Runtime: CUDA runtime, Python, the two binaries, the model and the miner.
FROM nvidia/cuda:12.8.1-runtime-ubuntu24.04@sha256:ebef3c171eeef0298e4eb2e4be843105edf3b8b0ac45e0b43acee358e8046867
ENV DEBIAN_FRONTEND=noninteractive
# libssl3t64, libzmq5 and libargon2-1: the engine's proof server links them (ldd on
# a native build, 24 Oct 2026); FlatBuffers is linked statically.
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv libgmp10 libgmpxx4ldbl libgomp1 ca-certificates \
      libssl3t64 libzmq5 libargon2-1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 miner && mkdir /work && chown miner /work
COPY --from=build /build/engine/build/bin/llama-server /opt/noet/bin/llama-server
COPY --from=build /build/chiavdf*.so /opt/noet/lib/
COPY --from=model /models/ /models/
COPY app/ /opt/noet/app/
COPY engine.tar convert.sh start.sh /opt/noet/
COPY README.md LICENSE-NOTICE.md /opt/noet/
COPY licenses/ /opt/noet/licenses/
ENV PYTHONPATH=/opt/noet/lib PYTHONDONTWRITEBYTECODE=1 NOET_ENGINE=/opt/noet/bin/llama-server \
    NOET_MODEL_DIR=/models:/work/models NOET_WORK_DIR=/work
USER miner
WORKDIR /work
VOLUME /work
ENTRYPOINT ["/opt/noet/start.sh"]
