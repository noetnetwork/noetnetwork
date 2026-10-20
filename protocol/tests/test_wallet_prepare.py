import copy
import unittest
from unittest.mock import Mock, patch
from noet import wallet_prepare as prepare
from noet import native_host

ADDRESS = 'nlab1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq50ehkw'
OTHER = 'nlab1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq50ehka'
PSBT = 'cHNidP8A'
REVIEW = {'outputs':[{'address':ADDRESS,'testNOET':'1.25000000','ownership':'External recipient'},
                     {'address':OTHER,'testNOET':'0.74999720','ownership':'This wallet (verify intended change)'}], 'broadcast':False}


class PrepareTransferTests(unittest.TestCase):
    def rpc(self, method, *args):
        if method == 'validateaddress': return {'isvalid':True,'scriptPubKey':'0014'+'ab'*20}
        if method == 'walletcreatefundedpsbt': return {'psbt':PSBT}
        raise AssertionError('Unexpected or signing RPC: '+method)

    def test_exact_intent_refuses_ambiguous_amounts_before_rpc(self):
        self.assertEqual(prepare.transfer_intent(ADDRESS, '1.25'), (ADDRESS,'1.25000000'))
        self.assertEqual(prepare.transfer_intent(ADDRESS, '0.00000001'), (ADDRESS,'0.00000001'))
        for value in ('0','-1','+1','1e-3','01.2','1.000000001','21000000.00000001',1.25,True,' 1.2'):
            rpc = Mock()
            with self.subTest(value=value),self.assertRaises(ValueError):prepare.prepare_transfer(rpc, ADDRESS,value)
            rpc.assert_not_called()
        for address in (ADDRESS+'\n',ADDRESS.upper(),'other1address',ADDRESS[:-1],None):
            with self.assertRaises(ValueError):prepare.transfer_intent(address,'1')

    @patch.object(prepare, 'review_psbt', return_value=REVIEW)
    def test_funds_only_requested_recipient_then_checks_all_outputs(self, review):
        rpc = Mock(side_effect=self.rpc)
        result = prepare.prepare_transfer(rpc,ADDRESS,'1.25')
        self.assertEqual(result,dict(psbt=PSBT,review=REVIEW,broadcast=False,testCoinsOnly=True))
        self.assertEqual(rpc.call_args_list[-1].args,('walletcreatefundedpsbt',[],[{ADDRESS:'1.25000000'}],0,
            {'fee_rate':2,'replaceable':True,'change_type':'bech32','lock_unspents':False,'include_watching':False},True))
        review.assert_called_once_with(rpc,PSBT)

    def test_wrong_amount_recipient_or_extra_external_output_refused(self):
        bad = []
        for key, value in (('testNOET','1.24000000'),('address',OTHER)):
            review=copy.deepcopy(REVIEW);review['outputs'][0][key]=value;bad.append(review)
        review=copy.deepcopy(REVIEW);review['outputs'][1]['ownership']='External recipient';bad.append(review)
        review=copy.deepcopy(REVIEW);review['outputs'].append(dict(review['outputs'][0]));bad.append(review)
        for review in bad:
            with patch.object(prepare,'review_psbt',return_value=review),self.assertRaises(ValueError):prepare.prepare_transfer(self.rpc,ADDRESS,'1.25')

    def test_invalid_checksum_or_script_is_refused_before_funding(self):
        for value in ({'isvalid':False},{'isvalid':True,'scriptPubKey':'0020'+'ab'*32},None):
            rpc=Mock(return_value=value)
            with self.assertRaises(ValueError):prepare.prepare_transfer(rpc,ADDRESS,'1.25')
            rpc.assert_called_once_with('validateaddress',ADDRESS)

    def test_native_host_rejects_browser_fee_options_and_invalid_amount_before_connection(self):
        for request in ({'method':'prepare','address':ADDRESS,'amount':'1','fee_rate':2000},
                        {'method':'prepare','address':ADDRESS,'amount':'0'},
                        {'method':'prepare','address':ADDRESS,'amount':'1','password':'not-allowed'}):
            rpc=Mock()
            with self.assertRaises(ValueError):native_host.handle(request,rpc,native_host.LAB_GENESIS)
            rpc.assert_not_called()
