import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import urllib.request

SPEC = importlib.util.spec_from_file_location('cpu_fetch', Path(__file__).resolve().parents[1] / 'cpu/fetch_model.py')
fetch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch)


class CandidateDownloadTests(unittest.TestCase):
    def test_verified_bytes_only_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            row = {'path':'config.json', 'bytes':3, 'sha256':hashlib.sha256(b'abc').hexdigest()}
            opener = Mock(); opener.open.return_value = io.BytesIO(b'abc')
            fetch.download_record(opener, root, row)
            self.assertEqual((root/'config.json').read_bytes(), b'abc')
            self.assertFalse((root/'config.json.partial').exists())
            with self.assertRaises(FileExistsError): fetch.download_record(opener, root, row)
            self.assertEqual(opener.open.call_count, 1)

    def test_wrong_hash_truncation_and_oversize_never_publish(self):
        for data in (b'abd', b'ab', b'abcd'):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                opener = Mock(); opener.open.return_value = io.BytesIO(data)
                with self.assertRaises(ValueError):
                    fetch.download_record(opener, root, {'path':'model.safetensors', 'bytes':3, 'sha256':hashlib.sha256(b'abc').hexdigest()})
                self.assertFalse((root/'model.safetensors').exists())

    def test_unsafe_paths_and_existing_links_fail_before_network(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('../escape', '/absolute', 'nested/file', 'a\\b'):
                opener = Mock()
                with self.assertRaises(ValueError):fetch.download_record(opener, root, {'path':name})
                opener.open.assert_not_called()
            (root/'link').symlink_to(root/'missing')
            opener = Mock()
            with self.assertRaises(FileExistsError): fetch.download_record(opener, root, {'path':'link'})
            opener.open.assert_not_called()

    def test_redirects_cannot_downgrade_or_leave_model_hosts(self):
        handler = fetch.PublicModelRedirect()
        request = urllib.request.Request(fetch.ORIGIN+'config.json')
        for url in ('http://huggingface.co/x', 'https://huggingface.co.attacker.invalid/x',
                    'https://127.0.0.1/x', 'https://user:password@huggingface.co/x',
                    'https://huggingface.co:8080/x'):
            with self.assertRaises(ValueError):handler.redirect_request(request,None,302,'redirect',{},url)
        result = handler.redirect_request(request,None,302,'redirect',{},'https://cas-bridge.xethub.hf.co/object')
        self.assertEqual(result.full_url, 'https://cas-bridge.xethub.hf.co/object')
