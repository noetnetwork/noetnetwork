import hashlib
from pathlib import Path
import struct
import unittest
from realwork.prepare import overlay

ROOT=Path(__file__).resolve().parents[1]
CORE=ROOT/'.upstream/services/core-node/bcore'


class PrivateWorkProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original=(CORE/'src/kernel/chainparams.cpp').read_text()
        cls.init=(CORE/'src/init.cpp').read_text()

    def test_overlay_only_changes_private_chain_and_validation_startup(self):
        chain,init=overlay(self.original,self.init)
        marker='class CTensorRegParams'
        self.assertEqual(chain.split(marker)[0],self.original.split(marker)[0])
        self.assertEqual(chain.split('\n};',chain[:chain.index(marker)].count('\n};')+1)[-1],
                         self.original.split('\n};',self.original[:self.original.index(marker)].count('\n};')+1)[-1])
        self.assertIn('m_is_mockable_chain = false;',chain[chain.index(marker):])
        self.assertIn('consensus.external_api = true;',chain[chain.index(marker):])
        self.assertIn('api_mode != "real"',init)
        self.assertIn('!args.GetBoolArg("-useextapi", true)',init)

    def test_private_genesis_nonce_meets_actual_short_header_target(self):
        prefix=struct.pack('<I',1)+bytes(32)+bytes.fromhex(
            '4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b')[::-1]
        header=prefix+struct.pack('<III',1791676860,0x207fffff,2)
        short=hashlib.sha256(hashlib.sha256(header).digest()).digest()
        self.assertLess(int.from_bytes(short,'little'),0x7fffff<<(8*29))
        self.assertEqual(short[::-1].hex(),'28a13eaf9061bd60c77e6f532ea1b7857dc859558ba1a003edd3bea3ac17ac46')

    def test_unexpected_source_fails_closed(self):
        with self.assertRaises(ValueError):overlay(self.original.replace('nDefaultPort = 29441;','nDefaultPort = 9999;'),self.init)

    def test_activation_schedule_is_fixed_and_window_is_complete(self):
        chain,_=overlay(self.original,self.init)
        block=chain[chain.index('class CTensorRegParams'):].split('\n};')[0]
        for field,value in {'V3ActivationHeight':1,'StepBindHeight':1,'ProofV4Height':1,
            'V3PenaltyHeight':2,'V3SunsetHeight':3,'ProofHistoryCollectHeight':1,
            'ProofHistoryEnforceHeight':2,'nEnforcedProofWindowSize':256}.items():
            self.assertIn(f'consensus.{field} = {value};',block)
        self.assertIn('bech32_hrp = "nwrk";',block)
        self.assertNotIn('testModel@testModelCommit',block)
