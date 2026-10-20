"""Run the actual CPU verifier on bounded experimental evidence, never a network."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time


def run(record, output, mutation=None):
    import torch
    import pfunpack
    from enhanced_logger import create_logger
    from proof_verifier import ProofVerifier
    from noet.verifier_entry import require_challenge_binding
    from noet.model_runtime import checked_model_directory
    from noet.vdf import verify
    import chiavdf

    if output.exists():
        raise FileExistsError(output)
    with record.open('rb') as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('Experimental evidence exceeds size limit')
    decoded = pfunpack.unpack_mining_response(raw)
    proof = decoded['pow_blob']
    model, revision = proof['model_identifier'].split('@')
    checked_model_directory(model, revision)
    require_challenge_binding()
    if not verify(proof['block_hash'], proof['vdf'], proof['tick'], chiavdf.verify_from_hash):
        raise ValueError('Experiment requires a real valid VDF')
    header = bytes.fromhex(proof['header_prefix'])
    if len(header) != 76 or header[4:36].hex() != proof['block_hash']:
        raise ValueError('Experiment header binding mismatch')
    if proof['version'] != 2 or len(proof['chosen_tokens']) != 256:
        raise ValueError('This experiment is restricted to 256-token legacy evidence')
    if mutation == 'corrupt-logits':
        # Break the declared model outputs while preserving header/VDF binding.
        for row in proof['topk_logits']:
            row[0] += 50.0
    elif mutation is not None:
        raise ValueError('Unsupported experiment mutation')
    envelope = pfunpack.pack_validation_request({'hash_id': hashlib.sha256(raw).digest(),
        'validation_type': 2, 'request': {'kind': 'BlockValidation',
            'version': int.from_bytes(header[:4], 'little'), 'prev_block_hash': header[4:36],
            'timestamp': int.from_bytes(header[68:72], 'little'), 'difficulty': 0, 'pow_blob': proof}})
    torch.set_num_threads(3)
    torch.set_num_interop_threads(1)
    logger = create_logger(name='noet-cpu-full', reports_dir=str(output.parent / 'reports'))
    verifier = ProofVerifier(logger=logger)
    started = time.monotonic()
    result = {'proofSha256': hashlib.sha256(raw).hexdigest(), 'model': proof['model_identifier'],
              'proofVersion': 2, 'realVdfVerified': True, 'productionReady': False,
              'mutation': mutation, 'envelopeSha256': hashlib.sha256(envelope).hexdigest(),
              'streamed': os.environ.get('POW_V2_STREAMED') == '1',
              'scope': 'CPU legacy experimental replay; no current-rules or native block acceptance'}
    try:
        result['quickVerdict'] = int(verifier.quick_verify(envelope))
        result['fullVerdict'] = verifier.full_verify(envelope)
    except Exception as error:
        result['exceptionType'] = type(error).__name__
        result['exception'] = str(error)[:1000]
    result['elapsedSeconds'] = time.monotonic() - started
    result['modelLoaded'] = verifier.model is not None
    with output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proof', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--mutation', choices=['corrupt-logits'])
    args = parser.parse_args()
    result = run(args.proof, args.out, args.mutation)
    accepted = result.get('fullVerdict') == ('RED' if args.mutation else 'GREEN')
    if not args.mutation:
        accepted = accepted and result.get('quickVerdict') == 0
    raise SystemExit(0 if accepted and result['modelLoaded'] and 'exception' not in result else 2)
