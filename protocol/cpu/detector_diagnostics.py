"""Offline feature diagnosis of captured CPU research replays, never a verdict."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

LIMIT = 16 * 1024 * 1024


def checked_archive(path, expected_sha256):
    with path.open('rb') as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Capture size or SHA-256 mismatch')
    import io
    source = io.BytesIO(raw)
    with zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        names = [item.filename for item in members]
        if (not members or len(members) > 100 or len(set(names)) != len(names)
                or sum(item.file_size for item in members) > LIMIT
                or any(not name.endswith('.npy') or '/' in name or '\\' in name for name in names)):
            raise ValueError('Unsupported capture archive')
    source.seek(0)
    return source


def diagnose(capture, report, output):
    import numpy as np
    from detector_gate import verify_bundle_against_policy
    from poi_detector.advisory_bundle import validate
    if output.exists(): raise FileExistsError(output)
    with report.open('rb') as stream: raw = stream.read(128 * 1024 + 1)
    if len(raw) > 128 * 1024: raise ValueError('Oversized replay report')
    record = json.loads(raw)
    source = checked_archive(capture, record['detectorCapture']['sha256'])
    with np.load(source, allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in archive.files if key != '__meta_json'}
        meta = json.loads(str(archive['__meta_json']))
    bundle = Path(os.environ['POI_DETECTOR_BUNDLE'])
    digest = verify_bundle_against_policy(str(bundle))
    validate(bundle)
    sys.path.insert(0, str(bundle.parent/'code'))
    from poi_union_release import live_features as live, union_features as features
    from poi_union_release.value_rank import TOKEN_CHANNELS, BUCKET_CHANNELS
    if arrays['declared_ids'].shape != (256, 70) or arrays['cand_value_top50_ids'].shape != (6, 256, 50):
        raise ValueError('Expected the recorded research profile')
    eligible = record['detectorSteps']['eligible_positions']
    if len(set(eligible)) != len(eligible) or any(type(p) is not int or not 1 <= p <= 256 for p in eligible):
        raise ValueError('Invalid eligible positions')
    snapshot = {'meta':meta, 'arrays':arrays}
    rows = []
    for position in eligible:
        step = position - 1
        tokens = set(int(t) for t in arrays['declared_ids'][step] if t >= 0)
        tokens.update(int(t) for t in arrays['cand_value_top50_ids'][:, step].reshape(-1) if t >= 0)
        item = {'position':position, 'unionTokens':len(tokens)}
        try:
            # Isolate each row so a generator failure cannot mask later causes.
            row = features.vr_row(next(live.rows(snapshot, vocab_size=meta['vocab_size'], steps=[step])))
            tensors = features.model_tensors(row, arrays['declared_values'][step], arrays['declared_ids'][step],
                token_channels=TOKEN_CHANNELS, bucket_channels=BUCKET_CHANNELS)
            slots = features.step_slots(tensors['union_ids'], arrays['declared_ids'][step],
                arrays['declared_scores0'][step], arrays['chosen'][step])
            if not tensors['tok_mask'][slots['chosen_idx']]: raise ValueError('Chosen token is absent')
            item['featureStatus'] = 'built'
        except Exception as error:
            item.update(featureStatus='refused', exceptionType=type(error).__name__, reason=str(error)[:400])
        rows.append(item)
    result = {'format':'noet-detector-feature-diagnosis-v1', 'captureSha256':record['detectorCapture']['sha256'],
        'proofSha256':record['proofSha256'], 'replaySeed':record.get('replaySeed'),
        'manifestSha256':digest, 'fixedTokenWidth':features.TOKEN_WIDTH,
        'eligibleCount':len(rows), 'maxEligibleUnionTokens':max((r['unionTokens'] for r in rows),default=0),
        'refused':[r for r in rows if r['featureStatus']=='refused'], 'rows':rows,
        'productionReady':False, 'scope':'Exact sealed feature construction on captured research inputs; no model score, threshold change or verdict'}
    with output.open('x') as stream: json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for flag in ('capture','report','out'): parser.add_argument('--'+flag,required=True,type=Path)
    args=parser.parse_args()
    diagnose(args.capture,args.report,args.out)
