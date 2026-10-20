#!/usr/bin/env python3
"""Real Linux read-only mount checks, tiny fixtures only; no inference or GPU."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from noet.model_artifacts import create

ROOT=Path(__file__).resolve().parents[1]
DOCKER=['docker']+(['--context',os.environ['NOET_DOCKER_CONTEXT']] if os.environ.get('NOET_DOCKER_CONTEXT') else [])

def main():
    image=subprocess.check_output(DOCKER+['image','inspect','--format','{{.Id}}','noet-native-prompt:check'],text=True).strip()
    with tempfile.TemporaryDirectory(prefix='.model-mount-test-',dir=ROOT) as folder:
        fixture=Path(folder);fixture.chmod(0o755)
        model=fixture/'model';(model/'weights').mkdir(parents=True)
        (model/'config.json').write_text('{}')
        weights=model/'weights/model.safetensors';weights.write_bytes(b'tiny fixture, not a model')
        manifest=fixture/'manifest.json';manifest.write_text(json.dumps(create(model,'NOET/fixture','a'*40,'test fixture only')))
        pin=hashlib.sha256(manifest.read_bytes()).hexdigest()
        outcomes={}
        def check(label,readonly=True,nested=False,digest=pin,expected=0):
            mounts=['--mount',f'type=bind,src={ROOT / "noet"},dst=/work/protocol/noet,readonly',
                    '--mount',f'type=bind,src={fixture},dst=/approval,readonly',
                    '--mount',f'type=bind,src={model},dst=/models'+(',readonly' if readonly else '')]
            if nested:mounts+=['--mount',f'type=bind,src={model / "weights"},dst=/models/weights']
            env={'NOET_MODEL_ROOT':'/models','NOET_MODEL_MANIFEST':'/approval/manifest.json','NOET_MODEL_MANIFEST_SHA256':digest}
            args=DOCKER+['run','--rm','--pull=never','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',*mounts]
            for key,value in env.items():args+=['-e',key+'='+value]
            code="from noet.model_runtime import checked_model_directory; print(checked_model_directory('NOET/fixture', 'a'*40))"
            result=subprocess.run(args+['--entrypoint','python3',image,'-c',code],capture_output=True,text=True,timeout=30)
            if (result.returncode==0)!=(expected==0):raise RuntimeError(label+' failed: '+result.stderr[-1000:])
            if expected and 'ValueError:' not in result.stderr:raise RuntimeError(label+' failed for an unexpected reason')
            outcomes[label]='passed'
        check('readOnlyAccepted')
        check('writableRootRejected',readonly=False,expected=1)
        check('writableNestedMountRejected',nested=True,expected=1)
        check('wrongManifestDigestRejected',digest='0'*64,expected=1)
        weights.write_bytes(b'changed fixture')
        check('modifiedWeightsRejected',expected=1)
        print(json.dumps({'imageId':image,'checks':outcomes,'gpuUsed':False,'proofValidation':'not performed'},indent=2))

if __name__=='__main__':main()
