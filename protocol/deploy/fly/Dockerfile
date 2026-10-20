# NOET private-work node for a hosted development network. Not a release build.
# Context: protocol/deploy/fly/prepare.py (reviewed source export + private-work overlay).
FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55 AS build
ARG UBUNTU_SNAPSHOT=20261012T000000Z
# Pin Ubuntu packages to a dated archive snapshot on amd64 (Fly), so a rebuild
# installs the same versions. snapshot.ubuntu.com does not serve the arm64 ports
# archive, so local arm64 builds use the live mirror.
RUN if [ "$(dpkg --print-architecture)" = amd64 ]; then \
      apt-get update && apt-get install -y --no-install-recommends ca-certificates && \
      sed -i -E "s#http://(archive|security)\.ubuntu\.com/ubuntu/?#https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT}/#" /etc/apt/sources.list.d/ubuntu.sources && \
      rm -rf /var/lib/apt/lists/*; \
    fi
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake ninja-build git pkg-config ca-certificates autoconf automake libtool python3 libevent-dev libssl-dev libzmq3-dev libsqlite3-dev libgmp-dev libargon2-dev libboost-dev libboost-locale-dev libboost-thread-dev libzstd-dev libflint-dev libsodium-dev flatbuffers-compiler libflatbuffers-dev cppzmq-dev libcurl4-openssl-dev nlohmann-json3-dev && rm -rf /var/lib/apt/lists/*
COPY src/shared-utils/secp256k1-zkp /src/secp256k1-zkp
WORKDIR /src/secp256k1-zkp
RUN ./autogen.sh && ./configure --disable-shared --enable-experimental --enable-module-ecdh --enable-module-extrakeys --enable-module-schnorrsig --enable-module-musig --enable-module-ellswift --enable-module-ecdsa-adaptor --enable-module-recovery && make -j4 && make install
COPY src/shared-utils/liboqs /src/liboqs
RUN cmake -S /src/liboqs -B /src/liboqs/build -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DOQS_USE_OPENSSL=OFF -DOQS_BUILD_ONLY_LIB=ON '-DOQS_MINIMAL_BUILD=SIG_ml_dsa_44;SIG_ml_dsa_65;SIG_ml_dsa_87' && cmake --build /src/liboqs/build -j4 && cmake --install /src/liboqs/build
RUN git clone --depth 1 --branch v0.3.11 https://github.com/supranational/blst.git /src/blst && test "$(git -C /src/blst rev-parse HEAD)" = "3dd0f804b1819e5d03fb22ca2e6fac105932043a" && cd /src/blst && ./build.sh -O3 && cp libblst.a /usr/local/lib/ && mkdir -p /usr/local/bindings && cp bindings/*.h /usr/local/bindings/
COPY src/services/core-node/bcore /src/bcore
COPY src/shared-utils/chiavdf /src/bcore/src/external/chiavdf
COPY src/shared-utils/fb-schemas /src/schemas
COPY profile.json /opt/noet/private-work-profile.json
# The overlay is applied by prepare.py; confirm the exact reviewed result.
RUN python3 -c "import hashlib,json;p=json.load(open('/opt/noet/private-work-profile.json'));[exec('assert hashlib.sha256(open(\"/src/bcore/\"+n,\"rb\").read()).hexdigest()==r[\"afterSha256\"],n') for n,r in p['files'].items()]"
RUN flatc --cpp -o /src/bcore/src/rpc /src/schemas/proof.fbs /src/schemas/blockheader.fbs /src/schemas/validation.fbs
WORKDIR /src/bcore
RUN cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_GUI=OFF -DWITH_ZMQ=ON -DENABLE_WALLET=ON -DBUILD_WALLET_TOOL=ON -DBUILD_TESTS=OFF -DENABLE_IPC=OFF && cmake --build build --target bitcoind bitcoin-cli bitcoin-wallet -j4 && sha256sum build/bin/bitcoind build/bin/bitcoin-cli build/bin/bitcoin-wallet > /opt/noet/binaries.sha256

FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55
ARG UBUNTU_SNAPSHOT=20261012T000000Z
# Pin Ubuntu packages to a dated archive snapshot on amd64 (Fly), so a rebuild
# installs the same versions. snapshot.ubuntu.com does not serve the arm64 ports
# archive, so local arm64 builds use the live mirror.
RUN if [ "$(dpkg --print-architecture)" = amd64 ]; then \
      apt-get update && apt-get install -y --no-install-recommends ca-certificates && \
      sed -i -E "s#http://(archive|security)\.ubuntu\.com/ubuntu/?#https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT}/#" /etc/apt/sources.list.d/ubuntu.sources && \
      rm -rf /var/lib/apt/lists/*; \
    fi
RUN apt-get update && apt-get install -y --no-install-recommends libevent-2.1-7t64 libevent-extra-2.1-7t64 libevent-pthreads-2.1-7t64 libssl3t64 libzmq5 libsqlite3-0 libgmp10 libgmpxx4ldbl libargon2-1 libzstd1 libflint18 libsodium23 libboost-locale1.83.0 libboost-thread1.83.0 libcurl4t64 ca-certificates python3 && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home noet && mkdir -p /data && chown noet:noet /data
COPY --from=build /src/bcore/build/bin/bitcoind /src/bcore/build/bin/bitcoin-cli /src/bcore/build/bin/bitcoin-wallet /usr/local/bin/
COPY --from=build /opt/noet /opt/noet
COPY runtime /opt/noet/runtime
ENV PYTHONPATH=/opt/noet/runtime PYTHONDONTWRITEBYTECODE=1
WORKDIR /home/noet
# Starts as root only to take ownership of the mounted volume, then drops to
# UID 10001 before reading secrets or starting any service.
# No upstream supervisor, miner, public verifier, VNC or seed bootstrap.
ENTRYPOINT ["python3", "-m", "noet_node.supervise"]
