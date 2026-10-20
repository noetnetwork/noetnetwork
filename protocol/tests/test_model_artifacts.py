import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from noet.model_artifacts import create, verify, validate


class ModelArtifactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'weights').mkdir()
        (self.root / 'weights/model.safetensors').write_bytes(b'fixture, not model weights')
        (self.root / 'config.json').write_text('{}')
        self.manifest = create(self.root, 'NOET/fixture', 'a' * 40, 'test fixture only')
        self.raw = json.dumps(self.manifest).encode()
        self.pin = hashlib.sha256(self.raw).hexdigest()

    def test_exact_inventory_and_pin(self):
        report = verify(self.root, self.raw, self.pin)
        self.assertTrue(report['filesVerified'])
        self.assertEqual(report['files'], 2)
        self.assertEqual(report['proofValidation'], 'not performed')
        with self.assertRaises(ValueError):verify(self.root, self.raw, '0' * 64)
        with self.assertRaises(ValueError):verify(self.root, self.raw + b' ', self.pin)

    def test_modified_missing_and_extra_files_rejected(self):
        file = self.root / 'config.json'
        for content in ('{"changed":true}', None):
            if content is None:file.unlink()
            else:file.write_text(content)
            with self.assertRaises(ValueError):verify(self.root, self.raw, self.pin)
        file.write_text('{}')
        (self.root / 'unapproved.py').write_text('raise RuntimeError()')
        with self.assertRaises(ValueError):verify(self.root, self.raw, self.pin)

    def test_symlinks_and_special_files_rejected(self):
        link = self.root / 'link'
        for target in ('config.json', 'weights'):
            link.symlink_to(target)
            with self.assertRaises((OSError, ValueError)):create(self.root, 'NOET/fixture', 'a'*40, 'test')
            link.unlink()
        os.mkfifo(link)
        with self.assertRaises(ValueError):create(self.root, 'NOET/fixture', 'a'*40, 'test')

    def test_paths_duplicates_and_schema_rejected(self):
        for path in ('../escape', '/absolute', 'weights/../file', 'weights//file', 'weights\\file', './file'):
            data = copy.deepcopy(self.manifest)
            data['files'][0]['path'] = path
            with self.subTest(path=path), self.assertRaises(ValueError):validate(data)
        data = copy.deepcopy(self.manifest)
        data['files'].append(data['files'][0])
        with self.assertRaises(ValueError):validate(data)
        for key, value in (('revision', 'main'), ('model', 'automatic'), ('license',''), ('version',2)):
            with self.subTest(key=key), self.assertRaises(ValueError):validate(dict(self.manifest, **{key:value}))
        raw = b'{"version":1,"version":1}'
        with self.assertRaises(ValueError):verify(self.root, raw, hashlib.sha256(raw).hexdigest())

    def test_creation_is_deterministic(self):
        self.assertEqual(self.manifest, create(self.root, 'NOET/fixture', 'a'*40, 'test fixture only'))

    def test_cli_create_verify_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / 'model.json'
            command = [sys.executable, '-m', 'noet.model_artifacts']
            create_args = ['create', str(self.root), '--model', 'NOET/fixture', '--revision', 'a'*40, '--license', 'test', '--out', str(manifest)]
            created = subprocess.run(command + create_args, capture_output=True, text=True, timeout=10)
            self.assertEqual(created.returncode, 0, created.stderr)
            pin = json.loads(created.stdout)['manifestSha256']
            checked = subprocess.run(command + ['verify', str(self.root), str(manifest), '--sha256', pin], capture_output=True, text=True, timeout=10)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertTrue(json.loads(checked.stdout)['filesVerified'])
            again = subprocess.run(command + create_args, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(again.returncode, 0)

    def test_cli_rejects_special_manifest_without_blocking(self):
        fifo = self.root / 'manifest-pipe'
        os.mkfifo(fifo)
        result = subprocess.run([sys.executable, '-m', 'noet.model_artifacts', 'verify', str(self.root), str(fifo), '--sha256', self.pin], capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('regular file', result.stderr)
