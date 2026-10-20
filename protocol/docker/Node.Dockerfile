# Development build only. Base image is pinned; OS packages still need a release lock.
# Context: source_bundle.py export, after bootstrap.py prepares reviewed sources.
FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55 AS build
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake ninja-build git pkg-config ca-certificates autoconf automake libtool python3 libevent-dev libssl-dev libzmq3-dev libsqlite3-dev libgmp-dev libargon2-dev libboost-dev libzstd-dev libflint-dev libsodium-dev flatbuffers-compiler libflatbuffers-dev && rm -rf /var/lib/apt/lists/*
COPY shared-utils/secp256k1-zkp /src/secp256k1-zkp
WORKDIR /src/secp256k1-zkp
RUN ./autogen.sh && ./configure --disable-shared --enable-experimental --enable-module-ecdh --enable-module-extrakeys --enable-module-schnorrsig --enable-module-musig --enable-module-ellswift --enable-module-ecdsa-adaptor --enable-module-recovery && make -j4 && make install
COPY shared-utils/liboqs /src/liboqs
RUN cmake -S /src/liboqs -B /src/liboqs/build -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DOQS_USE_OPENSSL=OFF -DOQS_BUILD_ONLY_LIB=ON '-DOQS_MINIMAL_BUILD=SIG_ml_dsa_44;SIG_ml_dsa_65;SIG_ml_dsa_87' && cmake --build /src/liboqs/build -j4 && cmake --install /src/liboqs/build
RUN apt-get update && apt-get install -y --no-install-recommends libboost-locale-dev libboost-thread-dev libboost-test-dev && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch v0.3.11 https://github.com/supranational/blst.git /src/blst && test "$(git -C /src/blst rev-parse HEAD)" = "3dd0f804b1819e5d03fb22ca2e6fac105932043a" && cd /src/blst && ./build.sh -O3 && cp libblst.a /usr/local/lib/ && mkdir -p /usr/local/bindings && cp bindings/*.h /usr/local/bindings/
COPY services/core-node/bcore /src/bcore
COPY shared-utils/chiavdf /src/bcore/src/external/chiavdf
COPY shared-utils/fb-schemas /src/schemas
RUN flatc --cpp -o /src/bcore/src/rpc /src/schemas/proof.fbs /src/schemas/blockheader.fbs /src/schemas/validation.fbs
WORKDIR /src/bcore
RUN apt-get update && apt-get install -y --no-install-recommends cppzmq-dev libcurl4-openssl-dev nlohmann-json3-dev && rm -rf /var/lib/apt/lists/*
RUN cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_GUI=OFF -DWITH_ZMQ=ON -DENABLE_WALLET=ON -DBUILD_TESTS=ON -DENABLE_IPC=OFF && cmake --build build --target bitcoind bitcoin-cli bitcoin-wallet test_bitcoin -j3

FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55
RUN apt-get update && apt-get install -y --no-install-recommends libevent-2.1-7t64 libevent-extra-2.1-7t64 libevent-pthreads-2.1-7t64 libssl3t64 libzmq5 libsqlite3-0 libgmp10 libgmpxx4ldbl libargon2-1 libzstd1 libflint18 libsodium23 libboost-locale1.83.0 libboost-thread1.83.0 libcurl4t64 ca-certificates python3 && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home noet
COPY --from=build /src/bcore/build/bin/bitcoind /src/bcore/build/bin/bitcoin-cli /src/bcore/build/bin/bitcoin-wallet /src/bcore/build/bin/test_bitcoin /usr/local/bin/
COPY --from=build /src/bcore/build/bin/mock_cosign_bridge /home/noet/bin/mock_cosign_bridge
USER noet
WORKDIR /home/noet
# No upstream supervisor, automatic miner, public verifier, VNC or seed bootstrap.
ENTRYPOINT ["bitcoind", "-chain=tensor-reg", "-dnsseed=0", "-fixedseeds=0", "-listenonion=0", "-discover=0", "-printtoconsole=1"]
