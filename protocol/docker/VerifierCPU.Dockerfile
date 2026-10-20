# Research runtime. Pass the same immutable builder recorded in BUILDER.
ARG NOET_NATIVE_BUILDER
FROM ${NOET_NATIVE_BUILDER} AS native
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3-dev python3-venv pybind11-dev && rm -rf /var/lib/apt/lists/*
COPY shared-utils/ /build/shared-utils/
WORKDIR /build/shared-utils/fb-schemas
RUN flatc --python --cpp validation.fbs && flatc --python --cpp blockheader.fbs && flatc --python --cpp proof.fbs
RUN mkdir /native && c++ -O2 -std=c++17 -shared -fPIC $(python3-config --includes) -I/usr/include/pybind11 -I. ../pow-utils/pfunpack/pfunpack.cpp -o /native/pfunpack.so
WORKDIR /build/shared-utils/chiavdf/src
RUN cc -O2 -fPIC -c refcode/lzcnt.c -o /native/lzcnt.o && c++ -O2 -std=c++17 -shared -fPIC $(python3-config --includes) -I/usr/include/pybind11 -I. python_bindings/fastvdf.cpp /native/lzcnt.o -lgmp -lgmpxx -pthread -o /native/chiavdf.so
RUN bash /build/shared-utils/pow-utils/build_credit_v4_lib.sh /native

FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55 AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv libgmp10 libgmpxx4ldbl libssl3t64 libgomp1 ca-certificates && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home verifier
RUN python3 -m venv /opt/runtime
ENV PATH=/opt/runtime/bin:$PATH PYTHONPATH=/app/src:/app:/opt/noet
COPY services/verification-api/requirements.txt /tmp/requirements.txt
RUN pip install --only-binary=:all: -r /tmp/requirements.txt && pip check && pip freeze > /opt/runtime/requirements.resolved.txt
COPY services/verification-api/src/ /app/src/
COPY shared-utils/pow-utils/*.py /app/src/
COPY shared-utils/pow-utils/*.py /app/src/utils/
COPY --from=native /build/shared-utils/fb-schemas/proof/ /app/src/proof/
COPY --from=native /native/pfunpack.so /native/chiavdf.so /app/
COPY --from=native /native/libtc_credit_v4.so /app/src/utils/
COPY noet/ /opt/noet/noet/
COPY LICENSE NOTICE LICENSING.md NOET-BUILD-INPUTS.json /app/provenance/
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 HF_HOME=/tmp/noet-hf MPLCONFIGDIR=/tmp/noet-mpl POW_ALLOW_CPU_INFERENCE=1
ENV POI_DETECTOR_BUNDLE=/app/src/poi_detector/assets/detector-amber-red-v2/bundle.json POI_DETECTOR_SHADOW_DIR=/tmp/noet-detector
USER verifier
WORKDIR /app/src
RUN python -c "import pfunpack,chiavdf; from utils import credit_v4_native,sunset_native; print(credit_v4_native.self_test()); print(sunset_native.self_test()); import proof_verifier; print('Verifier import passed')"
RUN python -m poi_detector.advisory_bundle /app/src/poi_detector/assets/detector-amber-red-v2/bundle.json --load
RUN python -c "import detector_gate as d; status=d.warm_on_start(); print(status); assert status['ready']; d.runtime().close()"
ENTRYPOINT ["python", "-m", "noet.verifier_entry"]
