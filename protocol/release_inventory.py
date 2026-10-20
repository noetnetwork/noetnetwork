#!/usr/bin/env python3
"""Read-only inventory of an existing local development node image.

Never pulls, publishes, signs, or starts the node entrypoint.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

BINARIES=('/usr/local/bin/bitcoind','/usr/local/bin/bitcoin-cli','/usr/local/bin/bitcoin-wallet','/usr/local/bin/test_bitcoin')


def packages(text):
    values=[];seen=set()
    for line in text.splitlines():
        fields=line.split('\t')
        if len(fields)!=3 or not all(fields):raise ValueError('Invalid package inventory')
        name,version,architecture=fields
        if name in seen:raise ValueError('Duplicate package in inventory')
        seen.add(name);values.append({'name':name,'version':version,'architecture':architecture})
    if not values:raise ValueError('Empty package inventory')
    return sorted(values,key=lambda value:value['name'])


def binaries(text):
    values={}
    for line in text.splitlines():
        fields=line.split(None,1)
        if len(fields)!=2 or not re.fullmatch('[a-f0-9]{64}',fields[0]):raise ValueError('Invalid executable digest')
        digest,name=fields
        if name not in BINARIES or name in values:raise ValueError('Unexpected executable in inventory')
        values[name]=digest
    if set(values)!=set(BINARIES):raise ValueError('Missing executable digest')
    return values


def collect(image,run):
    if not image or image.startswith('-'):raise ValueError('Use an existing local image reference')
    inspected=json.loads(run('image','inspect',image))
    if not isinstance(inspected,list) or len(inspected)!=1:raise ValueError('Expected exactly one image')
    info=inspected[0];identity=info['Id']
    if not re.fullmatch('sha256:[a-f0-9]{64}',identity):raise ValueError('Invalid image identity')
    if info['Os']!='linux' or info['Architecture'] not in ('arm64','amd64'):raise ValueError('Unsupported node image platform')
    # Use the inspected immutable ID. A tag changing between commands cannot
    # redirect the collection to a different image. No mounts or network.
    controls=('run','--rm','--pull=never','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--memory=512m','--cpus=1','--pids-limit=64')
    package_text=run(*controls,'--entrypoint','dpkg-query',identity,'-W','-f=${binary:Package}\t${Version}\t${Architecture}\n')
    binary_text=run(*controls,'--entrypoint','sha256sum',identity,*BINARIES)
    return {'format':'noet-node-image-inventory-v1','imageId':identity,'os':info['Os'],'architecture':info['Architecture'],'repositoryDigests':info.get('RepoDigests') or [],'packages':packages(package_text),'executables':binaries(binary_text),'signed':False,'productionReady':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    docker=['docker']+(['--context',os.environ['NOET_DOCKER_CONTEXT']] if os.environ.get('NOET_DOCKER_CONTEXT') else [])
    def run(*arguments):
        value=subprocess.run([*docker,*arguments],capture_output=True,text=True,timeout=120,check=True).stdout
        if len(value)>4*1024*1024:raise ValueError('Inventory output too large')
        return value
    result=collect(args.image,run)
    with args.out.open('x') as stream:stream.write(json.dumps(result,sort_keys=True,indent=2)+'\n')
    print('Recorded '+result['imageId']+' with '+str(len(result['packages']))+' packages. Unsigned development inventory only.')


if __name__=='__main__':main()
