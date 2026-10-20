import importlib.util
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
SPEC=importlib.util.spec_from_file_location('prepare_miner',ROOT/'prepare_miner.py')
prepare=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(prepare)

class GuardedMinerPackageTests(unittest.TestCase):
    def test_mutable_or_injectable_parent_rejected_before_access(self):
        for name in ('image:latest','image','image@sha256:'+'a'*63,'image@sha256:'+'a'*64+'\nRUN bad','--flag@sha256:'+'a'*64):
            with self.assertRaises(ValueError),patch.object(prepare.subprocess,'check_output',side_effect=AssertionError('Unexpected source access')):
                prepare.prepare(name,'unused.tar')
    def test_reviewed_launcher_packaged_without_weights_or_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            a=Path(folder)/'a.tar';b=Path(folder)/'b.tar';base='example.invalid/reviewed-engine@sha256:'+'a'*64
            self.assertEqual(prepare.prepare(base,a),prepare.prepare(base,b))
            with tarfile.open(a) as archive:
                names=archive.getnames()
                self.assertNotIn('.git',names);self.assertNotIn('model.safetensors',names)
                docker=archive.extractfile('Dockerfile').read().decode()
                self.assertIn('FROM '+base,docker);self.assertIn('USER 1000:1000',docker)
                self.assertIn('ENTRYPOINT ["python3", "/opt/noet/vllm_supervisor.py"]',docker)
                source=archive.extractfile('vllm_supervisor.py').read().decode()
                self.assertIn('checked_model_directory(model_name, model_commit)',source)
                self.assertNotIn('--trust-remote-code',source)
                self.assertNotIn('dev-secret',source)
                record=json.load(archive.extractfile('NOET-BUILD-INPUTS.json'))
                self.assertFalse(record['productionReady']);self.assertEqual(record['parentImage'],base)
                self.assertLess(sum(member.size for member in archive.getmembers()),100000)
            with self.assertRaises(FileExistsError):prepare.prepare(base,a)
