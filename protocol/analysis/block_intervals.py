#!/usr/bin/env python3
"""Block intervals and difficulty on a NOET network, from the public explorer API.

Reads every block's height, timestamp, difficulty and producer through
`/api/chain/blocks?before=N` (25 a page), then reports how far the intervals
are from the 600-second target, by window. Nothing here changes consensus;
it is the evidence a difficulty proposal starts from.

  python3 analysis/block_intervals.py [--api https://www.noet.network/api] [--out FILE]
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request

TARGET = 600


def get(api, path):
    request = urllib.request.Request(api + path, headers={'User-Agent': 'noet-analysis'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read(1 << 22))


def all_blocks(api, pause=0.2):
    rows, before = [], None
    while True:
        page = get(api, '/chain/blocks' + (f'?before={before}' if before else ''))
        if not page:
            break
        rows += page
        before = min(b['height'] for b in page)
        if before <= 1:
            break
        time.sleep(pause)
    return sorted({b['height']: b for b in rows}.values(), key=lambda b: b['height'])


def summary(intervals):
    if not intervals:
        return None
    ordered = sorted(intervals)
    return {
        'blocks': len(intervals),
        'meanSeconds': round(statistics.fmean(intervals), 1),
        'medianSeconds': statistics.median(intervals),
        'p90Seconds': ordered[int(len(ordered) * 0.9) - 1],
        'maxSeconds': ordered[-1],
        'meanVsTarget': round(statistics.fmean(intervals) / TARGET, 3),
        # Exponential inter-arrival times have a coefficient of variation of 1.
        'coefficientOfVariation': round(statistics.pstdev(intervals) / statistics.fmean(intervals), 3),
        'overOneHour': sum(1 for i in intervals if i > 3600),
    }


def analyse(blocks, window=144):
    intervals = [(b['height'], b['timestamp'] - a['timestamp']) for a, b in zip(blocks, blocks[1:])]
    # Block 1 carries the allocations at the genesis time; start after it.
    steady = [(h, i) for h, i in intervals if h > 2]
    windows = []
    for start in range(0, len(steady), window):
        part = steady[start:start + window]
        if len(part) < window // 2:
            continue
        s = summary([i for _, i in part])
        heights = [h for h, _ in part]
        difficulty = [float(b['difficulty']) for b in blocks if heights[0] <= b['height'] <= heights[-1]]
        windows.append({'from': heights[0], 'to': heights[-1], **s,
                        'difficultyStart': difficulty[0], 'difficultyEnd': difficulty[-1]})
    return {
        'targetSeconds': TARGET,
        'tip': blocks[-1]['height'],
        'all': summary([i for _, i in steady]),
        'negativeIntervals': sum(1 for _, i in steady if i < 0),
        'windows': windows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--api', default='https://www.noet.network/api')
    parser.add_argument('--out')
    args = parser.parse_args(argv)
    result = analyse(all_blocks(args.api))
    result['source'] = args.api + '/chain/blocks'
    result['readAt'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    text = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, 'w') as handle:
            handle.write(text + '\n')
    print(text)


if __name__ == '__main__':
    sys.exit(main())
