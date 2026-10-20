import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import prepare_verifier as prepare
from noet import verifier_entry as entry


class VerifierPackageTests(unittest.TestCase):
    def test_parent_requires_digest_before_source_access(self):
        for image in ('image:latest', 'image@sha256:'+'a'*63, 'image@sha256:'+'a'*64+'\nRUN bad'):
            with self.assertRaises(ValueError), patch.object(prepare.subprocess, 'check_output', side_effect=AssertionError('source access')):
                prepare.prepare(image, 'unused.tar')

    def test_deterministic_guarded_context(self):
        with tempfile.TemporaryDirectory() as folder:
            a = Path(folder)/'a.tar'; b = Path(folder)/'b.tar'
            base = 'example.invalid/reviewed-verifier@sha256:'+'a'*64
            self.assertEqual(prepare.prepare(base,a), prepare.prepare(base,b))
            with tarfile.open(a) as archive:
                docker = archive.extractfile('Dockerfile').read().decode()
                self.assertIn('FROM '+base, docker)
                self.assertIn('USER 1000:1000', docker)
                self.assertIn('ENTRYPOINT ["python3", "-m", "noet.verifier_entry"]', docker)
                self.assertIn('CMD []', docker)
                proof = archive.extractfile('src/proof_verifier.py').read().decode()
                self.assertIn('approved_pretrained(AutoModelForCausalLM', proof)
                self.assertNotIn('.from_pretrained(', proof)
                self.assertIn('IPFS model fallback is disabled', proof)
                helper = archive.extractfile('src/utils/shared_utils.py').read().decode()
                self.assertNotIn('hf_hub_download', helper)
                self.assertNotIn('list_repo_files', helper)
                self.assertFalse(json.load(archive.extractfile('NOET-BUILD-INPUTS.json'))['productionReady'])
                self.assertNotIn('.git', archive.getnames())
                self.assertIn('noet/vdf.py', archive.getnames())
                self.assertIn('noet/verifier_transport.py', archive.getnames())
                service = archive.extractfile('src/main.py').read().decode()
                self.assertIn('REMOTE_VERIFY_ENABLED = False', service)
                self.assertIn('self.admission.admit(message)', service)
                self.assertIn('noet/admission.py', archive.getnames())
                pinned_broker = prepare.subprocess.check_output(['git', '-C', str(ROOT/'.upstream'), 'show', json.loads((ROOT/'upstream.lock.json').read_text())['commit'] + ':services/verification-api/src/zmq_send_broker.py'])
                self.assertEqual(archive.extractfile('src/zmq_send_broker.py').read(), pinned_broker)
                self.assertIn('verify_bounded_vdf', helper)
                self.assertLess(sum(item.size for item in archive.getmembers()), 1000000)
            with self.assertRaises(FileExistsError): prepare.prepare(base,a)

    def test_entry_fails_before_application_import(self):
        with patch.dict(os.environ, {'POW_PROMPT_GIBBERISH_CALIBRATED':'NOET/model@'+'a'*40+':bf16','MODEL_NAME':'NOET/model','MODEL_COMMIT':'a'*40,'TEST_MODE':'false'}, clear=True), patch.object(entry, 'checked_model_directory', side_effect=ValueError('invalid snapshot')), patch.object(entry.runpy,'run_path') as run:
            with self.assertRaisesRegex(ValueError,'invalid snapshot'):entry.main()
            run.assert_not_called()

    def test_entry_rejects_mock_verification(self):
        for mode in ('true','1','yes','unexpected'):
            with patch.dict(os.environ,{'TEST_MODE':mode},clear=True), patch.object(entry.runpy,'run_path') as run:
                with self.assertRaisesRegex(ValueError,'Synthetic'):entry.main()
                run.assert_not_called()

    def test_entry_sets_offline_flags_before_application_import(self):
        def application(path, run_name):
            self.assertEqual(path,'/app/src/main.py');self.assertEqual(run_name,'__main__')
            for name in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_DATASETS_OFFLINE'):
                self.assertEqual(os.environ[name],'1')
            self.assertEqual(os.environ['TEST_MODE'],'false')
            self.assertNotIn('HF_TOKEN',os.environ)
            self.assertEqual(sys.path[0],'/app/src')
        original = sys.path[:]
        try:
            with patch.dict(os.environ, {'POW_PROMPT_GIBBERISH_CALIBRATED':'NOET/model@'+'a'*40+':bf16','MODEL_NAME':'NOET/model','MODEL_COMMIT':'a'*40,'HF_TOKEN':'secret','VALIDATOR_SHARED_SECRET':'ab'*32}, clear=True), patch.object(entry,'checked_model_directory', return_value='/approved') as gate, patch.object(entry.runpy,'run_path',side_effect=application) as run:
                entry.main();gate.assert_called_once_with('NOET/model','a'*40);run.assert_called_once()
        finally: sys.path[:] = original
