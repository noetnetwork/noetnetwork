"""Single-host GPU/native private-work acceptance. No public mining or mainnet coins."""
import argparse
import base64
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time

ROOT=Path('/workspace/noet-gpu')
# stage.py writes model.json when a host tests another candidate model
# (protocol/models/profiles.json). A new model gets a new private genesis and,
# until two conversions agree, an unpinned GGUF; both are recorded, not assumed.
PROFILE=(json.loads((ROOT/'model.json').read_text()) if (ROOT/'model.json').exists() else
    {'id':'Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca','gguf':'qwen3-0.6b-bf16.gguf',
     'ggufSha256':'8fa11f4d815167c4f6e3c91b25aebde25d8346a8f11228e744016592532b0700',
     'privateGenesis':'301587d540aafc312cf4606e700d9662e038e7e43ca6cca92779d6b4baa81b4e'})
GENESIS=PROFILE['privateGenesis']
MODEL=PROFILE['id']
GGUF=ROOT/PROFILE['gguf']
# A switch rehearsal (design/MODEL-SWITCH.md): from SWITCH['height'] every block
# carries the second model, and each attempt runs the weights of its job's height.
SWITCH=PROFILE.get('switch')
SWITCH_MODEL=SWITCH['id'] if SWITCH else None
SWITCH_GGUF=ROOT/SWITCH['gguf'] if SWITCH else None


