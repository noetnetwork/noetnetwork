import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch, Mock
from noet import model_runtime as runtime
from noet.model_artifacts import create

SUPERVISOR=Path(__file__).resolve().parents[1]/'.upstream/services/miner-api/vllm_supervisor.py'

class ModelRuntimeTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.folder=Path(folder.name);self.root=self.folder/'model';self.root.mkdir()
        (self.root/'config.json').write_text('{}')
        (self.root/'model.safetensors').write_bytes(b'fixture only')
        self.manifest=self.folder/'manifest.json'
        self.manifest.write_text(json.dumps(create(self.root,'NOET/fixture','a'*40,'test fixture')))
        self.env={'NOET_MODEL_ROOT':str(self.root),'NOET_MODEL_MANIFEST':str(self.manifest),
                  'NOET_MODEL_MANIFEST_SHA256':hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
                  'API_KEY':'a'*48,'VLLM_ADMIN_KEY':'b'*48,'MODEL_NAME':'NOET/fixture','MODEL_COMMIT':'a'*40}
        self.environment=patch.dict(os.environ,self.env,clear=True);self.environment.start();self.addCleanup(self.environment.stop)
    def readonly(self):return patch.object(runtime.os,'statvfs',return_value=types.SimpleNamespace(f_flag=os.ST_RDONLY))
    def test_verified_snapshot_and_exact_identity(self):
        with self.readonly():self.assertEqual(runtime.checked_model_directory('NOET/fixture','a'*40),str(self.root))
        for model,revision in [('other/model','a'*40),('NOET/fixture','main')]:
            with self.readonly(),self.assertRaises(ValueError):runtime.checked_model_directory(model,revision)
    def test_second_snapshot_serves_only_its_own_model(self):
        # Across a model switch one verifier holds both consensus models (design/MODEL-SWITCH.md).
        second=self.folder/'second';second.mkdir();(second/'config.json').write_text('{"n":2}')
        manifest=self.folder/'second.json';manifest.write_text(json.dumps(create(second,'NOET/switch','b'*40,'test fixture')))
        slot={'NOET_MODEL_ROOT_2':str(second),'NOET_MODEL_MANIFEST_2':str(manifest),
              'NOET_MODEL_MANIFEST_SHA256_2':hashlib.sha256(manifest.read_bytes()).hexdigest()}
        with self.readonly(),patch.dict(os.environ,slot):
            self.assertEqual(runtime.checked_model_directory('NOET/fixture','a'*40),str(self.root))
            self.assertEqual(runtime.checked_model_directory('NOET/switch','b'*40),str(second))
            for model,revision in [('NOET/switch','a'*40),('other/model','b'*40)]:
                with self.assertRaisesRegex(ValueError,'outside the reviewed'):runtime.checked_model_directory(model,revision)
            with patch.dict(os.environ,{'NOET_MODEL_MANIFEST_SHA256_2':'0'*64}),self.assertRaises(ValueError):
                runtime.checked_model_directory('NOET/switch','b'*40)
        with self.readonly(),self.assertRaisesRegex(ValueError,'outside the reviewed'):
            runtime.checked_model_directory('NOET/switch','b'*40)  # no second slot configured
    def test_writable_root_and_nested_mount_rejected(self):
        with patch.object(runtime.os,'statvfs',return_value=types.SimpleNamespace(f_flag=0)),self.assertRaisesRegex(ValueError,'read-only'):
            runtime.checked_model_directory('NOET/fixture','a'*40)
        with patch.object(runtime.os,'statvfs',side_effect=[types.SimpleNamespace(f_flag=os.ST_RDONLY),types.SimpleNamespace(f_flag=0)]),self.assertRaisesRegex(ValueError,'read-only'):
            runtime.checked_model_directory('NOET/fixture','a'*40)
    def test_changed_files_and_wrong_manifest_pin_rejected(self):
        with self.readonly():
            with patch.dict(os.environ,{'NOET_MODEL_MANIFEST_SHA256':'0'*64}),self.assertRaises(ValueError):runtime.checked_model_directory('NOET/fixture','a'*40)
            (self.root/'model.safetensors').write_bytes(b'changed')
            with self.assertRaises(ValueError):runtime.checked_model_directory('NOET/fixture','a'*40)
    def test_manifest_and_root_links_rejected(self):
        original=self.folder/'original.json';self.manifest.rename(original);self.manifest.symlink_to(original)
        with self.assertRaises(OSError):runtime.checked_model_directory('NOET/fixture','a'*40)
        link=self.folder/'model-link';link.symlink_to(self.root)
        with patch.dict(os.environ,{'NOET_MODEL_ROOT':str(link)}),self.assertRaises(ValueError):runtime.checked_model_directory('NOET/fixture','a'*40)
    def test_offline_child_does_not_inherit_admin_or_hub_secrets(self):
        with patch.dict(os.environ,{'HF_TOKEN':'secret','HUGGING_FACE_HUB_TOKEN':'secret','HF_HUB_OFFLINE':'0'}):
            result=runtime.backend_environment('c'*48)
        self.assertEqual(result['VLLM_API_KEY'],'c'*48)
        for key in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_DATASETS_OFFLINE','VLLM_ENABLE_POW'):self.assertEqual(result[key],'1')
        for key in ('HF_TOKEN','HUGGING_FACE_HUB_TOKEN','VLLM_ADMIN_KEY','API_KEY'):self.assertNotIn(key,result)
    def test_credentials_have_no_default_or_whitespace(self):
        for value in ('','dev-secret','a'*31,'a'*32+'\n','a'*513):
            with patch.dict(os.environ,{'API_KEY':value}),self.assertRaises(ValueError):runtime.required_key('API_KEY')
    def supervisor(self):
        spec=importlib.util.spec_from_file_location('noet_test_supervisor',SUPERVISOR)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    def test_actual_supervisor_passes_only_local_model_and_offline_environment(self):
        module=self.supervisor();supervisor=module.SUP
        with self.readonly(),patch.object(module.subprocess,'Popen') as spawn,patch.object(supervisor,'_wait_backend_ready',return_value=True):
            supervisor.start_backend('NOET/fixture','a'*40)
        args=spawn.call_args.args[0];env=spawn.call_args.kwargs['env']
        self.assertEqual(args[:3],['vllm','serve',str(self.root)])
        self.assertEqual(args[args.index('--tokenizer')+1],str(self.root))
        self.assertEqual(args[args.index('--served-model-name')+1],'NOET/fixture')
        for forbidden in ('--trust-remote-code','--api-key','--revision',self.env['API_KEY']):self.assertNotIn(forbidden,args)
        self.assertEqual(env['VLLM_API_KEY'],self.env['API_KEY']);self.assertNotIn('VLLM_ADMIN_KEY',env)
    def test_actual_supervisor_does_not_spawn_invalid_model_or_stop_for_switch(self):
        module=self.supervisor();supervisor=module.SUP
        with patch.object(module.subprocess,'Popen') as spawn,patch.object(supervisor,'stop_backend') as stop:
            with self.assertRaises(ValueError):supervisor.start_backend('wrong/model','a'*40)
            with self.assertRaises(RuntimeError):supervisor.switch_model('wrong/model','a'*40)
            spawn.assert_not_called();stop.assert_not_called()
    def test_persisted_state_cannot_override_and_admin_key_must_differ(self):
        state=self.folder/'state.json';state.write_text(json.dumps({'model_name':'other/model','model_commit':'c'*40}))
        with patch.dict(os.environ,{'MINER_RUNTIME_MODEL_STATE_PATH':str(state)}):
            supervisor=self.supervisor().SUP
            self.assertEqual(supervisor.model_name,'NOET/fixture');self.assertEqual(supervisor.model_commit,'a'*40)
        with patch.dict(os.environ,{'VLLM_ADMIN_KEY':self.env['API_KEY']}),self.assertRaises(ValueError):self.supervisor()
    def test_control_http_authentication_framing_and_fixed_identity(self):
        import http.client
        import threading
        module=self.supervisor()
        server=module.ThreadingHTTPServer(('127.0.0.1',0),module.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            def request(body=b'{}',authorization=None,length=None,extra=()):
                connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                try:
                    connection.putrequest('POST','/admin/switch-model')
                    if authorization is not None:connection.putheader('Authorization',authorization)
                    connection.putheader('Content-Length',str(len(body) if length is None else length))
                    for key,value in extra:connection.putheader(key,value)
                    connection.endheaders(body)
                    response=connection.getresponse();return response.status,json.loads(response.read())
                finally:connection.close()
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            connection.request('GET','/health')
            health=connection.getresponse();self.assertEqual(health.status,503)
            self.assertFalse(json.loads(health.read())['backend_up']);connection.close()
            auth='Bearer '+self.env['VLLM_ADMIN_KEY']
            with patch.object(module.SUP,'start_backend') as start,patch.object(module.SUP,'stop_backend') as stop:
                for invalid in (None,'Bearer wrong','Bearer é'):
                    self.assertEqual(request(authorization=invalid)[0],401)
                same=json.dumps({'model_name':'NOET/fixture','model_commit':'a'*40}).encode()
                status,payload=request(same,auth);self.assertEqual(status,200);self.assertFalse(payload['switched'])
                self.assertEqual(request(authorization=auth,length=4097)[0],413)
                self.assertEqual(request(authorization=auth,extra=[('Content-Length','2')])[0],400)
                self.assertEqual(request(authorization=auth,extra=[('Transfer-Encoding','chunked')])[0],400)
                self.assertEqual(request(b'not json',auth)[0],400)
                self.assertEqual(request(json.dumps({'model_name':'other/model','model_commit':'a'*40}).encode(),auth)[0],400)
                start.assert_not_called();stop.assert_not_called()
        finally:
            server.shutdown();server.server_close();thread.join(timeout=3)
    def test_dead_backend_ends_startup_wait_without_sleep(self):
        module=self.supervisor();module.SUP.proc=Mock();module.SUP.proc.poll.return_value=1
        with patch.object(module.time,'sleep') as sleep:
            self.assertFalse(module.SUP._wait_backend_ready(120));sleep.assert_not_called()
    def test_stubborn_backend_is_killed_and_reaped(self):
        module=self.supervisor();proc=Mock();module.SUP.proc=proc
        proc.poll.side_effect=[None,-9]
        proc.wait.side_effect=[module.subprocess.TimeoutExpired('fixture',20),-9]
        module.SUP.stop_backend(force=True)
        proc.terminate.assert_called_once();proc.kill.assert_called_once()
        self.assertEqual(proc.wait.call_count,2);self.assertIsNone(module.SUP.proc)
    def test_failed_stop_does_not_lose_running_child(self):
        module=self.supervisor();proc=Mock();module.SUP.proc=proc;proc.poll.return_value=None
        proc.wait.side_effect=module.subprocess.TimeoutExpired('fixture',20)
        with self.assertRaises(module.subprocess.TimeoutExpired):module.SUP.stop_backend(force=True)
        self.assertIs(module.SUP.proc,proc)
