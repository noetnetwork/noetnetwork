"""Summarize recorded GPU research evidence without making production rate claims."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics


def spread(values):
    return None if not values else {'minimum':min(values),'median':statistics.median(values),'maximum':max(values)}


def summarize(root):
    native=json.loads((root/'result.json').read_text())
    full=[]
    for path in sorted((root/'verifier').glob('*/result.json')):
        row=json.loads(path.read_text())
        if row.get('requestType')==2 and not row.get('reusedExactEnvelope'):
            full.append({'record':str(path.relative_to(root)),**row})
    speed=[];offload=set()
    for path in sorted(root.glob('generation-*/engine.log')):
        text=path.read_text(errors='replace')
        match=re.search(r'offloaded (\d+/\d+) layers to GPU',text)
        if match:offload.add(match[1])
        speed.extend(float(value) for value in re.findall(r'(?<!prompt )eval time\s*=.*?\(.*?([\d.]+) tokens per second\)',text))
    elapsed=native.get('finishedAt',0)-native.get('startedAt',0)
    blocks=native.get('acceptedBlocks',[])
    return {'format':'noet-gpu-run-summary-v1','productionReady':False,
        'nativeResultSha256':hashlib.sha256((root/'result.json').read_bytes()).hexdigest(),
        'passed':native.get('passed',False),'acceptedBlocks':len(blocks),'attempts':len(native.get('attempts',[])),
        'elapsedSeconds':elapsed,'harnessBlocksPerHour':len(blocks)*3600/elapsed if elapsed>0 else None,
        'generationTokensPerSecond':spread(speed),'offloadedLayers':sorted(offload),
        'fullVerificationSeconds':spread([row['elapsedSeconds'] for row in full]),
        'peakCudaBytes':max((row.get('peakCudaBytes',0) for row in full),default=0),
        'eligibleDetectorPositions':spread([(row.get('detectorSteps') or {}).get('eligible_steps',0) for row in full]),
        'fullRequests':full,
        'scope':'Unoptimized sequential research harness with engine startup per attempt; not production throughput, model calibration, token value or expected miner revenue'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    result=summarize(args.run)
    with args.out.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps({key:value for key,value in result.items() if key!='fullRequests'}))
