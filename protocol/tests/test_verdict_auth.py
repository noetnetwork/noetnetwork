import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from noet import verifier_entry

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'.upstream/services/verification-api/src/zmq_send_broker.py'


def broker_module():
    spec=importlib.util.spec_from_file_location('noet_verdict_broker_test',SOURCE)
    module=importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,{'zmq':types.ModuleType('zmq')}):spec.loader.exec_module(module)
    return module


class VerdictAuthTests(unittest.TestCase):
    def test_pinned_cross_language_vectors(self):
        module=broker_module()
        path=ROOT/'.upstream/shared-utils/pow-utils/tests/vectors/zmq_verdict_envelope_v1.json'
        vectors=json.loads(path.read_text())
        core=json.loads((ROOT/'.upstream/services/core-node/bcore/src/test/data/zmq_verdict_envelope_v1.json').read_text())
        self.assertEqual(vectors,core)
        for row in vectors['cases']:
            with self.subTest(name=row['name']):
                secret=module.load_shared_secret(row['secret_env'])
                self.assertEqual(secret.hex(),row['secret_hex'])
                envelope=module.build_envelope(secret,bytes.fromhex(row['request_id_hex']),row['type'],row['ruleset'],bytes.fromhex(row['payload_hex']))
                self.assertEqual(envelope.hex(),row['envelope_hex'])

    def test_broker_signs_payload_and_refuses_missing_metadata(self):
        module=broker_module();secret=b'a'*32
        broker=module.ZmqSendBroker('tcp://127.0.0.1:7001',shared_secret=secret)
        self.assertFalse(broker.submit(b'payload'))
        self.assertEqual(broker.q.qsize(),0)
        self.assertTrue(broker.submit(b'payload',request_id=b'h'*32,response_type=6,ruleset=2))
        frames=broker.q.get_nowait()
        self.assertEqual(frames,[b'payload',module.build_envelope(secret,b'h'*32,6,2,b'payload')])
        for payload,request_id,kind,rules in ((b'changed',b'h'*32,6,2),(b'payload',b'x'*32,6,2),(b'payload',b'h'*32,5,2),(b'payload',b'h'*32,6,1)):
            self.assertNotEqual(frames[1],module.build_envelope(secret,request_id,kind,rules,payload))

    def test_guarded_entry_requires_exact_secret_before_service_import(self):
        for secret in ('','short','a'*63,'g'*64,'ab'*32+'\n'):
            with patch.dict(os.environ,{'POW_PROMPT_GIBBERISH_CALIBRATED':'NOET/model@'+'a'*40+':bf16','MODEL_NAME':'NOET/model','MODEL_COMMIT':'a'*40,'VALIDATOR_SHARED_SECRET':secret},clear=True),patch.object(verifier_entry,'checked_model_directory',return_value='/approved'),patch.object(verifier_entry.runpy,'run_path') as run:
                with self.assertRaisesRegex(ValueError,'VALIDATOR_SHARED_SECRET'):verifier_entry.main()
                run.assert_not_called()

    def test_unsigned_nonlocal_broker_does_not_start_thread(self):
        module=broker_module()
        broker=module.ZmqSendBroker('tcp://10.0.0.2:7001',shared_secret=None)
        self.assertFalse(broker.start());self.assertTrue(broker.refused)
        self.assertIsNone(broker.thread)
