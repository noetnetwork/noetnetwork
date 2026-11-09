#!/usr/bin/env python3
"""The Qwen3-14B switch on mainnet, from the public explorer API.

Reads every block from a window before the switch height in
mainnet/network.json to the tip through `/api/chain/block/<height>`, and
reports, on each side of the switch: the model each proof names, the Full
verdicts, the time from block to verdict, the output tokens, the producers
and the intervals between blocks. It records what the chain shows; it does
not judge whether the switch is production ready.

  python3 analysis/model_switch_mainnet.py [--api https://www.noet.network/api] [--before 144] [--out FILE]
"""
import argparse
import json
import pathlib
import statistics
import sys
import time
import urllib.request

NETWORK = pathlib.Path(__file__).resolve().parent.parent / 'mainnet' / 'network.json'


def get(api, path):
    request = urllib.request.Request(api + path, headers={'User-Agent': 'noet-analysis'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read(1 << 23))


def pct(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def producer(block):
    for output in block.get('rewardOutputs') or []:
        if output.get('address'):
            return output['address']
    return None


def side(blocks, previous):
    """Summary of a run of consecutive blocks; `previous` is the block before the first."""
    verdicts, waits, tokens, intervals, models, producers = {}, [], [], [], {}, set()
    last = previous
    for b in blocks:
        model = (b.get('proof') or {}).get('model') or 'none'
        models[model] = models.get(model, 0) + 1
        full = ((b.get('verdict') or {}).get('full') or 'none').lower()
        verdicts[full] = verdicts.get(full, 0) + 1
        at = (b.get('verdict') or {}).get('recordedAt')
        if full == 'green' and isinstance(at, int) and at >= b['timestamp']:
            waits.append(at - b['timestamp'])
        if isinstance((b.get('work') or {}).get('outputTokens'), int):
            tokens.append(b['work']['outputTokens'])
        if producer(b):
            producers.add(producer(b))
        if last is not None:
            intervals.append(b['timestamp'] - last['timestamp'])
        last = b
    return {
        'from': blocks[0]['height'], 'to': blocks[-1]['height'], 'blocks': len(blocks),
        'proofModels': models,
        'fullVerdicts': verdicts,
        'secondsToGreen': {'median': statistics.median(waits) if waits else None, 'p90': pct(waits, 0.9),
                           'max': max(waits) if waits else None, 'blocks': len(waits)},
        'outputTokens': {'median': statistics.median(tokens) if tokens else None, 'min': min(tokens, default=None),
                         'max': max(tokens, default=None)},
        'producers': len(producers),
        'intervalSeconds': {'median': statistics.median(intervals) if intervals else None,
                            'mean': round(statistics.mean(intervals), 1) if intervals else None,
                            'max': max(intervals, default=None)},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--api', default='https://www.noet.network/api')
    parser.add_argument('--before', type=int, default=144, help='blocks to read before the switch')
    parser.add_argument('--out')
    args = parser.parse_args()
    switch = json.loads(NETWORK.read_text())['modelSwitch']
    height = switch['height']
    expected = f'{switch["model"]}@{switch["revision"]}'
    status = get(args.api, '/chain/status')
    tip = status['height']
    if tip < height:
        sys.exit(f'the chain is at {tip}, before the switch at {height}')
    blocks = []
    for h in range(height - args.before - 1, tip + 1):
        blocks.append(get(args.api, f'/chain/block/{h}'))
        time.sleep(0.1)
    previous, before, after = blocks[0], [b for b in blocks[1:] if b['height'] < height], [b for b in blocks if b['height'] >= height]
    after_models = {(b.get('proof') or {}).get('model') for b in after}
    record = {
        'format': 'noet-model-switch-mainnet-v1',
        'date': time.strftime('%Y-%m-%d', time.gmtime()),
        'productionReady': False,
        'question': f'Did mainnet change to {switch["model"]} at block {height}, and do its blocks reach a GREEN Full verdict like the blocks before?',
        'method': 'analysis/model_switch_mainnet.py read every block from the public explorer API, from '
                  f'{args.before} blocks before the switch height in mainnet/network.json to the tip, and '
                  'summarised each side of the switch: the model each proof names, Full verdicts, seconds '
                  'from block time to the GREEN verdict, output tokens, reward addresses and intervals.',
        'source': args.api + '/chain/block/<height>',
        'readAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'switch': {'height': height, 'model': expected},
        'tip': tip,
        'everyBlockFromSwitchNamesTheModel': after_models == {expected},
        'before': side(before, previous),
        'after': side(after, before[-1] if before else previous),
    }
    text = json.dumps(record, indent=1) + '\n'
    if args.out:
        pathlib.Path(args.out).write_text(text)
    print(text, end='')


if __name__ == '__main__':
    main()
