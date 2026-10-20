# Offline CPU mining research. Check the local parent tag against its recorded ID before and after building.
# Supplies the compiled CPU miner's dynamic libraries, not a hosted service.
ARG NOET_VERIFIER_IMAGE
FROM ${NOET_VERIFIER_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libargon2-1 libzmq5 && rm -rf /var/lib/apt/lists/*
USER verifier
