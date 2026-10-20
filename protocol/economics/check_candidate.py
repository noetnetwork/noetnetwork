#!/usr/bin/env python3
"""Validate the unactivated C++ policy against independent arbitrary-precision calculations."""
import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
from simulate import COIN, CAP, MINING, BLOCKS_PER_YEAR, budget
from tokenomics import ALLOCATION_ATOMS, ALLOCATIONS, MONTH_BLOCKS, RELEASE_PERCENT, epochs, policy_text

ROOT = Path(__file__).resolve().parent


def reference(height, percent=RELEASE_PERCENT):
    count = len(epochs(percent))
    remaining = MINING
    whole, part = divmod(height, BLOCKS_PER_YEAR)
    for _ in range(min(whole, count)):
        remaining -= budget(remaining, percent)
    mining = MINING - remaining + part * budget(remaining, percent) // BLOCKS_PER_YEAR
    previous = 0
    if height:
        prev_remaining = MINING
        prev_whole, prev_part = divmod(height - 1, BLOCKS_PER_YEAR)
        for _ in range(min(prev_whole, count)):
            prev_remaining -= budget(prev_remaining, percent)
        previous = MINING - prev_remaining + prev_part * budget(prev_remaining, percent) // BLOCKS_PER_YEAR
    unlocked = []
    for a in ALLOCATIONS:
        cliff, duration = a['cliffMonths'] * MONTH_BLOCKS, a['months'] * MONTH_BLOCKS
        vesting = (a['coins'] - a['initialCoins']) * COIN
        unlocked.append(a['initialCoins'] * COIN + vesting * min(max(height - cliff, 0), duration) // duration)
    return [height, mining-previous if height else 0, mining, ALLOCATION_ATOMS+mining, *unlocked, mining+sum(unlocked)]


def run(out, percent=RELEASE_PERCENT):
    if out.exists() or out.is_symlink(): raise FileExistsError(out)
    EPOCHS = len(epochs(percent))
    heights = {0,1,2,2**32-1,2**63-1,2**64-1}
    for epoch in range(1,EPOCHS+2):
        for delta in (-1,0,1): heights.add(epoch*BLOCKS_PER_YEAR+delta)
    rng=random.Random(20261011)
    for _ in range(256): heights.add(rng.randrange(EPOCHS*BLOCKS_PER_YEAR))
    heights=sorted(heights)
    with tempfile.TemporaryDirectory(prefix='noet-policy-check-') as folder:
        binary=Path(folder)/'candidate'
        policy=policy_text(percent)
        (Path(folder)/'policy.h').write_text(policy)
        (Path(folder)/'check.cpp').write_bytes((ROOT/'candidate/check.cpp').read_bytes())
        subprocess.run(['clang++','-std=c++17','-O1','-g','-fsanitize=address,undefined,unsigned-integer-overflow',
            '-fno-sanitize-recover=all',str(Path(folder)/'check.cpp'),'-o',str(binary)],check=True,capture_output=True,timeout=60)
        result=subprocess.run([str(binary)],input='\n'.join(map(str,heights))+'\n',text=True,capture_output=True,
            check=True,timeout=60,env={**os.environ,'ASAN_OPTIONS':'halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1'})
        rows=[[int(field) for field in row] for row in csv.reader(io.StringIO(result.stdout))]
        expected=[reference(height, percent) for height in heights]
        if rows != expected: raise ValueError('C++ schedule differs from arbitrary-precision reference')
    record={'status':'passed','activated':False,'policy':f'{CAP//COIN//1_000_000}m cap; {MINING//COIN//1_000_000}m mining; {percent:g}% remaining annual budget',
        'blocksChecked':EPOCHS*BLOCKS_PER_YEAR,'epochsChecked':EPOCHS,'crossLanguageVectors':len(rows),
        'miningAtoms':str(MINING),'supplyCapAtoms':str(CAP),
        'sanitizers':['address','undefined','unsigned-integer-overflow'],
        'sourceSha256':{'policy.h':hashlib.sha256(policy.encode()).hexdigest(),
                        'check.cpp':hashlib.sha256((ROOT/'candidate/check.cpp').read_bytes()).hexdigest()},
        'compiler':subprocess.check_output(['clang++','--version'],text=True).splitlines()[0],
        'scope':'Candidate arithmetic only; no chain activation or enforceable vesting outputs'}
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x') as stream:json.dump(record,stream,indent=2);stream.write('\n')
    print(json.dumps(record,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--percent',type=float,default=RELEASE_PERCENT,help='release rate to check (default: tokenomics.json)')
    args=parser.parse_args()
    percent=int(args.percent) if args.percent==int(args.percent) else args.percent
    run(args.out, percent)
