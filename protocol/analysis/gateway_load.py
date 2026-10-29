#!/usr/bin/env python3
"""Load test of the mining gateway's lease path, in process, against a stub node.

Many simulated miners (one client address each) lease and release work from
parallel threads. It checks the limits hold under contention (open leases per
client and in total never overshoot) and reports leases per second, so a
change to the gateway can be compared at 10x and 100x today's miners. The
node is a stub that answers like the real one after `--node-ms` milliseconds.

  python3 analysis/gateway_load.py --miners 1000 --rounds 5 --node-ms 2
"""
import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from noet.gateway import Gateway, Refused  # noqa: E402

GENESIS = 'ab' * 32
CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'


def bech32_digits(number, width):
    out = ''
    while number:
        number, digit = divmod(number, 32)
        out = CHARSET[digit] + out
    return out.rjust(width, 'q')


class StubNode:
    def __init__(self, delay):
        self.delay, self.req, self.lock = delay, 0, threading.Lock()

    def __call__(self, method, *params):
        if self.delay:
            time.sleep(self.delay)
        if method == 'validateaddress':
            return {'isvalid': True, 'scriptPubKey': '0014' + '11' * 20}
        if method == 'create_mining_work_unit':
            with self.lock:
                self.req += 1
                req = self.req
            return {'req_id': req, 'height': 7, 'header_prefix': '00' * 76, 'target': 'ff' * 32,
                    'expires_at': time.time() + 600}
        raise AssertionError(method)


def run(miners=100, rounds=5, node_ms=0.0, open_total=512, workers=64, release=True):
    gateway = Gateway(StubNode(node_ms / 1000), 'noet', GENESIS, open_total=open_total,
                      leases_per_hour=10_000)
    outcomes = {'leased': 0, 'capacity': 0, 'limited': 0, 'other': 0}
    peak = {'open': 0, 'perClient': 0}
    lock = threading.Lock()
    queue = [(m, r) for r in range(rounds) for m in range(miners)]
    position = [0]

    def worker():
        while True:
            with lock:
                if position[0] >= len(queue):
                    return
                miner, _ = queue[position[0]]
                position[0] += 1
            client = f'10.{miner // 65536}.{miner // 256 % 256}.{miner % 256}'
            address = 'noet1q' + bech32_digits(miner, 38)
            try:
                lease = gateway.lease(address, client)
                kind = 'leased'
            except Refused as refused:
                kind = 'capacity' if refused.status == 503 else 'limited' if refused.status == 429 else 'other'
                lease = None
            with gateway.lock:
                opened = len(gateway.leases)
                mine = sum(1 for v in gateway.leases.values() if v['client'] == client)
            with lock:
                outcomes[kind] += 1
                peak['open'] = max(peak['open'], opened)
                peak['perClient'] = max(peak['perClient'], mine)
            if lease and release:
                gateway.release(lease['job']['req_id'], lease['lease'])

    started = time.perf_counter()
    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    seconds = time.perf_counter() - started
    return {'miners': miners, 'rounds': rounds, 'nodeMs': node_ms, 'workers': workers, **outcomes,
            'seconds': round(seconds, 3), 'leasesPerSecond': round(outcomes['leased'] / seconds, 1),
            'peakOpen': peak['open'], 'peakPerClient': peak['perClient'],
            'limits': {'openTotal': open_total, 'openPerClient': gateway.open_per_client}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--miners', type=int, default=1000)
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--node-ms', type=float, default=2.0)
    parser.add_argument('--workers', type=int, default=64)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.miners, args.rounds, args.node_ms, workers=args.workers), indent=2))


if __name__ == '__main__':
    sys.exit(main())
