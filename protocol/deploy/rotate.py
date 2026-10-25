#!/usr/bin/env python3
"""Check and rotate the relay certificates for a hosted node and its GPU host.

  rotate.py --secrets DIR --check                  days left; exit 1 under 7 days
  rotate.py --secrets DIR                          issue a fresh set locally
  rotate.py --secrets DIR --app APP --apply \\
            --gpu-ssh WRAPPER --gpu-host USER@HOST   issue, install on both sides
  (repeat --gpu-host, and --gpu-ssh if they differ, for standby verifiers:
   every GPU host shares the same identity and must change with the node)

DIR/certs always holds the current set (launch-testnet.sh reads it); the
previous set moves to DIR/certs-retired-<time>. Each side pins the other's
certificate, so the node (Fly secret NOET_TLS_BUNDLE, which restarts it) and
the GPU host (gpu-bundle.b64, then a live.py restart) must change together.
Proofs pause for the minute in between.
"""
import argparse
import json
from pathlib import Path
import ssl
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from certs import NODE, issue  # noqa: E402

WARN_DAYS = 7
# The container's start shell also contains "python3 checks/live.py" and
# killing it restarts the container, so only the anchored pattern is used. The
# restart runs detached on the host (as control.py LIVE_START starts live.py),
# so a dropped SSH session cannot leave the host without its supervisor.
RESTART_GPU = '''set -e; cd /workspace/noet-gpu; mkdir -p output/live
tag="rotate-$(date +%s)"
cat > output/live/rotate-restart.sh <<'EOF'
cd /workspace/noet-gpu || exit 1
LIVE='^python3 checks/live[.]py'
pkill -TERM -f "$LIVE"
for _ in $(seq 1 90); do pgrep -f "$LIVE" >/dev/null || break; sleep 2; done
if pgrep -f "$LIVE" >/dev/null; then echo "$1: did not stop"; exit 1; fi
install -d -o 10001 -g 10001 -m 700 /tmp/noet-gpu-home
(umask 022; PATH=/usr/bin:/bin setsid nohup python3 checks/live.py >> output/live/live-stdout.log 2>&1 < /dev/null &)
sleep 5; echo "$1: started $(pgrep -f "$LIVE")"
EOF
setsid nohup bash output/live/rotate-restart.sh "$tag" >> output/live/restart.log 2>&1 < /dev/null &
for _ in $(seq 1 60); do grep -q "^$tag: " output/live/restart.log && break; sleep 5; done
grep "^$tag: started [0-9]" output/live/restart.log
sleep 20; tail -3 output/live/supervisor.jsonl'''


def days_left(certs, now=None):
    """Whole days until the first of the CA and node leaves expires."""
    ends = {}
    for name in ('ca', *NODE):
        line = subprocess.run(['openssl', 'x509', '-in', str(certs / f'{name}.pem'), '-noout', '-enddate'],
                              check=True, capture_output=True, text=True).stdout
        ends[name] = ssl.cert_time_to_seconds(line.strip().split('=', 1)[1])
    return int((min(ends.values()) - (now or time.time())) // 86400)


def rotate(secrets, days=30):
    fresh = secrets / 'certs.new'
    pins = issue(fresh, days)
    if (secrets / 'certs').exists():
        (secrets / 'certs').rename(secrets / time.strftime('certs-retired-%Y%m%dT%H%M%SZ', time.gmtime()))
    fresh.rename(secrets / 'certs')
    (secrets / 'pins.json').write_text(json.dumps(pins, indent=2) + '\n')
    return pins


def gpu_hosts(ssh, hosts):
    """Pair each GPU host with its SSH wrapper: one wrapper for all, or one each."""
    if not hosts or not ssh or len(ssh) not in (1, len(hosts)):
        raise ValueError('Pass --gpu-host for every GPU host and one --gpu-ssh, or one per host')
    return list(zip(ssh * len(hosts) if len(ssh) == 1 else ssh, hosts))


def apply(secrets, app, hosts):
    bundle = (secrets / 'certs/node-bundle.b64').read_text().strip()
    # Values travel on stdin, never in the process list or shell history.
    subprocess.run(['fly', 'secrets', 'import', '--app', app],
                   input=f'NOET_TLS_BUNDLE={bundle}\n', text=True, check=True, capture_output=True)
    for ssh, host in hosts:
        with open(secrets / 'certs/gpu-bundle.b64', 'rb') as stream:
            subprocess.run([ssh, host, 'umask 077; cat > /workspace/noet-live/gpu-bundle.b64'], stdin=stream, check=True)
        subprocess.run([ssh, host, RESTART_GPU], check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--secrets', type=Path, required=True, help='Private operator directory, e.g. .noet-session/live-...')
    parser.add_argument('--check', action='store_true', help='Only report the days left')
    parser.add_argument('--app', default='noet-mainnet')
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--apply', action='store_true', help='Install the new set on the node and GPU host')
    parser.add_argument('--gpu-ssh', action='append', help='SSH wrapper for the GPU hosts (required with --apply)')
    parser.add_argument('--gpu-host', action='append', help='user@host of a GPU host (required with --apply; repeatable)')
    args = parser.parse_args(argv)
    if args.check:
        left = days_left(args.secrets / 'certs')
        print(json.dumps({'daysLeft': left, 'rotate': left < WARN_DAYS}))
        return 1 if left < WARN_DAYS else 0
    if args.apply:
        try:
            hosts = gpu_hosts(args.gpu_ssh, args.gpu_host)
        except ValueError as error:
            parser.error('--apply changes both sides: ' + str(error))
    pins = rotate(args.secrets, args.days)
    if args.apply:
        apply(args.secrets, args.app, hosts)
    print(json.dumps({'directory': str(args.secrets / 'certs'), 'pins': pins, 'applied': args.apply,
                      'daysLeft': days_left(args.secrets / 'certs')}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
