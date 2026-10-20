# NOET network monitor: one small always-on process, standard library only.
FROM python:3.12.11-alpine3.22@sha256:efcdfa6a6b2fd2afb9c7dfa9a5b288a6f68338b5cfdebe6b637d986067d85757
RUN adduser -D -u 10001 monitor
COPY monitor.py /opt/noet/monitor.py
USER monitor
CMD ["python3", "-u", "/opt/noet/monitor.py"]
