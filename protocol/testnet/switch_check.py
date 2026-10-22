#!/usr/bin/env python3
"""Check the NOET model switch on a real test-variant node.

  python3 protocol/testnet/switch_check.py --image IMAGE [--docker-context CTX]

IMAGE is built from the mainnet overlay's test variant (deploy/fly/prepare.py
--profile mainnet --test-variant): mainnet consensus with mock validation and
the switch height read from -modelswitchheight. On a mocked clock, with no
network, the check:
  * mines to height UPGRADE with no switch scheduled, as the live chain runs;
  * restarts the node on the same data with the switch at height SWITCH (the
    upgrade) and expects the switch model registered next to the genesis model;
  * mines fast blocks (60 s) up to the switch and slow ones (3,000 s) after it,
    and expects every block to carry its height's model;
  * expects a block with the other model refused just before and at the switch;
  * compares every nBits with an independent Python port: ASERT before the
    switch, floor(T(SWITCH-1) * num / den) at it, and ASERT from a fresh anchor
    at SWITCH after it (pow.cpp phase C, testnet/prepare.py);
  * restarts once more after the switch and mines on.
Prints a JSON report; exits 1 on any failure. The containers and the data volume
are removed afterwards.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from asert_check import POW_LIMIT, asert, get_compact, set_compact  # noqa: E402
from prepare import (ASERT_ANCHOR_BITS, ASERT_PHASE_B_OFFSET, MODEL, REVISION,  # noqa: E402
                     SWITCH_TEST_SCALE, mainnet)

START = 20  # -daav2height for this check; mainnet pins its own (2)
PHASE_B = START + ASERT_PHASE_B_OFFSET
UPGRADE = 60  # the node restarts with the switch scheduled here
SWITCH = PHASE_B + 36  # after phase B, as on mainnet
END = SWITCH + 60
GENESIS_MODEL = MODEL + '@' + REVISION


def expected_bits(height, times, bits, scale):
    """nBits for `height` given the chain's earlier timestamps and bits."""
    if height < START:
        return get_compact(POW_LIMIT)
    if height == START:
        return ASERT_ANCHOR_BITS
    if height == SWITCH:
        num, den = scale
        return get_compact(min(POW_LIMIT, set_compact(bits[SWITCH - 1]) * num // den))
    if height > SWITCH:
        anchor, anchor_height, parent_time = set_compact(bits[SWITCH]), SWITCH, times[SWITCH - 1]
    elif height < PHASE_B:
        anchor, anchor_height, parent_time = set_compact(ASERT_ANCHOR_BITS), START, times[START - 1]
    else:
        anchor, anchor_height, parent_time = set_compact(bits[PHASE_B - 1]), PHASE_B - 1, times[PHASE_B - 2]
    return get_compact(asert(anchor, times[height - 1] - parent_time, height - 1 - anchor_height))


def run(args):
    spec = mainnet()
    switch = spec['modelSwitch']
    scale = switch['targetScale'] or SWITCH_TEST_SCALE
    switch_model = switch['model'] + '@' + switch['revision']
    docker = ['docker', '--context', args.docker_context]
    tag = uuid.uuid4().hex[:8]
    volume, names = 'noet-switch-' + tag, []
    failures = []

    def start(*extra):
        name = f'noet-switch-{tag}-{len(names)}'
        names.append(name)
        subprocess.run(docker + ['run', '-d', '--name', name, '--network', 'none', '-v', volume + ':/root/.tensorcash',
                                 '--entrypoint', 'bitcoind', args.image, '-chain=tensor-reg', '-server=1',
                                 '-validationapi=mock', '-useextapi=0', '-listen=0', '-rpcport=29443',
                                 '-printtoconsole=0', f'-daav2height={START}', f'-daav2phasebheight={PHASE_B}',
                                 *extra], check=True, capture_output=True)
        return name

    def stop(name):
        subprocess.run(docker + ['stop', '-t', '60', name], check=True, capture_output=True)

    def cli(*parts, check=True):
        out = subprocess.run(docker + ['exec', names[-1], 'bitcoin-cli', '-chain=tensor-reg', '-rpcport=29443',
                                       '-rpcwait', *map(str, parts)], capture_output=True, text=True)
        if check and out.returncode:
            raise RuntimeError(f'{parts[0]} failed: {out.stderr.strip()[:300]}')
        if out.returncode:
            return out.stderr
        text = out.stdout.strip()
        try:
            return json.loads(text)
        except ValueError:
            return text

    def models():
        return sorted(m['model_name'] + '@' + m['model_commit'] for m in cli('getmodelslist', 'false'))

    def refused(model, height):
        """Mine one block with `model` forced; expect the node to refuse it."""
        cli('setminermodel', model)
        tip = cli('getblockcount')
        error = cli('generatetoaddress', 1, address, check=False)
        cli('setminermodel', '', 'true')
        log = subprocess.run(docker + ['exec', names[-1], 'sh', '-c',
                                       'grep -h bad-model-for-height /root/.tensorcash/*/debug.log | tail -1'],
                             capture_output=True, text=True).stdout.strip()
        ok = cli('getblockcount') == tip and isinstance(error, str) and f'height {height} must carry' in log
        if not ok:
            failures.append({'refusal': model, 'height': height, 'error': str(error)[:300], 'log': log[-300:]})
        return {'height': height, 'model': model, 'refused': ok, 'log': log[log.find('bad-model-for-height'):][:200]}

    times, bits, rows, refusals = {}, {}, [], []
    try:
        start()
        cli('createwallet', 'switch')
        address = cli('getnewaddress', '', 'bech32')
        genesis = cli('getblockheader', cli('getblockhash', 0))
        times[0], bits[0] = genesis['time'], int(genesis['bits'], 16)
        before_upgrade = models()
        now = times[0] + 600

        def mine(height, gap):
            nonlocal now
            now += gap
            cli('setmocktime', now)
            block = cli('getblock', cli('generatetoaddress', 1, address)[0])
            times[height], bits[height] = block['time'], int(block['bits'], 16)
            want_model = switch_model if height >= SWITCH else GENESIS_MODEL
            want_bits = expected_bits(height, times, bits, scale)
            row = {'height': height, 'bits': block['bits'], 'model': block['tst']}
            if block['tst'] != want_model:
                failures.append({**row, 'expectedModel': want_model})
            if bits[height] != want_bits:
                failures.append({**row, 'expectedBits': f'{want_bits:08x}'})
            rows.append(row)

        for height in range(1, UPGRADE + 1):
            mine(height, 60)
        stop(names[-1])
        start(f'-modelswitchheight={SWITCH}')  # the upgrade: same data, switch scheduled
        cli('setmocktime', now)
        after_upgrade = models()
        if cli('getblockcount') != UPGRADE or after_upgrade != sorted([GENESIS_MODEL, switch_model]):
            failures.append({'upgrade': after_upgrade})
        for height in range(UPGRADE + 1, SWITCH - 1):
            mine(height, 60)
        refusals.append(refused(switch_model, SWITCH - 1))  # too early for the switch model
        mine(SWITCH - 1, 60)
        refusals.append(refused(GENESIS_MODEL, SWITCH))  # the genesis model is over
        for height in range(SWITCH, SWITCH + 30):
            mine(height, 3000)
        stop(names[-1])
        start(f'-modelswitchheight={SWITCH}')  # restart after the switch
        cli('setmocktime', now)
        restarted = {'height': cli('getblockcount'), 'models': models()}
        if restarted != {'height': SWITCH + 29, 'models': sorted([GENESIS_MODEL, switch_model])}:
            failures.append({'restart': restarted})
        for height in range(SWITCH + 30, END + 1):
            mine(height, 3000)
        at = {r['height']: r for r in rows}
        report = {'image': args.image, 'asertHeight': START, 'phaseBHeight': PHASE_B, 'upgradeHeight': UPGRADE,
                  'switchHeight': SWITCH, 'targetScale': list(scale), 'blocks': len(rows),
                  'modelsBeforeUpgrade': before_upgrade, 'modelsAfterUpgrade': after_upgrade,
                  'refusals': refusals, 'restartAfterSwitch': restarted,
                  'bits': {'beforeSwitch': at[SWITCH - 1]['bits'], 'atSwitch': at[SWITCH]['bits'],
                           'final': rows[-1]['bits']},
                  'failures': failures, 'passed': not failures}
        if args.out:
            Path(args.out).write_text(json.dumps({**report, 'rows': rows}, indent=2) + '\n')
        print(json.dumps(report, indent=2))
        return 0 if report['passed'] else 1
    finally:
        for name in names:
            subprocess.run(docker + ['rm', '-f', name], capture_output=True)
        subprocess.run(docker + ['volume', 'rm', '-f', volume], capture_output=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--image', required=True)
    parser.add_argument('--docker-context', default='colima-noet-build')
    parser.add_argument('--out', help='also write every block to this JSON file')
    sys.exit(run(parser.parse_args()))
