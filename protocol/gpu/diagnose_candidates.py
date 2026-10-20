"""Observe the unchanged Full verifier's numerical candidates on a captured proof.

Never publishes a verdict, changes precision, selects a new seed or changes a gate.
The reference replay result and the diagnostic report are separate artifacts.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace


def run(args):
    import torch
    from proof_verifier import ProofVerifier
    from replay import run as replay

    if args.out.exists() or args.replay_out.exists():
        raise FileExistsError('Use new diagnostic output files')
    record = json.loads(args.record.read_text())
    if record.get('requestType') != 2 or record.get('reusedExactEnvelope'):
        raise ValueError('Use an original computed Full record')
    source = args.record.with_name('request.bin')
    if hashlib.sha256(source.read_bytes()).hexdigest() != record['envelopeSha256']:
        raise ValueError('Captured envelope digest differs')
    captured = []
    original = ProofVerifier._verify_steps_from_logits_vectorized

    def observe(self, steps_logits, step_indices=None, *rest, **kwargs):
        result = original(self, steps_logits, step_indices, *rest, **kwargs)
        # Observe returned tensors only after the unchanged calculation.
        # No extra random draws, parameter changes or result mutation.
        steps = (torch.arange(steps_logits.shape[0], device=steps_logits.device)
                 if step_indices is None else step_indices)
        values = {key: result[key].detach().float().cpu().tolist() for key in
                  ('p_value', 'v4_severity', 'delta_mu', 'delta_raw', 'sigma_diag', 'saved_idx_sorted')}
        mean_a = torch.as_tensor(self.expected_lse, device=steps_logits.device)[steps, -1]
        mean_b = steps_logits[:, 0, :].float().mean(-1)
        captured.append({'candidate': kwargs.get('cand_name'), 'steps': steps.cpu().tolist(),
                         'reportedMean': mean_a.float().cpu().tolist(),
                         'referenceMean': mean_b.cpu().tolist(), **values})
        return result

    report = {'format': 'noet-gpu-candidate-diagnostic-v1', 'productionReady': False,
              'consensusVerified': False, 'scope': 'Observational replay of the recorded seed and original envelope; no changed gates, precision, mining or network verdict',
              'envelopeSha256': record['envelopeSha256'], 'replaySeed': record['replaySeed'],
              'sourceRecordSha256': hashlib.sha256(args.record.read_bytes()).hexdigest()}
    ProofVerifier._verify_steps_from_logits_vectorized = observe
    try:
        report['originalAcceptancePassed'] = replay(SimpleNamespace(request=source,
            sha256=record['envelopeSha256'], seed=record['replaySeed'], case='bound', out=args.replay_out))
    finally:
        ProofVerifier._verify_steps_from_logits_vectorized = original
    report['candidates'] = captured
    if captured:
        if any(row['steps'] != captured[0]['steps'] for row in captured):
            raise ValueError('Candidate step coverage differs; no inferred selection')
        selected = []
        for i, step in enumerate(captured[0]['steps']):
            # Same first-maximum tie handling as torch.argmax, for reporting only.
            row = max(captured, key=lambda item: item['p_value'][i])
            residuals = [abs(value - row['delta_mu'][i]) for value in row['delta_raw'][i]]
            worst = max(range(len(residuals)), key=residuals.__getitem__)
            selected.append({'step': step, 'candidate': row['candidate'],
                'pValue': row['p_value'][i], 'severity': row['v4_severity'][i],
                'deltaMean': row['delta_mu'][i], 'reportedMean': row['reportedMean'][i],
                'referenceMean': row['referenceMean'][i],
                'worstToken': int(row['saved_idx_sorted'][i][worst]),
                'rawDifference': row['delta_raw'][i][worst], 'absoluteResidual': residuals[worst]})
        report['worstSelectedSteps'] = sorted(selected, key=lambda row: row['severity'][0], reverse=True)[:12]
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value): return None
        if isinstance(value, list): return [finite(item) for item in value]
        if isinstance(value, dict): return {key: finite(item) for key, item in value.items()}
        return value
    report = finite(report)
    with args.out.open('x') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'candidates'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--replay-out', type=Path, required=True)
    run(parser.parse_args())
