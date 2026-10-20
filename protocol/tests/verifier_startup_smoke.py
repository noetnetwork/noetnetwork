#!/usr/bin/env python3
"""Check the real packaged entrypoint refuses unsafe calibration settings.

No model mount, credential or calibration approval is supplied by this test.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
from node_smoke import DOCKER


def check(image, output):
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image): raise ValueError('Use a recorded local image ID')
    if output.exists(): raise FileExistsError(output)
    mid = 'NOET/fixture@'+'a'*40
    cases = [({}, 'exact model@revision:precision'),
        ({'POW_PROMPT_GIBBERISH_CALIBRATED':'*'}, 'exact model@revision:precision'),
        ({'POW_PROMPT_GIBBERISH_CALIBRATED':mid}, 'exact model@revision:precision'),
        ({'POW_PROMPT_GIBBERISH_GATE':'shadow'}, 'POW_PROMPT_GIBBERISH_GATE=enforce'),
        ({'POW_PROMPT_GIBBERISH_GATE':'off'}, 'POW_PROMPT_GIBBERISH_GATE=enforce'),
        ({'POW_PROMPT_GIBBERISH_BITS':'99'}, 'POW_PROMPT_GIBBERISH_BITS=14.0'),
        ({'POW_PROMPT_GIBBERISH_ACTIVATION_TIME':'3000000000'}, 'POW_PROMPT_GIBBERISH_ACTIVATION_TIME=0'),
        ({'NOET_REQUIRE_PROMPT_CALIBRATION':'0'}, 'NOET_REQUIRE_PROMPT_CALIBRATION=1')]
    outcomes = []
    for env, reason in cases:
        command = DOCKER + ['run','--rm','--network','none','--read-only','--memory','256m',
            '--cpus','1','--pids-limit','32','--cap-drop','ALL','--security-opt','no-new-privileges']
        for key, value in {'MODEL_NAME':'NOET/fixture','MODEL_COMMIT':'a'*40, **env}.items():
            command += ['-e', key+'='+value]
        result = subprocess.run(command+[image], capture_output=True, text=True, timeout=30)
        assert result.returncode == 1 and 'ValueError: ' in result.stderr and reason in result.stderr, result.stderr[-2000:]
        assert 'checked_model_directory' not in result.stderr
        outcomes.append({'settings':env, 'refused':True, 'reason':reason})
    report = {'format':'noet-packaged-calibration-startup-v1', 'image':image,
        'cases':outcomes, 'modelMounted':False, 'serviceStarted':False,
        'productionReady':False, 'scope':'Packaged startup refusals; no calibration or model correctness claim'}
    with output.open('x') as stream: json.dump(report, stream, indent=2); stream.write('\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.image, args.out), indent=2))
