"""Research-only v4 replay with explicit constructed validation context.

Requires object-API bindings generated from the pinned schemas at /schemas.
It can cross-check a recorded lab-node work unit, but never receives a
node-emitted validation request or establishes block acceptance.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import sys
import time


def replay_passed(result):
    """Match the diagnostic to its actual profile, never promote advisory to required."""
    complete = (result.get('quickVerdict') == 0 and result.get('fullVerdict') == 'GREEN'
        and result.get('modelLoaded') is True and 'exception' not in result
        and result.get('replayViews') == ['1', '2', '5', '10', '20', 'spda']
        and result.get('detectorRecordStatus') == 'scored'
        and result.get('detectorSupport', {}).get('six_view_cloud_present', {}).get('status') == 'evaluated')
    if not complete:
        return False
    if result.get('ruleset') == 4:
        return result.get('detector', {}).get('detector_status') == 'required'
    # Only the explicitly cross-checked recorded-node fixture uses early v4.
    # Its sealed scorer is advisory and may finish asynchronously at close.
    # A successful diagnostic here is NOT mandatory-detector acceptance.
    return (result.get('ruleset') == 2 and result.get('nodeIssuedHeader') is True
        and result.get('nodeEmittedValidationRequest') is False
        and re.fullmatch('[a-f0-9]{64}', result.get('nodeWorkRecordSha256', '')) is not None)


def capture_detector_inputs(snapshot, output):
    """Write bounded numeric research inputs without pickle or production data."""
    import numpy as np
    from poi_detector.advisory import _cpu_copy, _size
    if _size(snapshot['arrays']) > 16 * 1024 * 1024:
        raise ValueError('Detector research snapshot exceeds 16 MiB')
    arrays = {k: np.asarray(v) for k, v in _cpu_copy(snapshot['arrays']).items()}
    if any(a.dtype.hasobject for a in arrays.values()):
        raise ValueError('Object arrays are not supported in diagnostic evidence')
    meta = {k: snapshot['meta'][k] for k in ('advisory_value_contract', 'vocab_size',
        'replay_eligibility_contract', 'top_k', 'temperature', 'top_p', 'repetition_penalty',
        'sampling_stop_token_ids', 'head_declared', 'head_verifier', 'window_size') if k in snapshot['meta']}
    arrays['__meta_json'] = np.asarray(json.dumps(meta))
    with output.open('xb') as stream:
        np.savez_compressed(stream, **arrays)
    return {'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'bytes': output.stat().st_size}


def run(record, output, context_case='bound', node_record=None, replay_seed=None, capture_detector=None):
    sys.path.insert(0, '/schemas')
    import flatbuffers
    import pfunpack
    import torch
    from numpy.testing import assert_equal
    import chiavdf
    from proof.MiningResponse import MiningResponse
    from proof.Proof import ProofT
    from proof.BlockValidation import BlockValidationT
    from proof.ValidationRequest import ValidationRequestT
    from enhanced_logger import create_logger
    import detector_gate
    from proof_verifier import ProofVerifier
    from noet.model_runtime import checked_model_directory
    from noet.verifier_entry import require_challenge_binding
    from noet.vdf import verify
    from utils.strict_validity import read_block_validation_fields

    if output.exists(): raise FileExistsError(output)
    if capture_detector is not None and capture_detector.exists(): raise FileExistsError(capture_detector)
    with record.open('rb') as stream: raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024: raise ValueError('Oversized experimental record')
    proof = pfunpack.unpack_mining_response(raw)['pow_blob']
    if proof['version'] != 4: raise ValueError('Expected version-4 evidence')
    model, revision = proof['model_identifier'].split('@')
    checked_model_directory(model, revision)
    require_challenge_binding()
    if not verify(proof['block_hash'], proof['vdf'], proof['tick'], chiavdf.verify_from_hash):
        raise ValueError('A real valid VDF is required')
    header = bytes.fromhex(proof['header_prefix'])
    if len(header) != 76 or header[4:36].hex() != proof['block_hash']:
        raise ValueError('Header/parent binding mismatch')
    height, ruleset = 1, 4
    node_record_sha = None
    if node_record is not None:
        from node_work import read_job
        job, node_record_sha = read_job(node_record)
        if context_case != 'bound' or job['header_prefix'] != header.hex() or MiningResponse.GetRootAs(raw, 0).ReqId() != job['req_id']:
            raise ValueError('Proof is not bound to the recorded node work unit')
        # The disposable runner sets X=1, with penalty/sunset disabled.
        # ValidationRulesetForHeight selects strict-v4 (2) for that profile.
        # This is still a constructed envelope, not one emitted by the node.
        height, ruleset = job['height'], 2
    block = BlockValidationT()
    block.version = int.from_bytes(header[:4], 'little')
    block.prevBlockHash = list(header[4:36])
    block.merkleRoot = list(header[36:68])
    block.timestamp = int.from_bytes(header[68:72], 'little')
    block.bits = block.adjustedBits = int.from_bytes(header[72:76], 'little')
    block.difficulty = 60_000_000
    block.height, block.ruleset = height, ruleset
    if context_case == 'missing-height': block.height = 0
    if context_case == 'missing-ruleset': block.ruleset = 0
    if context_case == 'wrong-parent': block.prevBlockHash = list(bytes.fromhex('cd'*32))
    if context_case == 'unsupported-ruleset': block.ruleset = 5
    block.powBlob = ProofT.InitFromObj(MiningResponse.GetRootAs(raw, 0).PowBlob())
    request = ValidationRequestT()
    request.hashId = list(hashlib.sha256(raw).digest())
    request.validationType, request.requestType, request.request = 2, 1, block
    builder = flatbuffers.Builder(1024)
    builder.ForceDefaults(context_case not in ('missing-height', 'missing-ruleset'))
    builder.Finish(request.Pack(builder))
    envelope = bytes(builder.Output())
    assert_equal(pfunpack.unpack_validation_request(envelope)['request']['pow_blob'], proof,
                 err_msg='Schema serialization changed the proof evidence')
    expected = {'bound':(height, ruleset), 'wrong-parent':(1, 4), 'missing-height':(None, 4),
                'missing-ruleset':(1, None), 'unsupported-ruleset':(1, 5)}[context_case]
    if read_block_validation_fields(envelope) != expected:
        raise ValueError('Serialized ruleset context was lost')
    torch.set_num_threads(3)
    torch.set_num_interop_threads(1)
    import random
    import secrets
    import numpy as np
    seed_source = 'explicit' if replay_seed is not None else 'random-diagnostic'
    replay_seed = secrets.randbits(32) if replay_seed is None else replay_seed
    random.seed(replay_seed)
    np.random.seed(replay_seed)
    torch.manual_seed(replay_seed)
    logger = create_logger(name='noet-cpu-v4', reports_dir=str(output.parent/'v4-reports'))
    verifier = ProofVerifier(logger=logger)
    started = time.monotonic()
    result = {'proofSha256': hashlib.sha256(raw).hexdigest(), 'envelopeSha256': hashlib.sha256(envelope).hexdigest(),
              'model': proof['model_identifier'], 'proofVersion': 4, 'height': expected[0], 'ruleset': expected[1],
              'contextCase': context_case,
              'streamed': os.environ.get('POW_V2_STREAMED') == '1',
              'difficulty': block.difficulty, 'realVdfVerified': True, 'productionReady': False,
              'scope': 'Synthetic ruleset-4 CPU research; no trusted node context, native block or network acceptance'}
    result['replaySeed'] = replay_seed
    result['replaySeedSource'] = seed_source
    result['torchInitialSeed'] = int(torch.initial_seed())
    result['cpuFullLogitsRequested'] = os.environ.get('NOET_CPU_FULL_LOGITS') == '1'
    if node_record_sha is not None:
        result.update(nodeWorkRecordSha256=node_record_sha, nodeIssuedHeader=True,
            nodeEmittedValidationRequest=False, registeredModel=False, registeredDifficulty=False,
            scope='Recorded lab-node header/height with constructed ruleset-2 envelope and research model/difficulty; no native block acceptance')
    score = None
    try:
        if context_case == 'bound':
            result['detectorStartup'] = detector_gate.warm_on_start(logger)
            if not detector_gate.detector_ready():
                raise RuntimeError('Required detector failed startup readiness')
            # Observe the real scorer without replacing its inputs or result.
            # This distinguishes complete replay from merely loading a bundle.
            runtime = detector_gate.runtime()
            score = runtime.score
            def observe_score(snapshot, **kwargs):
                result['replayViews'] = [str(name) for name in snapshot['arrays'].get('cand_names', [])]
                record = score(snapshot, **kwargs)
                result['detectorRecordStatus'] = record.get('status')
                result['detectorSupport'] = record.get('checks')
                result['detectorFeatureRefusals'] = record.get('feature_refusals', [])
                result['detectorSteps'] = record.get('steps')
                if capture_detector is not None:
                    try:
                        result['detectorCapture'] = capture_detector_inputs(snapshot, capture_detector)
                    except Exception as error:
                        result['detectorCaptureError'] = type(error).__name__ + ': ' + str(error)[:300]
                return record
            runtime.score = observe_score
        result['quickVerdict'] = int(verifier.quick_verify(envelope))
        result['fullVerdict'] = verifier.full_verify(envelope)
    except Exception as error:
        result['exceptionType'], result['exception'] = type(error).__name__, str(error)[:1000]
    finally:
        runtime = detector_gate.runtime()
        if runtime is not None:
            if score is not None:
                runtime.score = score
            runtime.close()
    result['elapsedSeconds'] = time.monotonic() - started
    result['modelLoaded'] = verifier.model is not None
    detector = getattr(verifier, '_poi_detector_result', None)
    if isinstance(detector, dict):
        result['detector'] = {key: detector[key] for key in ('detector_status', 'detector_decision', 'rule_digest', 'bundle_manifest_sha256', 'seconds', 'joint', 'activation', 'verdict_altered') if key in detector}
    with output.open('x') as stream: json.dump(result, stream, indent=2); stream.write('\n')
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proof', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--node-record', type=Path, help='Optional native-work smoke record; never represents a node-emitted validation request')
    parser.add_argument('--replay-seed', type=int, metavar='UINT32',
                        help='Diagnostic seed for controlled comparisons, not an acceptance-policy override')
    parser.add_argument('--capture-detector', type=Path, help='Optional exclusive NPZ output for bounded offline feature diagnostics')
    parser.add_argument('--context-case', choices=('bound', 'wrong-parent', 'missing-height', 'missing-ruleset', 'unsupported-ruleset'), default='bound')
    args = parser.parse_args()
    if args.replay_seed is not None and not 0 <= args.replay_seed < 2**32:
        parser.error('--replay-seed must be a uint32')
    result = run(args.proof, args.out, args.context_case, args.node_record, args.replay_seed, args.capture_detector)
    if args.context_case == 'bound':
        passed = replay_passed(result)
    elif args.context_case == 'wrong-parent':
        passed = result.get('quickVerdict') == 1 and result.get('fullVerdict') == 'RED' and not result['modelLoaded'] and 'exception' not in result
    else:
        expected_error = 'UnsupportedRulesetError' if args.context_case == 'unsupported-ruleset' else 'RulesetContextError'
        passed = result.get('exceptionType') == expected_error and not result['modelLoaded'] and 'fullVerdict' not in result
    raise SystemExit(0 if passed else 2)
