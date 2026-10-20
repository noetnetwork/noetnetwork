"""Bounded input boundary for the pinned, non-recursive 1024-bit VDF verifier."""
import re

# Pinned bqfc.h: ((1024 + 31) // 32 * 3 + 4) bytes per form.
# Pinned verifier.h: recursion zero requires exactly two serialized forms.
FORM_BYTES = 100
PROOF_BYTES = 2 * FORM_BYTES


def verify(block_hash, proof, iterations, native_verify, discriminant_bits=1024):
    if discriminant_bits != 1024 or type(discriminant_bits) is not int:
        return False
    if type(iterations) is not int or not 0 <= iterations <= 2**64 - 1:
        return False
    if not isinstance(block_hash, str) or len(block_hash) != 64:
        return False
    if not isinstance(proof, str) or len(proof) != PROOF_BYTES * 2:
        return False
    if re.fullmatch(r'[0-9a-fA-F]{64}', block_hash) is None:
        return False
    if re.fullmatch(r'[0-9a-fA-F]{400}', proof) is None:
        return False
    try:
        return native_verify(bytes.fromhex(block_hash), bytes.fromhex(proof),
                             discriminant_bits, iterations, 0) is True
    except Exception:
        return False
