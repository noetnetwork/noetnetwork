import base64
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from noet.wallet import main
from noet.wallet import read_psbt, write_private
from noet.wallet_review import review_psbt

SCRIPT = '0014' + 'ab' * 20
ADDRESS = 'nlab1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq50ehkw'
class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.output = {'value':'2.0','scriptPubKey':{'type':'witness_v0_keyhash','hex':SCRIPT,'address':ADDRESS}}
        self.decoded = {'psbt_version':0,'tx':{'version':2,'locktime':0,'vin':[{'txid':'a'*64,'vout':0,'sequence':0xfffffffd}], 'vout':[dict(self.output,value='1.9999')]},'inputs':[{'witness_utxo':{'amount':'2.0','scriptPubKey':self.output['scriptPubKey']}}],'outputs':[{}],'fee':'0.0001'}
        self.live=dict(self.output,confirmations=100,coinbase=True)
        self.analysis={'estimated_vsize':150}
        self.calls=[]
    def rpc(self,method,*args):
        self.calls.append(method)
        if method=='decodepsbt':return self.decoded
        if method=='analyzepsbt':return self.analysis
        if method=='gettxout':return self.live
        if method=='getaddressinfo':return {'scriptPubKey':SCRIPT,'ismine':True}
        raise AssertionError(method)
    def test_review_without_unlock_or_sign(self):
        result=review_psbt(self.rpc,'cHNidP8=')
        self.assertEqual(result['feeTestNOET'],'0.00010000')
        self.assertEqual(result['outputs'][0]['testNOET'],'1.99990000')
        self.assertTrue(result['replaceable'])
        self.assertFalse(result['broadcast'])
        self.assertNotIn('walletpassphrase',self.calls)
    def test_reject_non_all_or_extensions(self):
        for patch in [{'sighash':'NONE'},{'sighash':'ALL|ANYONECANPAY'},{'unknown':{'key':'value'}},{'partial_signatures':{'key':'value'}},{'final_scriptwitness':['value']}]:
            with self.subTest(patch=patch):
                original=copy.deepcopy(self.decoded['inputs'][0]);self.decoded['inputs'][0].update(patch)
                with self.assertRaises(ValueError):review_psbt(self.rpc,'cHNidP8=')
                self.decoded['inputs'][0]=original
    def test_reject_missing_spent_or_falsified_inputs(self):
        for live in [None,dict(self.output,value='3'),dict(self.output,coinbase=True,confirmations=99)]:
            self.live=live
            with self.assertRaises(ValueError):review_psbt(self.rpc,'cHNidP8=')
    def test_fee_total_rate_and_inconsistency(self):
        with self.assertRaises(ValueError):review_psbt(self.rpc,'cHNidP8=',max_fee='0.00001')
        self.analysis['estimated_vsize']=10
        with self.assertRaises(ValueError):review_psbt(self.rpc,'cHNidP8=')
        self.analysis['estimated_vsize']=150;self.decoded['fee']='0.0002'
        with self.assertRaises(ValueError):review_psbt(self.rpc,'cHNidP8=')
    def test_reject_custom_script_or_transaction(self):
        self.decoded['tx']['version']=3
        with self.assertRaises(ValueError):review_psbt(self.rpc,'cHNidP8=')
        self.decoded['tx']['version']=2
        self.decoded['tx']['vout'][0]=copy.deepcopy(self.output)
        self.decoded['tx']['vout'][0]['scriptPubKey']['hex']='6a00'
        with self.assertRaises(ValueError):review_psbt(self.rpc,'cHNidP8=')

class FileTests(unittest.TestCase):
    def test_private_no_overwrite_and_bounded_regular_read(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'tx.psbt';text=base64.b64encode(b'psbt\xff\x00').decode()
            write_private(p,text);self.assertEqual(read_psbt(p),text)
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            with self.assertRaises(FileExistsError):write_private(p,'other')
            link=Path(folder)/'link';link.symlink_to(p)
            with self.assertRaises(OSError):read_psbt(link)
            fifo=Path(folder)/'fifo';os.mkfifo(fifo)
            with self.assertRaises(ValueError):read_psbt(fifo)
            for bad in ['not base64!',base64.b64encode(b'hello').decode(),'a'*(1024*1024+1)]:
                p.write_text(bad)
                with self.assertRaises(ValueError):read_psbt(p)

class CommandTests(unittest.TestCase):
    def run_sign(self, rpc, review_error=None):
        with patch('sys.argv', ['wallet', 'sign', 'input.psbt', '--out', 'signed.psbt']), \
             patch.dict(os.environ, {'NOET_GENESIS_HASH': 'a' * 64}), \
             patch('noet.wallet.NodeRPC.from_env', return_value=rpc), \
             patch('noet.wallet.verify_network'), \
             patch('noet.wallet.read_psbt', return_value='cHNidP8='), \
             patch('noet.wallet.review_psbt', return_value={}, side_effect=review_error), \
             patch('builtins.input', return_value='SIGN'), \
             patch('noet.wallet.getpass', return_value='test-only'), \
             patch('noet.wallet.write_private') as write, \
             patch('builtins.print') as output:
            main()
            return write, output

    def test_rejected_review_never_unlocks(self):
        calls=[]
        with self.assertRaises(ValueError):
            self.run_sign(lambda method,*args: calls.append(method), ValueError('unsafe'))
        self.assertEqual(calls, [])

    def test_sign_failure_relocks(self):
        calls=[]
        def rpc(method,*args):
            calls.append(method)
            if method == 'walletprocesspsbt':
                raise ValueError('signing failed')
        with self.assertRaises(ValueError):self.run_sign(rpc)
        self.assertEqual(calls, ['walletpassphrase', 'walletprocesspsbt', 'walletlock'])

    def test_success_prints_txid_without_broadcast(self):
        calls=[]
        def rpc(method,*args):
            calls.append(method)
            if method == 'walletprocesspsbt':
                self.assertEqual(args[2], 'ALL')
                return {'complete':True, 'psbt':'signed'}
            if method == 'finalizepsbt':return {'complete':True, 'hex':'00'}
            if method == 'decoderawtransaction':return {'txid':'b'*64}
        write, output=self.run_sign(rpc)
        write.assert_called_once_with('signed.psbt', 'signed')
        self.assertEqual(calls, ['walletpassphrase', 'walletprocesspsbt', 'walletlock', 'finalizepsbt', 'decoderawtransaction'])
        self.assertIn('b'*64, output.call_args.args[0])
