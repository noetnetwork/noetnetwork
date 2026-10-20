"""Exercise the native wallet bridge against a stopped private-work acceptance chain.

Uses only synthetic local test coins. Approval is a test callback, not installed UI.
No GPU work, network exposure, seed changes or consensus shortcuts are introduced.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time

ROOT=Path('/workspace/noet-gpu')
# The reviewed wallet candidate is isolated from the running miner's Python files.
sys.path.insert(0,str(ROOT/'wallet-check/protocol'))
from noet.native_host import handle
from noet.wallet_backup import backup_wallet
from noet.rpc import NodeRPC
from noet.wallet_network import PROFILES
from noet.indexer import Index, atoms
GENESIS=PROFILES['private-work']['genesis']


def run(source,out):
    if os.geteuid()==0:raise ValueError('Use the restricted test worker')
    previous=json.loads((source/'result.json').read_text())
    if previous.get('genesis')!=GENESIS or previous.get('spendableRewardVerified') is not True:
        raise ValueError('Requires a completed mature reward and payment check')
    try:
        with socket.create_connection(('127.0.0.1',30443),timeout=1):
            raise ValueError('Another test node is still running')
    except ConnectionRefusedError:pass
    out.mkdir(parents=True,mode=0o700,exist_ok=False)
    report={'format':'noet-native-wallet-realwork-v1','passed':False,'productionReady':False,
        'scope':'Encrypted wallet on previously mined private-work chain, synthetic coins, loopback RPC, test approval callback; no installed browser or desktop acceptance',
        'startedAt':time.time(),'genesis':GENESIS,'sourceResultSha256':hashlib.sha256((source/'result.json').read_bytes()).hexdigest()}
    data=source/'node';cookie=data/'tensor-reg/.cookie'
    rpc=NodeRPC('http://127.0.0.1:30443',cookie)
    miner=NodeRPC('http://127.0.0.1:30443/wallet/miner',cookie)
    recipient=NodeRPC('http://127.0.0.1:30443/wallet/recipient',cookie)
    env={**os.environ,'VALIDATOR_SHARED_SECRET':secrets.token_hex(32),'VALIDATOR_HOST':'127.0.0.1',
         'VALIDATOR_PULL_BIND':'127.0.0.1','VALIDATOR_PUSH_PORT':'18555','VALIDATOR_PULL_PORT':'18556'}
    command=[str(ROOT/'native-source/node-build/bin/bitcoind'),'-chain=tensor-reg','-datadir='+str(data),
        '-server=1','-rpcport=30443','-port=30441','-listen=0','-connect=0','-dnsseed=0','-fixedseeds=0',
        '-listenonion=0','-discover=0','-validationapi=real','-useextapi=1','-printtoconsole=1']
    process=None;password=None
    try:
        with (out/'node.log').open('xb') as log:
            process=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('Wallet test node exited early')
            try:
                if rpc('getblockhash',0)==GENESIS:break
            except Exception:pass
            time.sleep(.5)
        else:raise TimeoutError('Node did not start')
        expected=previous['acceptedBlocks'][-1]
        if rpc('getbestblockhash')!=expected['hash']:raise AssertionError('Canonical chain changed')
        report['height']=rpc('getblockcount');report['tip']=rpc('getbestblockhash')
        for name in ('miner','recipient'):
            if name not in rpc('listwallets'):rpc('loadwallet',name)
        password_path=data/'acceptance-wallet-password'
        if password_path.is_symlink() or password_path.stat().st_mode & 0o077:raise ValueError('Insecure synthetic wallet password file')
        password=password_path.read_text().strip()
        status=handle({'method':'status'},miner,GENESIS)
        if not status['locked'] or not status['broadcastSupported']:raise AssertionError('Expected locked sending wallet')
        report['status']=status;report['balancesBefore']=handle({'method':'balances'},miner,GENESIS)
        # Compare the upgraded explorer with the actual encrypted wallet before
        # the extra unconfirmed bridge payment changes its pending balance.
        index=Index(out/'explorer-v2.sqlite3',GENESIS)
        try:
            indexed=index.sync(rpc,limit=1000)
            if indexed['tip']!=expected['hash'] or indexed['lag']:
                raise AssertionError('Upgraded explorer differs from the native tip')
            totals={'balanceAtoms':0,'matureAtoms':0,'immatureAtoms':0}
            owned=[]
            for row in index.db.execute('SELECT DISTINCT address FROM outputs WHERE address IS NOT NULL'):
                if miner('getaddressinfo',row[0]).get('ismine'):
                    value=index.address(row[0]);owned.append(row[0])
                    for key in totals:totals[key]+=int(value[key])
            wallet=report['balancesBefore']
            if totals['matureAtoms']!=atoms(wallet['trusted']) or totals['immatureAtoms']!=atoms(wallet['immature']):
                raise AssertionError('Explorer maturity totals differ from the native wallet')
            transactions=[];cursor=None
            while True:
                page=index.transactions(cursor);transactions.extend(row['txid'] for row in page['transactions'])
                cursor=page['nextCursor']
                if cursor is None:break
            if len(transactions)!=len(set(transactions)) or len(transactions)!=index.db.execute('SELECT count(*) FROM transactions').fetchone()[0]:
                raise AssertionError('Explorer transaction pagination lost or duplicated a transaction')
            confirmed=index.transaction(previous['rewardSpend']['txid'])
            if not confirmed or confirmed['confirmations']<1:
                raise AssertionError('Explorer did not identify the confirmed reward payment')
            for vin in confirmed['inputs']:
                previous_output=index.transaction(vin['prev_txid'])['outputs'][vin['prev_n']]
                if previous_output['spentBy']!=confirmed['txid']:
                    raise AssertionError('Explorer spend link differs from confirmed transaction')
            report['upgradedExplorer']={'passed':True,'status':indexed,'walletAddresses':owned,
                'walletTotals':{key:str(value) for key,value in totals.items()},
                'pagedTransactions':len(transactions),'statistics':index.statistics(),
                'maturityRule':'100 blocks after coinbase, matching native wallet depth 101'}
        finally:index.db.close()
        destination=recipient('getnewaddress','','bech32')
        prepared=handle({'method':'prepare','address':destination,'amount':'0.25'},miner,GENESIS)
        if miner('getwalletinfo')['unlocked_until']!=0:raise AssertionError('Preparation unlocked wallet')
        if prepared['review']['networkGenesis']!=GENESIS:raise AssertionError('Review omitted network identity')
        before=rpc('getrawmempool')
        cancelled=handle({'method':'send','psbt':prepared['psbt']},miner,GENESIS,approve_send=lambda _:None)
        if not cancelled.get('cancelled') or rpc('getrawmempool')!=before:raise AssertionError('Cancellation changed mempool')
        signed=handle({'method':'sign','psbt':prepared['psbt']},miner,GENESIS,approve=lambda _:password)
        if signed.get('broadcast') is not False or rpc('getrawmempool')!=before:raise AssertionError('Sign-only broadcast a transaction')
        if miner('getwalletinfo')['unlocked_until']!=0:raise AssertionError('Signing left wallet unlocked')
        sent=handle({'method':'send','psbt':prepared['psbt']},miner,GENESIS,approve_send=lambda _:password)
        if not sent['broadcast'] or sent['txid']!=signed['txid']:raise AssertionError('Unexpected signed payment identity')
        entry=rpc('getmempoolentry',sent['txid'])
        if miner('getwalletinfo')['unlocked_until']!=0:raise AssertionError('Sending left wallet unlocked')
        def no_second_approval(_):raise AssertionError('Spent draft reached another approval')
        try:handle({'method':'send','psbt':prepared['psbt']},miner,GENESIS,approve_send=no_second_approval)
        except ValueError:report['spentDraftRefused']=True
        else:raise AssertionError('Already spent draft accepted')
        history=handle({'method':'history'},miner,GENESIS)
        if not any(row['txid']==sent['txid'] and row['amount']=='-0.25000000' for row in history['transactions']):raise AssertionError('Payment absent from wallet history')
        if rpc('getbestblockhash')!=expected['hash']:raise AssertionError('Wallet test unexpectedly changed chain')
        backup=data/'bridge-encrypted-backup.dat'
        saved=backup_wallet(miner,GENESIS,PROFILES['private-work']['name'],choose=lambda _:backup)
        if not saved.get('saved'):raise AssertionError('Encrypted backup was not saved')
        rpc('restorewallet','bridge-recovered',str(backup),True)
        restored=NodeRPC('http://127.0.0.1:30443/wallet/bridge-recovered',cookie)
        if restored('getwalletinfo').get('unlocked_until')!=0:raise AssertionError('Restored wallet is not encrypted and locked')
        if not restored('getaddressinfo',previous['rewardAddress']).get('ismine'):raise AssertionError('Backup lost mining keys')
        restored('walletpassphrase',password,10)
        try:
            if restored('getwalletinfo')['unlocked_until']<=0:raise AssertionError('Restored passphrase did not unlock')
        finally:restored('walletlock')
        report['encryptedBackupRestored']=True
        report['recoveredWalletLocked']=restored('getwalletinfo')['unlocked_until']==0
        rpc('unloadwallet','bridge-recovered')
        report.update(passed=True,cancellationDidNotBroadcast=True,signOnlyDidNotBroadcast=True,walletRelocked=True,
            payment=sent,mempool=entry,balancesAfter=handle({'method':'balances'},miner,GENESIS),history=history,
            confirmed=False,confirmationNote='This extra bridge payment remains in the test mempool; the earlier one-unit mining reward payment was confirmed at block 102.')
    except Exception as error:
        report.update(exceptionType=type(error).__name__,exception=str(error)[:1000])
    finally:
        password=None
        if process is not None:
            try:rpc('stop')
            except Exception:pass
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=10)
        report['finishedAt']=time.time()
        (out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({key:report.get(key) for key in ('passed','height','walletRelocked','spentDraftRefused','exceptionType')}))
    return report['passed']

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();sys.exit(0 if run(args.source,args.out) else 1)
