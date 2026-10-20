FROM python:3.12-slim
RUN useradd --uid 10001 --create-home noet
RUN mkdir -p /data && chown noet:noet /data
ENV NOET_INDEX_PATH=/data/explorer.sqlite3
WORKDIR /app
COPY noet /app/noet
USER noet
CMD ["python", "-m", "noet.serve"]
