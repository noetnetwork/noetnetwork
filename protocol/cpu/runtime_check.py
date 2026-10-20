"""Exercise compiled verifier boundaries in the isolated CPU research image."""
import argparse
import json
from pathlib import Path
import random


def check(output):
    import chiavdf
    import pfunpack
    from noet.vdf import verify
    from utils import credit_v4_native, sunset_native

    if not __debug__:
        raise RuntimeError('Acceptance requires assertions')
    rng = random.Random(20261011)
    malformed = [b'', b'\0', b'\0'*7, b'\xff'*8]
    malformed += [rng.randbytes(size) for size in (8, 16, 32, 64, 128, 256, 1024, 8192) for _ in range(16)]
    for raw in malformed:
        try:
            pfunpack.unpack_validation_request(raw)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Malformed validation envelope was parsed')
    missing = pfunpack.pack_validation_request({'hash_id': bytes(32), 'request': {'kind': 'BlockValidation'}})
    try:
        pfunpack.unpack_validation_request(missing)
    except RuntimeError as error:
        assert 'no pow_blob' in str(error)
    else:
        raise AssertionError('Missing proof accepted')
    parent = bytes.fromhex('ab'*32)
    proof = chiavdf.prove_from_hash(parent, 1024, 10_000)
    assert verify(parent.hex(), proof.hex(), 10_000, chiavdf.verify_from_hash)
    bad = [(parent.hex(), proof.hex(), 9999), ('cd'*32, proof.hex(), 10_000)]
    for offset in range(len(proof)):
        changed = bytearray(proof)
        changed[offset] ^= 1
        bad.append((parent.hex(), changed.hex(), 10_000))
    for challenge, value, iterations in bad:
        assert not verify(challenge, value, iterations, chiavdf.verify_from_hash)
    record = {'status': 'passed', 'malformedEnvelopesRejected': len(malformed),
              'missingProofRejected': True, 'realVdfAccepted': True,
              'invalidVdfsRejected': len(bad), 'creditKernel': credit_v4_native.self_test(),
              'sunsetKernel': sunset_native.self_test(), 'fullInferenceAccepted': False,
              'scope': 'Actual compiled parser, VDF and arithmetic libraries; no full inference or block acceptance'}
    with output.open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    check(parser.parse_args().out)
