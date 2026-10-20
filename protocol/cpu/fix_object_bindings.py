"""Repair a narrow flatc Python object-API namespace collision in test bindings.

The generated ProofT._UnPack parameter is named `proof`, shadowing the `proof`
package when unpacking nested arrays. Only the three qualified factory calls
are changed. Field access, schema, field types and serialization stay intact.
"""
import argparse
from pathlib import Path


def fix(directory):
    path = directory/'proof/Proof.py'
    source = path.read_text()
    alias = 'import proof as _proof_schema\n'
    replacements = {'proof.FloatArray.FloatArrayT.InitFromObj': 2,
                    'proof.UIntArray.UIntArrayT.InitFromObj': 1}
    if alias in source:
        raise ValueError('Bindings are already repaired; do not transform them twice')
    for name, expected in replacements.items():
        if source.count(name) != expected:
            raise ValueError('Unexpected generated binding; review before repair')
    for name in replacements:
        source = source.replace(name, '_proof_schema.'+name.removeprefix('proof.'))
    path.write_text(alias+source)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bindings', type=Path, required=True)
    fix(parser.parse_args().bindings)
