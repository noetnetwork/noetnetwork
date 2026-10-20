import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
from noet import native_host as host

PSBT = 'cHNidP8A'


class NativeHostTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.info = {'unlocked_until':0, 'private_keys_enabled':True}
        self.genesis = host.LAB_GENESIS
    def rpc(self, method, *args):
        self.calls.append((method, args))
        return {'getblockhash':self.genesis, 'getblockchaininfo':{'chain':'tensor-reg'},
                'getwalletinfo':self.info, 'getnewaddress':'nlab1fixture',
                'walletprocesspsbt':{'complete':True, 'psbt':PSBT},
                'finalizepsbt':{'complete':True, 'hex':'00'},
                'decoderawtransaction':{'txid':'b'*64}}.get(method)
    def test_status_and_receive_never_unlock(self):
        self.assertTrue(host.handle({'method':'status'}, self.rpc, self.genesis)['locked'])
        self.assertEqual(host.handle({'method':'receive'}, self.rpc, self.genesis)['address'], 'nlab1fixture')
        self.assertNotIn('walletpassphrase', [m for m,_ in self.calls])
    def test_unrecognized_methods_and_browser_passwords_rejected(self):
        for request in ({'method':'backup','path':'/tmp/browser-chosen'}, {'method':'broadcast'}, {'method':'sign','psbt':PSBT,'password':'bad'}, {'method':'sign','psbt':PSBT,'approve':True}, {'method':'review','psbt':PSBT,'maxFee':'100'}, {'method':'status','rpcUrl':'http://other'}):
            with self.subTest(request=request), self.assertRaises(ValueError):host.handle(request,self.rpc,self.genesis)
        self.assertEqual(self.calls, [])
    @patch.object(host, 'review_psbt', return_value={'fingerprint':'fixed'})
    def test_cancel_and_review_never_unlock(self, review):
        for method in ('review','sign'):
            result=host.handle({'method':method,'psbt':PSBT},self.rpc,self.genesis,approve=lambda review:None)
            if method=='sign':self.assertTrue(result['cancelled'])
        self.assertNotIn('walletpassphrase',[m for m,_ in self.calls])
    @patch.object(host, 'review_psbt', return_value={'fingerprint':'fixed'})
    def test_native_approval_signs_then_locks_without_broadcast(self, review):
        approve=Mock(return_value='local-only-password')
        result=host.handle({'method':'sign','psbt':PSBT},self.rpc,self.genesis,approve)
        approve.assert_called_once_with({'fingerprint':'fixed', 'networkGenesis':self.genesis, 'networkName':'NOET laboratory'})
        self.assertEqual(review.call_count,2)
        methods=[m for m,_ in self.calls]
        self.assertLess(methods.index('walletprocesspsbt'), methods.index('walletlock'))
        self.assertNotIn('sendrawtransaction',methods)
        self.assertNotIn('local-only-password',json.dumps(result))
        self.assertFalse(result['broadcast'])
        self.assertEqual(result['psbt'],PSBT)
    @patch.object(host, 'review_psbt', side_effect=[{'fee':1},{'fee':2}])
    def test_changed_review_rejected_before_unlock(self, review):
        with self.assertRaises(ValueError):host.handle({'method':'sign','psbt':PSBT},self.rpc,self.genesis,lambda review:'password')
        self.assertNotIn('walletpassphrase',[m for m,_ in self.calls])
    @patch.object(host, 'review_psbt', return_value={})
    def test_signing_error_relocks(self, review):
        def rpc(method,*args):
            if method=='walletprocesspsbt':raise RuntimeError('failure')
            return self.rpc(method,*args)
        with self.assertRaises(RuntimeError):host.handle({'method':'sign','psbt':PSBT},rpc,self.genesis,lambda review:'password')
        self.assertEqual(self.calls[-1][0],'walletlock')
    def test_wrong_network_and_unencrypted_wallet_rejected(self):
        with self.assertRaises(ValueError):host.handle({'method':'status'},self.rpc,'f'*64)
        self.info={}
        with self.assertRaises(ValueError):host.handle({'method':'status'},self.rpc,self.genesis)
    def test_bounded_native_framing(self):
        message={'method':'status'}
        stream=io.BytesIO();host.write_message(stream,message);stream.seek(0)
        self.assertEqual(int.from_bytes(stream.getvalue()[:4], sys.byteorder), len(stream.getvalue()) - 4)
        self.assertEqual(host.read_message(stream),message)
        self.assertIsNone(host.read_message(io.BytesIO()))
        for data in (b'\x01',struct.pack('=I',host.MAX_MESSAGE+1),struct.pack('=I',20)+b'{}',struct.pack('=I',0)):
            with self.assertRaises(ValueError):host.read_message(io.BytesIO(data))
        raw=b'{"method":"status","method":"sign"}'
        with self.assertRaises(ValueError):host.read_message(io.BytesIO(struct.pack('=I',len(raw))+raw))
    def test_config_restricts_origin_permissions_node_and_genesis(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'host.json'
            config={'extensionId':'a'*32,'rpcUrl':'http://127.0.0.1:29443/wallet/personal','cookieFile':'/tmp/cookie','genesis':self.genesis}
            path.write_text(json.dumps(config));path.chmod(0o600)
            with patch.object(host,'NodeRPC',return_value=self.rpc):
                host.load_config(path,'chrome-extension://'+'a'*32+'/')
                for key,value in (('rpcUrl','https://example.com/wallet/personal'),('rpcUrl','http://localhost/'),('genesis','f'*64)):
                    path.write_text(json.dumps(dict(config,**{key:value})))
                    with self.assertRaises(ValueError):host.load_config(path,'chrome-extension://'+'a'*32+'/')
                path.write_text(json.dumps(config))
                with self.assertRaises(ValueError):host.load_config(path,'chrome-extension://'+'b'*32+'/')
                path.chmod(0o644)
                with self.assertRaises(ValueError):host.load_config(path,'chrome-extension://'+'a'*32+'/')

class NativeSendTests(unittest.TestCase):
    setUp = NativeHostTests.setUp
    rpc = NativeHostTests.rpc
    @patch.object(host, 'review_psbt', return_value={'outputs':[], 'fingerprint':'fixed'})
    def test_send_requires_its_own_approval_then_locks_before_broadcast(self, review):
        approve = Mock(return_value='test-only')
        def rpc(method,*args):
            if method == 'testmempoolaccept':
                self.calls.append((method,args));return [{'allowed':True,'txid':'b'*64}]
            if method == 'sendrawtransaction':
                self.calls.append((method,args));return 'b'*64
            return self.rpc(method,*args)
        result=host.handle({'method':'send','psbt':PSBT},rpc,self.genesis,approve=Mock(side_effect=AssertionError('sign-only approval used')),approve_send=approve)
        methods=[m for m,_ in self.calls]
        self.assertTrue(result['broadcast'])
        self.assertEqual(result['genesis'],self.genesis)
        self.assertLess(methods.index('walletlock'),methods.index('testmempoolaccept'))
        self.assertLess(methods.index('testmempoolaccept'),methods.index('sendrawtransaction'))
        self.assertEqual(self.calls[-1],('sendrawtransaction',('00','0.001')))
        self.assertNotIn('test-only',json.dumps(result))
        approve.assert_called_once()

    @patch.object(host,'review_psbt',return_value={'outputs':[]})
    def test_send_cancellation_never_signs_or_broadcasts(self, review):
        result=host.handle({'method':'send','psbt':PSBT},self.rpc,self.genesis,approve_send=lambda _:None)
        self.assertTrue(result['cancelled'])
        self.assertNotIn('walletpassphrase',[m for m,_ in self.calls])
        self.assertNotIn('sendrawtransaction',[m for m,_ in self.calls])

    @patch.object(host,'review_psbt',return_value={'outputs':[]})
    def test_mempool_rejection_and_network_change_never_broadcast(self, review):
        def rpc(method,*args):
            if method=='testmempoolaccept': return [{'allowed':False,'txid':'b'*64}]
            return self.rpc(method,*args)
        with self.assertRaises(ValueError):host.handle({'method':'send','psbt':PSBT},rpc,self.genesis,approve_send=lambda _:'test-only')
        self.assertNotIn('sendrawtransaction',[m for m,_ in self.calls])
        self.calls=[]
        def changed(_):
            self.genesis='f'*64
            return 'test-only'
        with self.assertRaises(RuntimeError):host.handle({'method':'send','psbt':PSBT},self.rpc,host.LAB_GENESIS,approve_send=changed)
        self.assertNotIn('walletpassphrase',[m for m,_ in self.calls])

    def test_private_work_profile_and_wrong_network_recipient(self):
        from noet.wallet_network import PROFILES
        self.genesis=PROFILES['private-work']['genesis']
        result=host.handle({'method':'status'},self.rpc,self.genesis)
        self.assertEqual(result['addressPrefix'],'nwrk')
        self.calls=[]
        with self.assertRaises(ValueError):host.handle({'method':'prepare','address':'nlab1q'+'q'*38,'amount':'1'},self.rpc,self.genesis)
        self.assertEqual(self.calls,[])

    def test_balances_are_exact_and_history_is_bounded_without_unlocking(self):
        def rpc(method,*args):
            if method=='getbalances':return {'mine':{'trusted':'1.00000001','untrusted_pending':'0.00000000','immature':'715'}}
            if method=='listtransactions':
                self.assertEqual(args,('*',25,0,False))
                return [{'txid':'a'*64,'amount':'-0.00000001','category':'send','confirmations':1,'time':123}]
            return self.rpc(method,*args)
        self.assertEqual(host.handle({'method':'balances'},rpc,self.genesis)['trusted'],'1.00000001')
        self.assertEqual(host.handle({'method':'history'},rpc,self.genesis)['transactions'][0]['amount'],'-0.00000001')
        self.assertNotIn('walletpassphrase',[m for m,_ in self.calls])
        with self.assertRaises(ValueError):host.handle({'method':'history','count':999999},rpc,self.genesis)
