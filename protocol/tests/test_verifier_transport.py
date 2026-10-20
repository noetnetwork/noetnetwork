import ast
import os
import types
import unittest
from unittest.mock import Mock, call, patch
from noet.verifier_transport import private_endpoint, check_request, MAX_REQUEST_BYTES
from noet.admission import AdmissionBudget, AdmissionFull
from noet import verifier_entry
from test_verifier_artifacts import extracted, SOURCE


class VerifierTransportTests(unittest.TestCase):
    def test_only_explicit_private_ipv4_endpoints(self):
        for host in ('127.0.0.1','10.0.0.2','172.16.1.2','192.168.1.5'):
            self.assertEqual(private_endpoint(host,6001),'tcp://'+host+':6001')
        for host in ('0.0.0.0','*','8.8.8.8','169.254.169.254','localhost','verifier.example','::1','fc00::1','127.1','127.0.0.1/path','127.0.0.1%eth0'):
            with self.subTest(host=host),self.assertRaises(ValueError):private_endpoint(host,6001)
        for port in (0,65536,True,6001.0,'6001'):
            with self.assertRaises(ValueError):private_endpoint('127.0.0.1',port)

    def test_envelope_bytes_and_limits(self):
        for message in (None,'payload',bytearray(8),b'',b'x'*7,b'x'*(MAX_REQUEST_BYTES+1)):
            with self.assertRaises(ValueError):check_request(message)
        check_request(b'x'*8);check_request(b'x'*MAX_REQUEST_BYTES)

    def test_remote_verdicts_rejected_before_application_import(self):
        for env in ({'REMOTE_VERIFY_ENABLED':'true'}, {'REMOTE_VERIFY_ENABLED':'yes'}, {'REMOTE_VERIFY_BASE_URL':'https://external.invalid'}):
            with patch.dict(os.environ,env,clear=True),patch.object(verifier_entry.runpy,'run_path') as run:
                with self.assertRaisesRegex(ValueError,'Remote verdict'):verifier_entry.main()
                run.assert_not_called()

    def test_main_remote_delegation_cannot_be_enabled_by_environment(self):
        tree=ast.parse((SOURCE/'main.py').read_text())
        assignments={node.targets[0].id:node.value for node in tree.body if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name)}
        for name,expected in [('REMOTE_VERIFY_ENABLED',False),('REMOTE_VERIFY_BASE_URL',None),('REMOTE_VERIFY_API_KEY',None)]:
            self.assertIsInstance(assignments[name],ast.Constant)
            self.assertIs(assignments[name].value,expected)

    def test_socket_limits_applied_before_bind_and_public_addresses_never_open_context(self):
        class StopAfterBind(Exception): pass
        zmq=Mock(PULL=1,MAXMSGSIZE=2,RCVHWM=3,LINGER=4)
        sock=zmq.Context.return_value.socket.return_value
        sock.bind.side_effect=StopAfterBind
        constants=types.SimpleNamespace(ZMQ_VERIFY_PULL_PORT=6001,ZMQ_VERIFY_PUSH_HOST='10.1.0.3',ZMQ_VERIFY_PUSH_PORT=7001)
        namespace={'logging':Mock(),'pow_v4_strict_mode':lambda:'enforce','StrictModeConfigError':ValueError,
            'POW_V4_MODE_ENFORCE':'enforce','POW_V4_MODE_ENFORCE_REQUESTED':'requested',
            'POW_V4_STRICT_MODE_ENV':'mode','constants':constants,'os':os,'zmq':zmq,
            'private_endpoint':private_endpoint,'MAX_REQUEST_BYTES':MAX_REQUEST_BYTES,'AdmissionBudget':AdmissionBudget}
        # main.py's first constructor is AsyncValidator.__init__.
        method=extracted('main.py','__init__',namespace)
        with patch.dict(os.environ,{'NOET_VERIFIER_BIND':'127.0.0.1'},clear=True),self.assertRaises(StopAfterBind):
            method(types.SimpleNamespace())
        self.assertEqual(sock.method_calls,[call.setsockopt(2,MAX_REQUEST_BYTES),call.setsockopt(3,4),call.setsockopt(4,0),call.bind('tcp://127.0.0.1:6001')])
        zmq.reset_mock()
        with patch.dict(os.environ,{'NOET_VERIFIER_BIND':'0.0.0.0'},clear=True),self.assertRaises(ValueError):
            method(types.SimpleNamespace())
        zmq.Context.assert_not_called()

    def test_bad_envelope_never_reaches_flatbuffer_parser(self):
        parser=Mock()
        method=extracted('main.py','enqueue_request',{'check_request':check_request,'ValidationRequest':parser,'AdmissionFull':AdmissionFull})
        obj=types.SimpleNamespace(logger=Mock(),send_error_response=Mock(),admission=AdmissionBudget())
        method(obj,b'x')
        parser.ValidationRequest.GetRootAs.assert_not_called()
        obj.send_error_response.assert_called_once_with(b'\x00'*32,kind='quick')

    def test_wrong_hash_length_stops_before_queueing(self):
        parser=Mock()
        request=parser.ValidationRequest.GetRootAs.return_value
        request.HashIdAsNumpy.return_value.tobytes.return_value=b'x'*31
        method=extracted('main.py','enqueue_request',{'check_request':check_request,'ValidationRequest':parser,'AdmissionFull':AdmissionFull})
        obj=types.SimpleNamespace(logger=Mock(),send_error_response=Mock(),admission=AdmissionBudget())
        method(obj,b'x'*8)
        request.ValidationType.assert_not_called()
        obj.send_error_response.assert_not_called()

    def test_guarded_entry_requires_loopback_for_both_plaintext_legs(self):
        for endpoint in ('ZMQ_VERIFY_PUSH_HOST', 'NOET_VERIFIER_BIND'):
            for host in ('10.0.0.2','0.0.0.0','8.8.8.8','localhost','::1'):
                with self.subTest(endpoint=endpoint,host=host),patch.dict(os.environ,{endpoint:host,'MODEL_NAME':'NOET/model','MODEL_COMMIT':'a'*40,'POW_PROMPT_GIBBERISH_CALIBRATED':'NOET/model@'+'a'*40+':bf16'},clear=True),patch.object(verifier_entry,'checked_model_directory') as model,patch.object(verifier_entry.runpy,'run_path') as run:
                    with self.assertRaises(ValueError):verifier_entry.main()
                    model.assert_not_called();run.assert_not_called()
