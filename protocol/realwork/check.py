#!/usr/bin/env python3
"""Offline private-node/miner/verifier integration; never a production launch.

No published ports, public peers, mock verdicts, cloud resources or paid APIs.
The test model is not calibrated; the research verifier explicitly scores its
prompt gate in shadow. Report every rejected candidate and never claim a live
or spendable reward without native confirmation.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'.upstream/.noet-cpu-proof-20261011'
MODEL='Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca'


def run(args):
    if args.out.exists():raise FileExistsError(args.out)
    args.out.mkdir(mode=0o700,parents=True)
    docker=['docker','--context',args.docker_context]
    def command(*parts,timeout=180,check=True,input_text=None):
        result=subprocess.run(docker+list(parts),capture_output=True,text=True,timeout=timeout,input=input_text)
        if check and result.returncode:raise RuntimeError(result.stderr[-2000:] or result.stdout[-2000:])
        return result
    def immutable(tag):
        value=command('image','inspect',tag,'--format','{{.Id}}').stdout.strip()
        if not re.fullmatch(r'sha256:[a-f0-9]{64}',value):raise ValueError('Unpinned test image')
        return value
    images={kind:immutable(getattr(args,kind+'_image')) for kind in ('node','producer','verifier')}
    name='noet-realwork-'+uuid.uuid4().hex[:10]
    def rpc(method,*params):
        # Mining proofs exceed per-argument OS limits on some platforms. CLI
        # stdin preserves exact RPC values without putting proofs in argv.
        values=[x if isinstance(x,str) else json.dumps(x) for x in params]
        if any('\n' in x or '\r' in x for x in values):raise ValueError('Multiline RPC argument')
        result=command('exec','-i',name,'/src/bcore/build/bin/bitcoin-cli','-chain=tensor-reg',
            '-datadir=/tmp/node','-rpcport=30443','-stdin',method,
            input_text='\n'.join(values)+ ('\n' if values else ''))
        try:return json.loads(result.stdout,parse_float=str)
        except json.JSONDecodeError:return result.stdout.strip()
    def wait(check,seconds=60):
        until=time.monotonic()+seconds;last=None
        while time.monotonic()<until:
            try:
                result=check()
                if result:return result
            except Exception as error:last=str(error)
            time.sleep(.5)
        raise TimeoutError(str(last))
    report={'format':'noet-live-work-research-v1','images':images,'productionReady':False,
        'promptCalibrationApproved':False,'scope':'Isolated native integration, not guarded-service or public mining acceptance',
        'attempts':[],'acceptedBlocks':[],'spendableRewardVerified':False,'requestedBlocks':args.blocks}
    created=[]
    key_file=args.out/'transport.env'
    with os.fdopen(os.open(key_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:
        stream.write('VALIDATOR_SHARED_SECRET='+secrets.token_hex(32)+'\n')
    try:
        for mode in ('mock','desktop'):
            probe=name+'-'+mode
            created.append(probe)
            command('create','--name',probe,'--pull=never','--network','none',
                '--memory','2g','--cpus','1','--cap-drop','ALL','--security-opt','no-new-privileges',
                '--tmpfs','/tmp:rw,size=256m',
                images['node'],'-datadir=/tmp','-server=1','-validationapi='+mode,'-useextapi=1',check=False)
            rejected=command('start','-a',probe,timeout=60,check=False)
            if rejected.returncode==0 or 'NOET private work requires' not in rejected.stdout+rejected.stderr:
                raise AssertionError('Unsupported verifier mode was not refused: '+mode)
            command('rm',probe);created.remove(probe)
        report['refusedValidationModes']=['mock','desktop']
        created.append(name)
        command('run','-d','--name',name,'--pull=never','--network','none','--read-only',
            '--memory','2g','--cpus','1','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--tmpfs','/tmp:rw,size=512m','--env-file',str(key_file),
            '-e','VALIDATOR_HOST=127.0.0.1','-e','VALIDATOR_PUSH_PORT=18555','-e','VALIDATOR_PULL_PORT=18556',
            '--entrypoint','sh',images['node'],'-c',
            'mkdir /tmp/node && exec /src/bcore/build/bin/bitcoind -chain=tensor-reg -datadir=/tmp/node -server=1 -dnsseed=0 -fixedseeds=0 -listenonion=0 -discover=0 -connect=0 -port=30441 -rpcport=30443 -validationapi=real -useextapi=1 -printtoconsole=1 -fallbackfee=0.0002')
        wait(lambda:rpc('getblockchaininfo'))
        report['genesis']=rpc('getblockhash',0)
        if report['genesis']!='301587d540aafc312cf4606e700d9662e038e7e43ca6cca92779d6b4baa81b4e':
            raise AssertionError('Private work genesis does not match the pinned profile')
        registry=rpc('getmodelslist',False)
        if len(registry)!=1 or registry[0]['model_name']+'@'+registry[0]['model_commit']!=MODEL:
            raise AssertionError('Real pinned model is not registered')
        if registry[0]['difficulty']!=1000000:raise AssertionError('Unexpected registered difficulty')
        report['registry']=registry
        rpc('createwallet','private-work')
        address=rpc('getnewaddress','','bech32')
        if not address.startswith('nwrk1'):raise AssertionError('Wrong private wallet prefix')
        report['rewardAddress']=address
        script=rpc('getaddressinfo',address)['scriptPubKey']
        report['validationInfo']=rpc('getvalidationapiinfo')
        receiver=name+'-verifier'
        common=['--pull=never','--read-only','--memory','20g','--cpus','3','--pids-limit','256',
            '--cap-drop','ALL','--security-opt','no-new-privileges','--user',f'{os.getuid()}:{os.getgid()}',
            '--tmpfs','/tmp:rw,size=256m,mode=1777','--tmpfs','/data/pow_proofs:rw,size=512m,mode=1777',
            '-v',str(ROOT/'realwork')+':/checks:ro','-v',str(ROOT/'cpu')+':/cpu:ro',
            '-v',str(WORK/'model')+':/model:ro','-v',str(ROOT/'cpu/qwen3-06b.candidate.json')+':/model-manifest.json:ro',
            '-v',str(args.out.resolve())+':/out',
            '-e','NOET_MODEL_ROOT=/model','-e','NOET_MODEL_MANIFEST=/model-manifest.json',
            '-e','NOET_MODEL_MANIFEST_SHA256=c18e242c1f4c2273d89a27a226856ad1a6623a7a0c22839495b45371eefa3ae4',
            '-e','POW_V4_STRICT_MODE=enforce','-e','POW_V2_STREAMED=1']
        created.append(receiver)
        command('run','-d','--name',receiver,'--network','container:'+name,*common,
            '--env-file',str(key_file),'--entrypoint','python',images['verifier'],
            '/checks/verify.py','--out','/out/verifier','--seconds','7200')
        wait(lambda:(args.out/'verifier/ready.json').exists(),120)
        for attempt in range(args.attempts):
            parent=rpc('getbestblockhash')
            job=rpc('create_mining_work_unit','tensor-reg',script,secrets.token_hex(8))
            record={'profile':'noet-private-work-v1','genesis':report['genesis'],
                    'parent':parent,'model':registry[0],'job':job}
            jobfile=args.out/f'job-{attempt}.json';jobfile.write_text(json.dumps(record,indent=2)+'\n')
            producer=name+'-producer'
            created.append(producer)
            command('create','--name',producer,'--network','none',*common,
                '-v',str(ROOT/'.upstream/.noet-cpu-height-20261011/build/bin')+':/engine:ro',
                '-v',str(WORK/'qwen3-0.6b-bf16.gguf')+':/model.gguf:ro',
                '--entrypoint','timeout',images['producer'],'900','python','/checks/generate.py',
                '--job',f'/out/job-{attempt}.json','--engine','/engine/llama-server',
                '--model','/model.gguf','--out',f'/out/generation-{attempt}')
            generated=command('start','-a',producer,timeout=960,check=False)
            (args.out/f'producer-{attempt}.log').write_text(generated.stdout+generated.stderr)
            command('rm',producer);created.remove(producer)
            observed={'attempt':attempt,'producerExit':generated.returncode,'submissions':[]}
            report['attempts'].append(observed)
            if generated.returncode and 'No eligible evidence' not in (generated.stdout+generated.stderr)[-4000:]:
                raise RuntimeError('Producer failed before completing an eligible-window search; inspect its log')
            for proof in sorted((args.out/f'generation-{attempt}/proofs').glob(str(job['req_id'])+'_*.bin')):
                raw=proof.read_bytes()
                if len(raw)>2*1024*1024:raise ValueError('Oversized mining response')
                submitted=rpc('submit_mining_response',job['req_id'],base64.b64encode(raw).decode(),0)
                observed['submissions'].append({'proofSha256':hashlib.sha256(raw).hexdigest(),'response':submitted})
                if submitted.get('status') not in ('accepted','accepted_pending_connect'):continue
                block=submitted['block_hash']
                def settled():
                    rows=[r for r in rpc('getrecentvalidations',100)
                          if r['block_hash']==block and r['type']=='full']
                    if not rows:return None
                    status=rows[0]['full_status']
                    if status in ('red','amber') or (status=='green' and rpc('getbestblockhash')==block):
                        return rows[0]
                    return None
                try:validation=wait(settled,900)
                except TimeoutError:observed['acceptanceTimeout']=True;break
                observed['nativeFullValidation']=validation
                if validation['full_status']!='green':
                    raise RuntimeError('Generated proof did not pass native Full validation: '+validation['full_status'])
                height=rpc('getblockcount');balance=rpc('getbalances')
                report['acceptedBlocks'].append({'hash':block,'height':rpc('getblockcount'),
                    'block':rpc('getblock',block,2),'wallet':balance,'nativeFullValidation':validation})
                observed['duplicateSubmission']=rpc('submit_mining_response',job['req_id'],base64.b64encode(raw).decode(),0)
                if rpc('getblockcount')!=height or rpc('getbalances')!=balance:
                    raise AssertionError('Duplicate changed canonical height or reward')
                observed['duplicateIsIdempotent']=True
                break
            (args.out/'progress.json').write_text(json.dumps(report,indent=2)+'\n')
            if len(report['acceptedBlocks'])>=args.blocks:break
        report['finalHeight']=rpc('getblockcount');report['finalPeerCount']=rpc('getconnectioncount')
        report['wallet']=rpc('getbalances')
    except Exception as error:
        report.update(exceptionType=type(error).__name__,exception=str(error)[:2000])
    finally:
        for container in reversed(created):
            log=command('logs','--tail','200',container,check=False)
            (args.out/(container+'.log')).write_text(log.stdout+log.stderr)
            command('rm','-f',container,check=False)
        key_file.unlink(missing_ok=True)
        (args.out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k in ('acceptedBlocks','finalHeight','exception','productionReady')},indent=2))
    return len(report['acceptedBlocks'])>=args.blocks and 'exception' not in report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--docker-context',default='colima-noet-build')
    p.add_argument('--node-image',required=True);p.add_argument('--producer-image',required=True)
    p.add_argument('--verifier-image',required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--attempts',type=int,default=4)
    p.add_argument('--blocks',type=int,default=1,choices=(1,2,3))
    a=p.parse_args()
    if not 1<=a.attempts<=12:p.error('At most twelve bounded attempts')
    raise SystemExit(0 if run(a) else 2)
