"""Check actual detector artifact rejection without loading changed code/weights."""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile


def check(output):
    from detector_gate import verify_bundle_against_policy, DetectorBundleInvalid
    from poi_detector.advisory_bundle import validate
    source = Path(os.environ['POI_DETECTOR_BUNDLE'])
    manifest_digest = verify_bundle_against_policy(str(source))
    validate(source)
    rejected = []
    with tempfile.TemporaryDirectory(prefix='noet-detector-boundary-') as folder:
        base = Path(folder)
        for case in ('changed-weight', 'unresolved-pointer', 'changed-code', 'changed-threshold', 'extra-code'):
            root = base/case
            shutil.copytree(source.parent, root)
            manifest = root/source.name
            data = json.loads(manifest.read_text())
            if case == 'changed-weight':
                path = root/data['weights']
                value = bytearray(path.read_bytes()); value[-1] ^= 1
                path.write_bytes(value)
            elif case == 'unresolved-pointer':
                (root/data['weights']).write_text('version https://git-lfs.github.com/spec/v1\n')
            elif case == 'changed-code':
                with (root/data['backend']).open('a') as stream: stream.write('\n# changed\n')
            elif case == 'changed-threshold':
                data['threshold'] = 0.0
                manifest.write_text(json.dumps(data))
            else:
                (root/'extra.py').write_text('# unlisted executable dependency\n')
            try:
                verify_bundle_against_policy(str(manifest))
                validate(manifest)
            except (ValueError, DetectorBundleInvalid):
                rejected.append(case)
            else:
                raise AssertionError('Changed detector artifact was accepted: '+case)
    result = {'status':'passed', 'validBundleAccepted':True, 'manifestSha256':manifest_digest,
              'rejected':rejected, 'changedCodeOrWeightsExecuted':False,
              'scope':'Actual sealed-detector integrity/policy validation; no detection-accuracy claim'}
    with output.open('x') as stream: json.dump(result,stream,indent=2); stream.write('\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    check(parser.parse_args().out)
