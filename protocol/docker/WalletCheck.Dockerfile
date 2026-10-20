# Diagnostic image only. Build Wallet.Dockerfile first.
ARG NOET_WALLET_IMAGE=noet-wallet:candidate
FROM ${NOET_WALLET_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends valgrind && rm -rf /var/lib/apt/lists/*
USER noet
ENV QT_QPA_PLATFORM=offscreen QT_ENABLE_REGEXP_JIT=0
ENTRYPOINT ["timeout", "--signal=KILL", "600s", "valgrind", "--error-exitcode=97", "--leak-check=full", "--show-leak-kinds=definite", "--errors-for-leak-kinds=definite"]
CMD ["/opt/noet/bin/test_bitcoin-qt"]
