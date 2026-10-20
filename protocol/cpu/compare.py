#!/usr/bin/env python3
"""Compare the deterministic fields of two local CPU experiment records."""
import argparse
from pathlib import Path
import sys


def compare(first, second, schemas):
    sys.path.insert(0, str(schemas))
    from proof.MiningResponse import MiningResponse
    import numpy as np
    records = []
    for path in (first, second):
        with path.open('rb') as stream:
            data = stream.read(2 * 1024 * 1024 + 1)
        if not 1 <= len(data) <= 2 * 1024 * 1024:
            raise ValueError('Invalid experiment record size')
        response = MiningResponse.GetRootAs(data, 0)
        proof = response.PowBlob()
        if response.ReqId() != 9201 or proof is None or proof.Version() != 2:
            raise ValueError('Unexpected experiment record')
        if proof.ChosenTokensLength() != 256 or not 1 <= proof.PromptTokensLength() <= 768:
            raise ValueError('Unexpected experiment dimensions')
        records.append(proof)
    a, b = records
    for name in ('Version', 'ModelIdentifier', 'ComputePrecision', 'Temperature', 'TopK', 'TopP'):
        if getattr(a, name)() != getattr(b, name)():
            raise ValueError('Evidence differs: ' + name)
    for name in ('Hash', 'Target', 'BlockHash', 'HeaderPrefix', 'ChosenTokens', 'PromptTokens',
                 'ChosenProbs', 'SamplingU', 'SoftmaxNormalizers'):
        if not np.array_equal(getattr(a, name+'AsNumpy')(), getattr(b, name+'AsNumpy')()):
            raise ValueError('Evidence differs: ' + name)
    for name in ('TopkIndices', 'TopkLogits', 'LogsumexpStats'):
        if getattr(a, name+'Length')() != 256 or getattr(b, name+'Length')() != 256:
            raise ValueError('Unexpected evidence row count')
        for i in range(256):
            left, right = getattr(a, name)(i), getattr(b, name)(i)
            if not 1 <= left.ValuesLength() <= 70 or not 1 <= right.ValuesLength() <= 70 or not np.array_equal(left.ValuesAsNumpy(), right.ValuesAsNumpy()):
                raise ValueError('Evidence differs: ' + name)
    return 'Token, sampling, logit and commitment fields match exactly. This is not consensus verification.'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--first', type=Path, required=True)
    parser.add_argument('--second', type=Path, required=True)
    parser.add_argument('--schemas', type=Path, required=True)
    args = parser.parse_args()
    print(compare(args.first.resolve(), args.second.resolve(), args.schemas.resolve()))
