# Build Node.Dockerfile --target build -t noet-core:build first.
ARG NOET_BUILD_IMAGE=noet-core:build
FROM ${NOET_BUILD_IMAGE} AS wallet-build
RUN apt-get update && apt-get install -y --no-install-recommends qt6-base-dev qt6-tools-dev qt6-l10n-tools qt6-tools-dev-tools libgl-dev libqrencode-dev && rm -rf /var/lib/apt/lists/*
COPY patches/noet-wallet.patch /tmp/noet-wallet.patch
RUN cd /src/bcore && git apply --check /tmp/noet-wallet.patch && git apply /tmp/noet-wallet.patch
COPY desktop/brand_wallet.py /tmp/brand_wallet.py
RUN python3 /tmp/brand_wallet.py /src/bcore
COPY desktop/noet.png /src/bcore/src/qt/res/icons/bitcoin.png
RUN cmake -S /src/bcore -B /src/bcore/build -G Ninja -DBUILD_GUI=ON -DBUILD_GUI_TESTS=ON -DENABLE_IPC=OFF && cmake --build /src/bcore/build --target bitcoin-qt test_bitcoin-qt -j3
RUN mkdir -p /opt/noet/bin && cp /src/bcore/build/bin/bitcoin-qt /opt/noet/bin/noet-wallet && cp /src/bcore/build/bin/bitcoind /opt/noet/bin/noetd && cp /src/bcore/build/bin/bitcoin-cli /opt/noet/bin/noet-cli && cp /src/bcore/build/bin/bitcoin-wallet /opt/noet/bin/noet-wallet-tool
FROM ubuntu:24.04 AS wallet
RUN apt-get update && apt-get install -y --no-install-recommends libevent-2.1-7t64 libevent-extra-2.1-7t64 libevent-pthreads-2.1-7t64 libssl3t64 libzmq5 libsqlite3-0 libgmp10 libgmpxx4ldbl libargon2-1 libzstd1 libflint18 libsodium23 libboost-locale1.83.0 libboost-thread1.83.0 libcurl4t64 libqt6widgets6 libqt6network6 libqt6test6 libqt6concurrent6 libqt6dbus6 qt6-qpa-plugins libqrencode4 ca-certificates python3 && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home noet
COPY --from=wallet-build /opt/noet /opt/noet
COPY --from=wallet-build /src/bcore/build/bin/test_bitcoin-qt /opt/noet/bin/
COPY desktop/launch_wallet.py /opt/noet/launch_wallet.py
USER noet
WORKDIR /home/noet
ENV PATH="/opt/noet/bin:${PATH}"
ENTRYPOINT ["python3", "/opt/noet/launch_wallet.py"]