def run(args):
    if os.geteuid()==0:raise ValueError('Run the acceptance as the restricted worker')
    args.out.mkdir(mode=0o700,parents=True,exist_ok=False)
    data=args.out/'node';data.mkdir(mode=0o700)
    binaries=ROOT/'native-source/node-build/bin'
    env=dict(os.environ)
    env.update(NOET_MODEL_ID=MODEL,VALIDATOR_SHARED_SECRET=secrets.token_hex(32),VALIDATOR_HOST='127.0.0.1',
        VALIDATOR_PULL_BIND='127.0.0.1',VALIDATOR_PUSH_PORT='18555',VALIDATOR_PULL_PORT='18556')
    report={'format':'noet-gpu-native-integration-v1','productionReady':False,
        'promptCalibrationApproved':False,'requestedBlocks':args.blocks,'attempts':[],
        'rejectedProofs':[],'continueAfterRejection':args.continue_after_rejection,
        'acceptedBlocks':[],'spendableRewardVerified':False,'startedAt':time.time(),
        'producerAccelerator':args.accelerator,
        'flashAttention':args.flash_attention,'forgeries':[],'forgeryAccepted':False,
        'switch':{'model':SWITCH_MODEL,'height':SWITCH['height'],'relabelAfterSwitch':args.relabel_after_switch} if SWITCH else None,
        'scope':'Single rented host, private research chain, unapproved calibration in shadow, inherited test economics'}
    processes=[];logs=[]
    deadline=time.monotonic()+args.seconds
    def remaining():
        value=deadline-time.monotonic()
        if value<=0:raise TimeoutError('Overall acceptance deadline reached')
        return value
    def rpc(method,*params,wallet=None):
        values=[v if isinstance(v,str) else json.dumps(v) for v in params]
        if any('\n' in v or '\r' in v for v in values):raise ValueError('Multiline RPC parameter')
        command=[str(binaries/'bitcoin-cli'),'-chain=tensor-reg','-datadir='+str(data),'-rpcport=30443','-stdin']
        if wallet:command+=['-rpcwallet='+wallet]
        result=subprocess.run(command+[method],input='\n'.join(values)+ ('\n' if values else ''),
            text=True,capture_output=True,timeout=min(120,remaining()))
        if result.returncode:raise RuntimeError(result.stderr[-2000:])
        try:return json.loads(result.stdout,parse_float=str)
        except json.JSONDecodeError:return result.stdout.strip()
    def wait(check,seconds=90):
        until=min(deadline,time.monotonic()+seconds);last=None
        while time.monotonic()<until:
            try:
                result=check()
                if result:return result
            except Exception as error:last=str(error)
            time.sleep(.5)
        raise TimeoutError(last or 'Acceptance condition did not complete')
    def start(command,name):
        log=(args.out/name).open('xb');logs.append(log)
        process=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        processes.append(process);return process
    def save():
        temporary=args.out/'progress.tmp';temporary.write_text(json.dumps(report,indent=2)+'\n')
        temporary.replace(args.out/'progress.json')
    try:
        for executable in ('engine/build/bin/llama-server','native-source/node-build/bin/bitcoind','native-source/node-build/bin/bitcoin-cli'):
            if not os.access(ROOT/executable,os.R_OK|os.X_OK):raise PermissionError('Worker cannot execute '+executable)
        with GGUF.open('rb') as source:
            converted_digest=hashlib.file_digest(source,'sha256').hexdigest()
        if PROFILE['ggufSha256'] and converted_digest!=PROFILE['ggufSha256']:
            raise ValueError('Converted model differs from the pinned research artifact')
        report.update(model=MODEL,convertedModelSha256=converted_digest,convertedModelPinned=bool(PROFILE['ggufSha256']))
        if SWITCH:
            with SWITCH_GGUF.open('rb') as source:
                switch_digest=hashlib.file_digest(source,'sha256').hexdigest()
            if SWITCH['ggufSha256'] and switch_digest!=SWITCH['ggufSha256']:
                raise ValueError('Converted switch model differs from its pinned artifact')
            report['switch']['convertedModelSha256']=switch_digest
        node_command=[str(binaries/'bitcoind'),'-chain=tensor-reg','-datadir='+str(data),'-server=1',
            '-listen=0','-dnsseed=0','-fixedseeds=0','-listenonion=0','-discover=0','-connect=0',
            '-port=30441','-rpcport=30443','-validationapi=real','-useextapi=1',
            '-printtoconsole=1','-fallbackfee=0.0002']
        node_process=start(node_command,'node.log')
        wait(lambda:rpc('getblockchaininfo'))
        report['genesis']=rpc('getblockhash',0)
        if GENESIS and report['genesis']!=GENESIS:raise AssertionError('Private genesis mismatch')
        registry=rpc('getmodelslist',False)
        registered={r['model_name']+'@'+r['model_commit']:r for r in registry}
        if (sorted(registered)!=sorted([MODEL]+([SWITCH_MODEL] if SWITCH else []))
                or any(r['difficulty']!=1000000 for r in registry)):raise AssertionError('Model registry mismatch')
        report['registry']=registry
        report['validationInfo']=rpc('getvalidationapiinfo')
        if report['validationInfo']['mode']!='real' or report['validationInfo']['result_pull_endpoint']!='tcp://127.0.0.1:18556':
            raise AssertionError('Unexpected verifier mode or bind')
        rpc('createwallet','miner')
        address=rpc('getnewaddress','','bech32',wallet='miner')
        if not address.startswith('nwrk1'):raise AssertionError('Wrong payout network')
        script=rpc('getaddressinfo',address,wallet='miner')['scriptPubKey']
        password=secrets.token_hex(32)
        rpc('encryptwallet',password,wallet='miner')
        # Ephemeral laboratory recovery only. This directory is private and is
        # explicitly excluded from collected evidence and release artifacts.
        # Keeping the test key permits recovery if the acceptance process exits.
        password_fd=os.open(data/'acceptance-wallet-password',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(password_fd,'w') as password_file:password_file.write(password+'\n')
        if Decimal(rpc('getbalances',wallet='miner')['mine']['trusted'])!=0:raise AssertionError('Funded initial wallet')
        report['rewardAddress']=address
        start([sys.executable,str(ROOT/'checks/verify.py'),'--out',str(args.out/'verifier'),
            '--seconds',str(min(86400,args.seconds)),'--capture-detector'],'verifier.log')
        wait(lambda:(args.out/'verifier/ready.json').exists(),180)
        spend=None
        for attempt in range(args.attempts):
            remaining()
            if len(report['acceptedBlocks'])>=args.blocks:
                if not args.spend or (spend and rpc('gettransaction',spend,wallet='recipient')['confirmations']>=1):break
                if spend is None:
                    report['matureBalance']=rpc('getbalances',wallet='miner')
                    if Decimal(report['matureBalance']['mine']['trusted'])<2:raise AssertionError('No mature mined reward')
                    rpc('createwallet','recipient')
                    recipient=rpc('getnewaddress','','bech32',wallet='recipient')
                    rpc('walletpassphrase',password,30,wallet='miner')
                    try:spend=rpc('sendtoaddress',recipient,'1',wallet='miner')
                    finally:rpc('walletlock',wallet='miner')
                    report['rewardSpend']={'txid':spend,'recipient':recipient,'mempool':rpc('getmempoolentry',spend)}
            parent=rpc('getbestblockhash')
            job=rpc('create_mining_work_unit','tensor-reg',script,secrets.token_hex(8))
            chosen=SWITCH_MODEL if SWITCH and job['height']>=SWITCH['height'] else MODEL
            # Research forgery: the genesis model's weights behind a proof that claims the switch model.
            forged=chosen==SWITCH_MODEL and len(report['forgeries'])<args.relabel_after_switch
            weights=SWITCH_GGUF if chosen==SWITCH_MODEL and not forged else GGUF
            record={'profile':'noet-private-work-v1','genesis':report['genesis'],'parent':parent,
                    'model':registered[chosen],'job':job}
            jobfile=args.out/f'job-{attempt}.json';jobfile.write_text(json.dumps(record,indent=2)+'\n')
            generation=args.out/f'generation-{attempt}'
            with (args.out/f'producer-{attempt}.log').open('xb') as log:
                generated=subprocess.Popen([sys.executable,str(ROOT/'checks/generate.py'),'--job',str(jobfile),
                    '--engine',str(ROOT/'engine/build/bin/llama-server'),'--model',str(weights),
                    '--out',str(generation),'--accelerator',args.accelerator,
                    '--flash-attention',args.flash_attention],env={**env,'NOET_MODEL_ID':chosen},
                    stdout=log,stderr=subprocess.STDOUT,
                    start_new_session=True)
                try:generated.wait(timeout=min(900,remaining()))
                finally:
                    # Include a stalled engine grandchild, even if its Python
                    # parent has already failed. Never leave GPU jobs orphaned.
                    try:os.killpg(generated.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    generated.wait(timeout=10)
            observed={'attempt':attempt,'height':job['height'],'model':chosen,'weights':weights.name,
                'producerExit':generated.returncode,'submissions':[]}
            if forged:observed['forgedRelabel']=True
            report['attempts'].append(observed);save()
            if generated.returncode:
                tail=(args.out/f'producer-{attempt}.log').read_text()[-4000:]
                if 'Incomplete inference run' in tail:
                    # The model ended its text (an end token) before the fixed proof
                    # length: no proof, a miss like "No eligible evidence" (and so for miners).
                    observed['incompleteRun']=True;save()
                elif 'No eligible evidence' not in tail:
                    raise RuntimeError('Producer failed before completing an eligible-window search; inspect its log')
            for proof in sorted((generation/'proofs').glob(str(job['req_id'])+'_*.bin')):
                with proof.open('rb') as source:raw=source.read(2*1024*1024+1)
                if len(raw)>2*1024*1024:raise ValueError('Oversized mining proof')
                submitted=rpc('submit_mining_response',job['req_id'],base64.b64encode(raw).decode(),0)
                observed['submissions'].append({'proofSha256':hashlib.sha256(raw).hexdigest(),'response':submitted});save()
                if submitted.get('status') not in ('accepted','accepted_pending_connect'):
                    if forged:
                        # Refused when submitted, before any Full replay: also a refusal.
                        report['forgeries'].append({'attempt':attempt,'height':job['height'],'claimedModel':chosen,
                            'weights':weights.name,'verdict':'refused-at-submit','response':submitted})
                        save();break
                    continue
                block=submitted['block_hash']
                def settled():
                    rows=[r for r in rpc('getrecentvalidations',100) if r['block_hash']==block and r['type']=='full']
                    if not rows:return None
                    status=rows[0]['full_status']
                    if status in ('red','amber') or (status=='green' and rpc('getbestblockhash')==block):return rows[0]
                    return None
                try:verdict=wait(settled,900)
                except TimeoutError:observed['acceptanceTimeout']=True;break
                observed['nativeFullValidation']=verdict
                if forged:
                    report['forgeries'].append({'attempt':attempt,'height':job['height'],'blockHash':block,
                        'claimedModel':chosen,'weights':weights.name,'verdict':verdict['full_status']})
                    report['forgeryAccepted']=report['forgeryAccepted'] or verdict['full_status']=='green'
                    save()
                    if verdict['full_status']=='green':raise AssertionError('A relabelled proof was accepted')
                    break
                if verdict['full_status']!='green':
                    report['rejectedProofs'].append({'attempt':attempt,'height':job['height'],
                        'blockHash':block,'proofSha256':hashlib.sha256(raw).hexdigest(),
                        'verdict':verdict['full_status']})
                    save()
                    # Explicit progress-only research mode. Never replay a RED
                    # envelope with another seed or change its result. Obtain a
                    # fresh work lease, and retain a failed acceptance outcome.
                    if args.continue_after_rejection and verdict['full_status']=='red':break
                    raise RuntimeError('Generated proof did not pass native Full validation: '+verdict['full_status'])
                height=rpc('getblockcount');wallet=rpc('getbalances',wallet='miner')
                report['acceptedBlocks'].append({'height':height,'hash':block,'nativeFullValidation':verdict,'wallet':wallet})
                duplicate=rpc('submit_mining_response',job['req_id'],base64.b64encode(raw).decode(),0)
                if rpc('getblockcount')!=height or rpc('getbalances',wallet='miner')!=wallet:
                    raise AssertionError('Duplicate changed canonical height or reward')
                observed['duplicateSubmission']=duplicate;observed['duplicateIsIdempotent']=True
                break
            save()
        report['finalHeight']=rpc('getblockcount');report['peerCount']=rpc('getconnectioncount')
        report['wallet']=rpc('getbalances',wallet='miner')
        if spend:
            transaction=rpc('gettransaction',spend,wallet='recipient')
            report['rewardSpend']['confirmed']=transaction
            report['spendableRewardVerified']=transaction['confirmations']>=1 and Decimal(transaction['amount'])==1
        milestones=len(report['acceptedBlocks'])>=args.blocks and (not args.spend or report['spendableRewardVerified'])
        report['miningAndPaymentComplete']=milestones
        report['passed']=(milestones and not report['rejectedProofs'] and not report['forgeryAccepted']
            and len(report['forgeries'])==(args.relabel_after_switch if SWITCH else 0))
        if milestones and args.check_indexer:
            from noet.indexer import Index
            index=Index(args.out/'explorer.sqlite3',report['genesis'])
            try:
                status=index.sync(rpc,limit=1000)
                if status['height']!=report['finalHeight'] or status['tip']!=rpc('getbestblockhash') or status['lag']:
                    raise AssertionError('Explorer differs from the native chain')
                report['explorer']={'status':status,'miner':index.address(address)}
                if spend:
                    recipient=index.address(report['rewardSpend']['recipient'])
                    if recipient['matureAtoms']!='100000000':raise AssertionError('Explorer recipient balance differs')
                    if spend not in [row['txid'] for row in recipient['transactions']]:raise AssertionError('Explorer missed reward spend')
                    report['explorer']['recipient']=recipient
            finally:index.db.close()
        if milestones and args.restart:
            before={'height':rpc('getblockcount'),'hash':rpc('getbestblockhash'),'wallet':rpc('getbalances',wallet='miner')}
            rpc('stop');node_process.wait(timeout=min(60,remaining()))
            node_process=start(node_command,'node-restart.log')
            wait(lambda:rpc('getblockchaininfo'),120)
            for name in ('miner','recipient') if spend else ('miner',):
                if name not in rpc('listwallets'):rpc('loadwallet',name)
            after={'height':rpc('getblockcount'),'hash':rpc('getbestblockhash'),'wallet':rpc('getbalances',wallet='miner')}
            if before!=after:raise AssertionError('Restart changed chain or wallet state')
            if spend and rpc('gettransaction',spend,wallet='recipient')['confirmations']<1:
                raise AssertionError('Reward spend lost confirmation after restart')
            report['restart']={'passed':True,'height':after['height'],'hash':after['hash'],
                'scope':'Clean restart only; not crash recovery, reindex or multi-peer acceptance'}
    except (Exception,KeyboardInterrupt) as error:
        report.update(passed=False,exceptionType=type(error).__name__,exception=str(error)[:2000])
    finally:
        if processes and processes[0].poll() is None:
            try:
                report['finalHeight']=rpc('getblockcount')
                report['wallet']=rpc('getbalances',wallet='miner')
            except Exception:pass
        for process in reversed(processes):
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait(timeout=10)
        for log in logs:log.close()
        report['finishedAt']=time.time();save()
        (args.out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report.get(k) for k in ('passed','finalHeight','spendableRewardVerified','exception','productionReady')}))
    return report.get('passed',False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--blocks',type=int,default=3)
    p.add_argument('--attempts',type=int,default=20);p.add_argument('--seconds',type=int,default=3600)
    p.add_argument('--accelerator',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--flash-attention',choices=('auto','on','off'),default='off')
    p.add_argument('--spend',action='store_true')
    p.add_argument('--continue-after-rejection',action='store_true',
        help='Continue fresh work after RED, retaining every rejection and a failed acceptance result')
    p.add_argument('--check-indexer',action='store_true');p.add_argument('--restart',action='store_true')
    p.add_argument('--relabel-after-switch',type=int,default=0,
        help='Switch rehearsal: this many attempts after the switch run the genesis weights while claiming '
             'the switch model; each must be refused (research forgery, recorded apart from honest rejections)')
    a=p.parse_args()
    if not 0<=a.relabel_after_switch<=10 or (a.relabel_after_switch and not SWITCH):
        p.error('--relabel-after-switch needs a switch rehearsal and at most 10 attempts')
    if not 1<=a.blocks<=105 or not 1<=a.attempts<=400 or not 60<=a.seconds<=86400:p.error('Acceptance must stay bounded')
    if a.spend and a.blocks<101:p.error('Reward spend acceptance needs actual maturity')
    raise SystemExit(0 if run(a) else 2)
