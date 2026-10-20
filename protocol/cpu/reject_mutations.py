"""Test actual verifier rejection of changed context and VDF, without a node."""
import argparse
import copy
import hashlib
import json
from pathlib import Path


def check(record, output):
    import pfunpack
    from enhanced_logger import create_logger
    from proof_verifier import ProofVerifier
    from noet.verifier_entry import require_challenge_binding

    if output.exists():
        raise FileExistsError(output)
    with record.open('rb') as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('Experimental evidence exceeds size limit')
    original = pfunpack.unpack_mining_response(raw)['pow_blob']
    require_challenge_binding()
    header = bytes.fromhex(original['header_prefix'])
    if len(header) != 76 or original['version'] != 2:
        raise ValueError('Expected bound legacy experimental evidence')
    logger = create_logger(name='noet-cpu-mutations', reports_dir=str(output.parent/'mutation-reports'))
    verifier = ProofVerifier(logger=logger)

    def envelope(proof, parent=header[4:36]):
        return pfunpack.pack_validation_request({'hash_id': hashlib.sha256(raw).digest(),
            'validation_type': 2, 'request': {'kind': 'BlockValidation',
                'version': int.from_bytes(header[:4], 'little'), 'prev_block_hash': parent,
                'timestamp': int.from_bytes(header[68:72], 'little'), 'difficulty': 0, 'pow_blob': proof}})

    if int(verifier.quick_verify(envelope(original))) != 0:
        raise AssertionError('Positive quick control failed')
    cases = {}
    for name in ('proof-parent', 'header-parent', 'short-header', 'outer-parent', 'corrupted-vdf', 'iterations'):
        proof = copy.deepcopy(original)
        parent = header[4:36]
        if name == 'proof-parent': proof['block_hash'] = 'cd'*32
        if name == 'header-parent': proof['header_prefix'] = (header[:4]+bytes.fromhex('cd'*32)+header[36:]).hex()
        if name == 'short-header': proof['header_prefix'] = header[:-1].hex()
        if name == 'outer-parent': parent = bytes.fromhex('cd'*32)
        if name == 'corrupted-vdf':
            changed = bytearray.fromhex(proof['vdf']); changed[-1] ^= 1
            proof['vdf'] = changed.hex()
        if name == 'iterations': proof['tick'] -= 1
        packed = envelope(proof, parent)
        quick = int(verifier.quick_verify(packed))
        if quick == 0:
            raise AssertionError('Mutation passed quick verification: '+name)
        case = {'quickVerdict': quick}
        # These four context errors must fail even when a caller skips Quick.
        if name in ('proof-parent', 'header-parent', 'short-header', 'outer-parent'):
            case['fullVerdict'] = verifier.full_verify(packed)
            if case['fullVerdict'] != 'RED' or verifier.model is not None:
                raise AssertionError('Context mutation reached model replay: '+name)
        cases[name] = case
    result = {'status': 'passed', 'proofSha256': hashlib.sha256(raw).hexdigest(),
              'positiveQuickControl': True, 'mutations': cases, 'modelLoaded': verifier.model is not None,
              'productionReady': False, 'scope': 'Actual verifier context/VDF rejection; no full positive or block acceptance'}
    with output.open('x') as stream:
        json.dump(result, stream, indent=2); stream.write('\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proof', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    check(args.proof, args.out)
