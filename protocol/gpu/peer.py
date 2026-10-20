"""Fresh native peer acceptance over loopback on one research host, not decentralization."""
import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
from native import ROOT, GENESIS
from noet.rpc import NodeRPC
from noet.indexer import Index


def run(args):
    if os.geteuid()==0:raise ValueError('Use the restricted GPU worker')
    source=json.loads((args.source/'result.json').read_text())
    if source.get('genesis')!=GENESIS or not source.get('acceptedBlocks'):
        raise ValueError('Expected previously mined real-work evidence')
    expected=source['acceptedBlocks'][-1]
    if (args.source/'node').is_symlink():raise ValueError('Native data directory cannot be a symlink')
    args.out.mkdir(parents=True,mode=0o700,exist_ok=False)
    follower=args.out/'node';follower.mkdir(mode=0o700)
    result={'format':'noet-realwork-peer-v1','passed':False,'productionReady':False,
        'expectedHeight':expected['height'],'expectedTip':expected['hash'],
        'scope':'Two native processes on one host, actual P2P transfer and separate Full verification; no independent operators',
        'startedAt':time.time()}
    deadline=time.monotonic()+args.seconds
    processes=[];logs=[]
    def start(command,environment,name):
        log=(args.out/name).open('xb');logs.append(log)
        child=subprocess.Popen(command,env=environment,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        processes.append(child);return child
    def wait(check,seconds):
        until=min(deadline,time.monotonic()+seconds)
        while time.monotonic()<until:
            try:
                value=check()
                if value:return value
            except Exception:pass
            if any(child.poll() is not None for child in processes):raise RuntimeError('Acceptance process exited early')
            time.sleep(1)
        raise TimeoutError('Fresh peer did not reach the required acceptance state')
    def environment(push,pull):
        return {**os.environ,'VALIDATOR_SHARED_SECRET':secrets.token_hex(32),'VALIDATOR_HOST':'127.0.0.1',
            'VALIDATOR_PULL_BIND':'127.0.0.1','VALIDATOR_PUSH_PORT':str(push),'VALIDATOR_PULL_PORT':str(pull)}
    seed_rpc=NodeRPC('http://127.0.0.1:30443',args.source/'node/tensor-reg/.cookie')
    peer_rpc=NodeRPC('http://127.0.0.1:30444',follower/'tensor-reg/.cookie')
    try:
        for name,data,rpcport,p2pport,push,pull,connection in (
            ('seed',args.source/'node',30443,30441,18555,18556,'-connect=0'),
            ('peer',follower,30444,30442,18557,18558,'-connect=127.0.0.1:30441')):
            env=environment(push,pull)
            start([sys.executable,str(ROOT/'checks/verify.py'),'--out',str(args.out/(name+'-verifier')),
                '--seconds',str(args.seconds),'--capture-detector'],env,name+'-verifier.log')
            wait(lambda:(args.out/(name+'-verifier')/'ready.json').exists(),120)
            command=[str(ROOT/'native-source/node-build/bin/bitcoind'),'-chain=tensor-reg',
                '-datadir='+str(data),'-server=1','-rpcport='+str(rpcport),'-port='+str(p2pport),
                '-bind=127.0.0.1:'+str(p2pport),'-listen=1','-dnsseed=0','-fixedseeds=0',
                '-listenonion=0','-discover=0',connection,'-validationapi=real','-useextapi=1',
                '-disablewallet=1','-printtoconsole=1']
            start(command,env,name+'-node.log')
            rpc=seed_rpc if name=='seed' else peer_rpc
            wait(lambda:rpc('getblockhash',0)==GENESIS,120)
            if name=='seed' and rpc('getbestblockhash')!=expected['hash']:
                raise AssertionError('Source chain differs from recorded acceptance')
        wait(lambda:peer_rpc('getconnectioncount')==1,90)
        wait(lambda:peer_rpc('getblockcount')==expected['height'] and peer_rpc('getbestblockhash')==expected['hash'],args.seconds)
        result['peerHeight']=peer_rpc('getblockcount');result['peerTip']=peer_rpc('getbestblockhash')
        result['connections']=peer_rpc('getpeerinfo')
        result['recentValidation']=peer_rpc('getrecentvalidations',100)
        result['validationInfo']=peer_rpc('getvalidationapiinfo')
        if result['validationInfo']['mode']!='real':raise AssertionError('Peer used an unexpected verifier mode')
        computed=[]
        for path in sorted((args.out/'peer-verifier').glob('*/result.json')):
            row=json.loads(path.read_text())
            if row.get('requestType')==2 and not row.get('reusedExactEnvelope'):
                computed.append(row)
        result['computedFull']=computed
        if {row.get('height') for row in computed}!=set(range(1,expected['height']+1)):
            raise AssertionError('Fresh peer lacks independent Full coverage for every mined block')
        if any(row.get('fullVerdict')!='GREEN' or row.get('exception') or row.get('detectorCaptureError') for row in computed):
            raise AssertionError('Fresh peer Full verification did not complete cleanly')
        for row in computed:
            if row['height']>=3 and ((row.get('detector') or {}).get('detector_status')!='required'
                    or row.get('detectorRecordStatus')!='scored'
                    or row.get('replayViews')!=['1','2','5','10','20','spda']):
                raise AssertionError('Fresh peer mandatory detector coverage is incomplete')
        index=Index(args.out/'explorer.sqlite3',GENESIS)
        try:
            result['explorer']=index.sync(peer_rpc,limit=1000)
            if result['explorer']['tip']!=expected['hash'] or result['explorer']['lag']:
                raise AssertionError('Fresh peer explorer differs from native tip')
            if source.get('spendableRewardVerified'):
                address=index.address(source['rewardSpend']['recipient'])
                if address['matureAtoms']!='100000000':raise AssertionError('Fresh peer missed the confirmed reward spend')
                result['recipient']=address
        finally:index.db.close()
        result['passed']=True
    except (Exception,KeyboardInterrupt) as error:
        result.update(exceptionType=type(error).__name__,exception=str(error)[:1000])
    finally:
        for rpc in (peer_rpc,seed_rpc):
            try:rpc('stop')
            except Exception:pass
        for child in reversed(processes):
            try:os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=10)
        for log in logs:log.close()
        result['finishedAt']=time.time()
        (args.out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({key:result.get(key) for key in ('passed','peerHeight','exception','productionReady')}))
    return result['passed']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=3600)
    args=parser.parse_args()
    if not 60<=args.seconds<=7200:parser.error('Bound this test to two hours')
    raise SystemExit(0 if run(args) else 2)
