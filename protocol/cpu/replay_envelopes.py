"""Replay node-emitted Full requests recorded by another verifier, on CPU.

Research only (CPU-VALIDATION.md, step 1): each item is a directory holding the
native `request.bin` and that verifier's `result.json`, as `realwork/verify.py`
writes them. The same envelope is verified again here with the recorded
replay seed (or a fixed one), and the verdict, logit severity and detector
decision are compared. Prompt calibration stays shadow; nothing is sent to a
node. Results are written per envelope, so an interrupted run resumes.

  python /cpu/replay_envelopes.py --corpus /corpus --out /out [--limit N] [--seed N]
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import time


def corpus(root):
    """Distinct completed Full verdicts, in height order."""
    seen, items = set(), []
    for result_path in root.glob('*/result.json'):
        try:
            recorded = json.loads(result_path.read_text())
        except ValueError:
            continue
        request = result_path.parent / 'request.bin'
        if recorded.get('fullVerdict') not in ('GREEN', 'AMBER', 'RED') or recorded.get('reusedExactEnvelope'):
            continue
        if not request.exists() or recorded['envelopeSha256'] in seen:
            continue
        if hashlib.sha256(request.read_bytes()).hexdigest() != recorded['envelopeSha256']:
            raise ValueError(f'{request} does not match its recorded hash')
        seen.add(recorded['envelopeSha256'])
        items.append((recorded.get('height') or 0, int(result_path.parent.name), request, recorded))
    return [(request, recorded) for _, _, request, recorded in sorted(items, key=lambda i: i[:2])]


def detector_summary(detector):
    if not isinstance(detector, dict):
        return None
    joint = detector.get('joint') or {}
    return {'status': detector.get('detector_status'), 'decision': detector.get('detector_decision'),
            'eligible': joint.get('eligible_total'),
            'flagged': {name: model.get('flagged_eligible_positions')
                        for name, model in (joint.get('models') or {}).items()}}


def replay(request, recorded, seed):
    import numpy as np
    import random
    import torch
    import pfunpack
    from proof_verifier import ProofVerifier
    from utils.request_context import read_block_validation_fields

    raw = request.read_bytes()
    unpacked = pfunpack.unpack_validation_request(raw)
    if unpacked['request_type'] != 1 or unpacked['validation_type'] != 2:
        raise ValueError('Not a native Full request')
    height, ruleset = read_block_validation_fields(raw)
    seed = recorded.get('replaySeed') if seed is None else seed
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    verifier = ProofVerifier(logger=LOGGER)
    started = time.monotonic()
    out = {'height': height, 'ruleset': ruleset, 'replaySeed': seed, 'promptTokens': None}
    try:
        out['fullVerdict'] = verifier.full_verify(raw)
    except Exception as error:  # an abstention is recorded, never turned into a verdict
        out.update(exceptionType=type(error).__name__, exception=str(error)[:1500])
    out['elapsedSeconds'] = round(time.monotonic() - started, 2)
    gate = getattr(verifier, '_last_full_gate', None) or {}
    out['maxLogitSeverity'] = gate.get('max_logit_severity')
    out['maxAbsProbResidual'] = gate.get('max_abs_prob_residual')
    out['detector'] = detector_summary(getattr(verifier, '_poi_detector_result', None))
    score = (getattr(verifier, '_last_prompt_gibberish_decision', None) or {}).get('score') or {}
    out['promptTokens'] = score.get('n_prompt_tokens')
    out['modelDevices'] = sorted({str(p.device) for p in verifier.model.parameters()}) if verifier.model else None
    out['peakRssMiB'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    return out


def compare(recorded, cpu):
    """Verdicts always; the detector only where it was required (ruleset 4). An
    advisory detector may finish after the verdict, so there is nothing to compare."""
    gpu_detector = detector_summary(recorded.get('detector')) or {}
    agree = {'verdict': cpu.get('fullVerdict') == recorded['fullVerdict']}
    if gpu_detector.get('status') == 'required':
        agree['detectorDecision'] = (cpu['detector'] or {}).get('decision') == gpu_detector.get('decision')
        agree['detectorFlags'] = (cpu['detector'] or {}).get('flagged') == gpu_detector.get('flagged')
    return agree


def summarize(out):
    rows = [json.loads(p.read_text()) for p in sorted(out.glob('*/result.json'))]
    severities = [r['cpu']['maxLogitSeverity'] for r in rows if r['cpu'].get('maxLogitSeverity') is not None]
    times = sorted(r['cpu']['elapsedSeconds'] for r in rows)
    summary = {
        'replayed': len(rows),
        'recordedVerdicts': dict(sorted({v: sum(r['recorded']['fullVerdict'] == v for r in rows)
                                         for v in {r['recorded']['fullVerdict'] for r in rows}}.items())),
        'cpuVerdicts': dict(sorted({str(v): sum(r['cpu'].get('fullVerdict') == v for r in rows)
                                    for v in {r['cpu'].get('fullVerdict') for r in rows}}.items())),
        'verdictAgreement': sum(r['agree']['verdict'] for r in rows),
        'detectorRequired': sum('detectorDecision' in r['agree'] for r in rows),
        'detectorDecisionAgreement': sum(r['agree'].get('detectorDecision', False) for r in rows),
        'detectorFlagAgreement': sum(r['agree'].get('detectorFlags', False) for r in rows),
        'abstentions': sum('exceptionType' in r['cpu'] for r in rows),
        'maxLogitSeverity': max(severities) if severities else None,
        'medianLogitSeverity': sorted(severities)[len(severities) // 2] if severities else None,
        'logitSeverityCap': 12.0,
        'medianSeconds': times[len(times) // 2] if times else None,
        'maxSeconds': times[-1] if times else None,
        'disagreements': [r['envelopeSha256'] for r in rows if not all(r['agree'].values())],
        'productionReady': False, 'promptCalibrationApproved': False,
    }
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main():
    global LOGGER
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--seed', type=int, help='one fixed seed instead of each recorded replaySeed')
    parser.add_argument('--threads', type=int, default=3)
    args = parser.parse_args()
    import torch
    import detector_gate
    from enhanced_logger import create_logger
    from noet.verifier_entry import require_challenge_binding

    require_challenge_binding()
    os.environ['POW_PROMPT_GIBBERISH_GATE'] = 'shadow'
    torch.set_num_threads(args.threads); torch.set_num_interop_threads(1)
    args.out.mkdir(parents=True, exist_ok=True)
    LOGGER = create_logger(name='noet-cpu-corpus', reports_dir=str(args.out / 'reports'))
    detector_gate.warm_on_start(LOGGER)
    if not detector_gate.detector_ready():
        raise RuntimeError('Sealed detector unavailable')
    items = corpus(args.corpus)[:args.limit]
    print(json.dumps({'corpus': len(items)}), flush=True)
    try:
        for request, recorded in items:
            target = args.out / recorded['envelopeSha256'][:16]
            if (target / 'result.json').exists():
                continue
            cpu = replay(request, recorded, args.seed)
            row = {'envelopeSha256': recorded['envelopeSha256'], 'source': request.parent.name,
                   'recorded': {k: recorded.get(k) for k in ('fullVerdict', 'height', 'ruleset', 'replaySeed',
                                                            'modelDevices', 'elapsedSeconds')},
                   'cpu': cpu, 'agree': compare(recorded, cpu)}
            target.mkdir(exist_ok=True)
            (target / 'result.json').write_text(json.dumps(row, indent=2, default=str) + '\n')
            print(json.dumps({'height': cpu['height'], 'gpu': recorded['fullVerdict'], 'cpu': cpu.get('fullVerdict'),
                              'severity': cpu['maxLogitSeverity'], 'seconds': cpu['elapsedSeconds'],
                              'agree': row['agree']}), flush=True)
    finally:
        detector_gate.runtime().close()
    print(json.dumps(summarize(args.out)), flush=True)


LOGGER = None

if __name__ == '__main__':
    main()
