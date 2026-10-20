#!/usr/bin/env python3
"""Exercise native mining-work issuance with isolated, real-mode lab nodes.

No verifier is connected; no proof, block, model registration or payout is
accepted. The activation schedules and OP_TRUE payout are disposable fixtures.
"""
import argparse
import json
import os
from pathlib import Path
import re
import time
import uuid
from node_smoke import cmd, rpc, wait_for

IMAGE = os.environ.get('NOET_NODE_IMAGE') or json.loads(
    (Path(__file__).resolve().parents[1]/'validation/node-image.json').read_text())['imageId']


def validate_work(work, genesis, active):
    header = bytes.fromhex(work['header_prefix'])
    assert len(header) == 76
    assert header[4:36][::-1].hex() == genesis == work['tip_hash']
    assert work['height'] == 1 and type(work['height']) is int
    assert work['network'] == 'tensor-reg'
    bits = int.from_bytes(header[72:76], 'little')
    exponent, mantissa = bits >> 24, bits & 0x7fffff
    assert not bits & 0x800000
    target = mantissa << (8*(exponent-3)) if exponent >= 3 else mantissa >> (8*(3-exponent))
    assert int(work['target'], 16) == target and len(work['target']) == 64
    assert time.time() < work['expires_at'] < time.time()+3600
    assert type(work['req_id']) is int and 0 < work['req_id'] < 2**32
    assert (work.get('stepbind'), work.get('v4')) == ((1, 1) if active else (None, None))


def check(output):
    if output.exists(): raise FileExistsError(output)
    image = cmd('image', 'inspect', '--format', '{{.Id}}', IMAGE)
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image): raise ValueError('Expected immutable image ID')
    expected = json.loads((Path(__file__).resolve().parents[1]/'lab.network.json').read_text())['genesis']
    results = []
    for active in (False, True):
        name = 'noet-work-' + uuid.uuid4().hex[:12]
        flags = ['-v3activationheight=1', '-stepbindheight=1', '-proofv4height=1'] if active else []
        try:
            cmd('run', '-d', '--name', name, '--network', 'none', '--pull=never',
                '--memory', '2g', '--cpus', '1', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                image, '-server=1', '-validationapi=real', '-useextapi=1', '-connect=0',
                '-port=29441', '-rpcport=29443', '-maxminingworkunits=4', *flags)
            wait_for(lambda: rpc(name, 'getblockchaininfo'), seconds=30)
            assert rpc(name, 'getblockhash', 0) == expected
            rejected = []
            def reject(label, method, params, reason):
                try: rpc(name, method, *params)
                except RuntimeError as error:
                    assert reason in str(error), str(error)
                    rejected.append(label)
                else: raise AssertionError('Unexpected work acceptance: '+label)
            for label, params, reason in (
                ('wrong-network', ['main', '51'], 'network mismatch'),
                ('empty-payout', ['tensor-reg', ''], 'non-empty'),
                ('malformed-payout', ['tensor-reg', 'zz'], 'hexadecimal'),
                ('unknown-parent', ['tensor-reg', '51', '', 'ab'*32], 'build-ahead'),
                ('oversized-extranonce', ['tensor-reg', '51', 'ab'*100], 'scriptSig'),
            ): reject(label, 'create_mining_work_unit', params, reason)
            for count in (0, -1, 1000000):
                reject('invalid-batch-'+str(count), 'create_mining_work_units_batch', ['tensor-reg', '51', count], 'out of range')
            first = rpc(name, 'create_mining_work_unit', 'tensor-reg', '51', '01')
            second = rpc(name, 'create_mining_work_unit', 'tensor-reg', '51', '01')
            batch = rpc(name, 'create_mining_work_units_batch', 'tensor-reg', '51', 3, '01')
            assert (batch['issued_count'], batch['units_truncated'], batch['capacity_reason']) == (2, 1, 'tracker_full')
            work = [first, second] + [{**{k:v for k,v in batch.items() if k != 'units'}, **u} for u in batch['units']]
            for item in work: validate_work(item, expected, active)
            assert len({item['req_id'] for item in work}) == 4
            assert len({item['header_prefix'] for item in work}) == 4
            assert len({item['header_prefix'][72:136] for item in work}) == 4  # distinct coinbase roots
            reject('capacity-exhausted', 'create_mining_work_unit', ['tensor-reg', '51'], 'capacity')
            assert rpc(name, 'getblockcount') == 0
            assert rpc(name, 'getconnectioncount') == 0
            results.append({'activeV4': active, 'rejected': rejected, 'workUnits': work,
                            'distinctCoinbaseRoots': 4, 'heightAfter': 0, 'peerCount': 0})
        finally:
            cmd('rm', '-f', name)
    report = {'format':'noet-native-work-issuance-v1', 'nodeImage':image, 'genesis':expected,
              'validationMode':'real', 'externalValidationEnabled':True, 'verifierConnected':False, 'cases':results,
              'blocksAccepted':0, 'rewardsEarned':0, 'productionReady':False,
              'scope':'Node-issued work on disposable lab identities, not real-work block acceptance'}
    with output.open('x') as stream: json.dump(report, stream, indent=2); stream.write('\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.out), indent=2))
