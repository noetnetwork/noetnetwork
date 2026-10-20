"""Audit every recorded Full result, including retries, without rerunning inference."""
import argparse
import hashlib
import json
from pathlib import Path

VIEWS = ['1', '2', '5', '10', '20', 'spda']


def audit(directory, *, require_captures=False):
    report = json.loads((directory / 'result.json').read_text())
    failures = []
    if report.get('passed') is not True:
        failures.append('Native acceptance did not finish successfully')
    blocks = report.get('acceptedBlocks', [])
    heights = [block['height'] for block in blocks]
    if not heights or heights != list(range(1, report.get('finalHeight', 0) + 1)):
        failures.append('Accepted blocks are not the complete consecutive chain')
    computed, reused = [], []
    for path in sorted((directory / 'verifier').glob('*/result.json')):
        row = json.loads(path.read_text())
        if row.get('requestType') != 2:
            continue
        raw = path.with_name('request.bin').read_bytes()
        if hashlib.sha256(raw).hexdigest() != row.get('envelopeSha256'):
            failures.append(f'{path.parent.name}: Native envelope digest differs')
        if row.get('reusedExactEnvelope'):
            reused.append((path, row))
            continue
        computed.append((path, row))
        if (row.get('fullVerdict') != 'GREEN' or row.get('responseCode') != 6
                or row.get('exception') or not row.get('responseSent')):
            failures.append(f'{path.parent.name}: Full did not complete GREEN')
        if row.get('height', 0) >= 3:
            detector = row.get('detector') or {}
            if (row.get('replayViews') != VIEWS or row.get('detectorRecordStatus') != 'scored'
                    or detector.get('detector_status') != 'required'
                    or detector.get('detector_decision') != 'NO_VIOLATION'):
                failures.append(f'{path.parent.name}: Required detector evidence is incomplete')
        if require_captures:
            capture = path.with_name('detector.npz')
            expected = row.get('detectorCapture') or {}
            if (row.get('detectorCaptureError') or not capture.is_file()
                    or hashlib.sha256(capture.read_bytes()).hexdigest() != expected.get('sha256')):
                failures.append(f'{path.parent.name}: Detector capture missing or changed')
    by_directory = {path.parent.name: row for path, row in computed}
    for path, row in reused:
        prior = by_directory.get(row.get('computedResultDirectory'), {})
        if (not row.get('responseSent') or row.get('exception')
                or any(prior.get(key) != row.get(key) for key in ('envelopeSha256', 'height', 'ruleset', 'fullVerdict', 'responseCode'))):
            failures.append(f'{path.parent.name}: Retry does not match its computed verdict')
    if set(heights) != {row.get('height') for _, row in computed}:
        failures.append('Computed Full coverage differs from the accepted chain')
    successes = [attempt for attempt in report.get('attempts', []) if attempt.get('nativeFullValidation')]
    if len(successes) != len(blocks) or any(
            attempt.get('duplicateIsIdempotent') is not True
            or attempt['nativeFullValidation'].get('full_status') != 'green' for attempt in successes):
        failures.append('Duplicate submission acceptance is incomplete')
    return {
        'format': 'noet-gpu-evidence-audit-v1', 'passed': not failures,
        'productionReady': False, 'acceptedHeights': heights,
        'computedFullRequests': len(computed), 'exactRetryResponses': len(reused),
        'requiredDetectorRequests': sum(row.get('height', 0) >= 3 for _, row in computed),
        'capturesRequired': require_captures, 'failures': failures,
        'scope': 'Recorded private acceptance only; every computed Full result audited, no seed selection or recalibration',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--require-captures', action='store_true')
    args = parser.parse_args()
    result = audit(args.run, require_captures=args.require_captures)
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result))
    raise SystemExit(0 if result['passed'] else 2)
