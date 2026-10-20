#!/usr/bin/env bash
# Build only the disposable private-work node, never a production release.
set -euo pipefail
umask 022
cd /workspace/noet-gpu
sha256sum -c native-inputs.sha256
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends autoconf automake libtool libevent-dev libsqlite3-dev libboost-dev libboost-locale-dev libboost-thread-dev libboost-test-dev libzstd-dev libflint-dev libsodium-dev libcurl4-openssl-dev nlohmann-json3-dev
python3 - <<'PY'
import hashlib,json,shutil,tarfile
from pathlib import Path
root=Path('native-source');root.mkdir(exist_ok=False)
with tarfile.open('native.tar') as tar:tar.extractall(root,filter='data')
overlay=Path('native-overlay');overlay.mkdir(exist_ok=False)
with tarfile.open('private-work.tar') as tar:tar.extractall(overlay,filter='data')
profile=json.loads((overlay/'profile.json').read_text())
if profile['format']!='noet-private-work-v1' or profile['productionReady']:
    raise ValueError('Expected the private research overlay')
core=root/'services/core-node/bcore'
for name,record in profile['files'].items():
    before=core/name;after=overlay/name
    if hashlib.sha256(before.read_bytes()).hexdigest()!=record['beforeSha256']:
        raise ValueError('Native baseline differs from reviewed source')
    if hashlib.sha256(after.read_bytes()).hexdigest()!=record['afterSha256']:
        raise ValueError('Private overlay differs from recorded source')
    shutil.copyfile(after,before)
shutil.copytree(root/'shared-utils/chiavdf',core/'src/external/chiavdf',dirs_exist_ok=True)
PY
cd native-source/shared-utils/secp256k1-zkp
./autogen.sh
./configure --disable-shared --enable-experimental --enable-module-ecdh --enable-module-extrakeys --enable-module-schnorrsig --enable-module-musig --enable-module-ellswift --enable-module-ecdsa-adaptor --enable-module-recovery
make -j4
make install
cd /workspace/noet-gpu/native-source
cmake -S shared-utils/liboqs -B liboqs-build -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DOQS_USE_OPENSSL=OFF -DOQS_BUILD_ONLY_LIB=ON '-DOQS_MINIMAL_BUILD=SIG_ml_dsa_44;SIG_ml_dsa_65;SIG_ml_dsa_87'
cmake --build liboqs-build -j4
cmake --install liboqs-build
git clone --depth 1 --branch v0.3.11 https://github.com/supranational/blst.git blst
[[ "$(git -C blst rev-parse HEAD)" == 3dd0f804b1819e5d03fb22ca2e6fac105932043a ]]
(cd blst && ./build.sh -O3)
cp blst/libblst.a /usr/local/lib/
mkdir -p /usr/local/bindings
cp blst/bindings/*.h /usr/local/bindings/
flatc --cpp -o services/core-node/bcore/src/rpc shared-utils/fb-schemas/proof.fbs shared-utils/fb-schemas/blockheader.fbs shared-utils/fb-schemas/validation.fbs
cmake -S services/core-node/bcore -B node-build -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_GUI=OFF -DWITH_ZMQ=ON -DENABLE_WALLET=ON -DBUILD_WALLET_TOOL=ON -DBUILD_TESTS=OFF -DENABLE_IPC=OFF
cmake --build node-build --target bitcoind bitcoin-cli bitcoin-wallet -j4
chmod a+rx /workspace/noet-gpu/native-source /workspace/noet-gpu/native-source/node-build /workspace/noet-gpu/native-source/node-build/bin
sha256sum node-build/bin/bitcoind node-build/bin/bitcoin-cli node-build/bin/bitcoin-wallet > /workspace/noet-gpu/native-binaries.sha256
printf 'NOET_NATIVE_BUILD_COMPLETE\n'
