# Disposable headless Linux approval-window check. Not a wallet release.
FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-tk xvfb xauth && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home noet
WORKDIR /work
COPY noet/*.py /work/protocol/noet/
COPY lab.network.json /work/protocol/
COPY desktop/native_prompt_check.py /work/check.py
ENV PYTHONPATH=/work/protocol XDG_CACHE_HOME=/tmp/noet-cache
USER noet
CMD ["timeout", "30s", "xvfb-run", "-a", "python3", "/work/check.py"]
