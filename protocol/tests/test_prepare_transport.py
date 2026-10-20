import hashlib
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import prepare_transport


class TransportPackageTests(unittest.TestCase):
    def test_deterministic_allowlist_excludes_credentials_and_runtime_config(self):
        with tempfile.TemporaryDirectory() as folder:
            a = Path(folder)/'a.tar'; b = Path(folder)/'b.tar'
            parent = 'example.invalid/python@sha256:'+'a'*64
            self.assertEqual(prepare_transport.prepare(parent, a), prepare_transport.prepare(parent, b))
            with tarfile.open(a) as archive:
                self.assertEqual(set(archive.getnames()), {'Dockerfile','NOET-BUILD-INPUTS.json','noet',
                    'noet/__init__.py','noet/secure_transport.py','noet/verifier_transport.py'})
                metadata = json.load(archive.extractfile('NOET-BUILD-INPUTS.json'))
                for name, digest in metadata['files'].items():
                    self.assertEqual(hashlib.sha256(archive.extractfile(name).read()).hexdigest(), digest)
                self.assertFalse(metadata['productionReady'])
                self.assertFalse(metadata['containsCredentials'])
                docker = archive.extractfile('Dockerfile').read().decode()
                self.assertIn('USER 1000:1000', docker)
                self.assertIn('FROM '+parent, docker)
                self.assertIn('["python3", "-m", "noet.secure_transport"]', docker)
            with self.assertRaises(FileExistsError): prepare_transport.prepare(parent, a)

    def test_mutable_image_or_symlink_input_is_refused(self):
        with self.assertRaises(ValueError): prepare_transport.prepare('python:latest', 'unused')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'noet').mkdir()
            (root/'noet/__init__.py').symlink_to(ROOT/'noet/__init__.py')
            output = root/'out.tar'
            with patch.object(prepare_transport, 'ROOT', root), self.assertRaises(OSError):
                prepare_transport.prepare('example.invalid/python@sha256:'+'a'*64, output)
            self.assertFalse(output.exists())
