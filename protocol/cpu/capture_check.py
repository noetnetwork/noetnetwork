"""Exercise diagnostic capture with real NumPy; no inference or verdict claim."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile


def check(output):
    import numpy as np
    from verify_current import capture_detector_inputs
    from detector_diagnostics import checked_archive
    if output.exists(): raise FileExistsError(output)
    cases=[]
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);path=root/'capture.npz'
        snapshot={'arrays':{'ids':np.arange(70,dtype=np.int64)},
            'meta':{'advisory_value_contract':'fixture','vocab_size':151936,
                    'sampling_stop_token_ids':[],'top_k':50,'temperature':1.0}}
        result=capture_detector_inputs(snapshot,path)
        with np.load(checked_archive(path,result['sha256']),allow_pickle=False) as archive:
            assert np.array_equal(archive['ids'],snapshot['arrays']['ids'])
            assert json.loads(str(archive['__meta_json']))==snapshot['meta']
        cases.append('numeric-and-metadata-roundtrip')
        before=path.read_bytes()
        try:capture_detector_inputs(snapshot,path)
        except FileExistsError:pass
        else:raise AssertionError('Existing capture overwritten')
        assert path.read_bytes()==before
        cases.append('exclusive-output-preserved')
        for name,array in [('object',np.array([object()],dtype=object)),
                           ('oversized',np.zeros(16*1024*1024+1,dtype=np.uint8))]:
            target=root/(name+'.npz')
            try:capture_detector_inputs({'meta':snapshot['meta'],'arrays':{'data':array}},target)
            except ValueError:pass
            else:raise AssertionError('Invalid capture accepted: '+name)
            assert not target.exists();cases.append(name+'-refused-before-write')
        cases.append('verified-sha256')
        assert hashlib.sha256(path.read_bytes()).hexdigest()==result['sha256']
    report={'format':'noet-detector-capture-check-v1','cases':cases,'passed':True,
            'scope':'Actual numeric diagnostic capture safety; no detector accuracy or inference claim'}
    with output.open('x') as stream:json.dump(report,stream,indent=2);stream.write('\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True,type=Path)
    check(parser.parse_args().out)
