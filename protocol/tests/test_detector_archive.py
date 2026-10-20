import hashlib
import io
from pathlib import Path
import tempfile
import unittest
import warnings
import zipfile

from cpu.detector_diagnostics import checked_archive, LIMIT


class DetectorArchiveTests(unittest.TestCase):
    def archive(self, entries):
        raw = io.BytesIO()
        with warnings.catch_warnings(), zipfile.ZipFile(raw, 'w', zipfile.ZIP_DEFLATED) as z:
            warnings.simplefilter('ignore', UserWarning)
            for name, value in entries: z.writestr(name, value)
        return raw.getvalue()

    def check(self, raw, digest=None):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'capture.npz';path.write_bytes(raw)
            return checked_archive(path,digest or hashlib.sha256(raw).hexdigest()).read()

    def test_verified_numeric_archive_is_returned_without_execution(self):
        raw=self.archive([('x.npy',b'numeric payload')])
        self.assertEqual(self.check(raw),raw)

    def test_changed_capture_is_refused(self):
        with self.assertRaisesRegex(ValueError,'SHA-256'):self.check(self.archive([('x.npy',b'a')]),'a'*64)

    def test_archive_expansion_is_bounded(self):
        raw=self.archive([('x.npy',b'0'*(LIMIT+1))])
        self.assertLess(len(raw),100000)
        with self.assertRaisesRegex(ValueError,'Unsupported'):self.check(raw)
        with self.assertRaisesRegex(ValueError,'size'):self.check(b'0'*(LIMIT+1))

    def test_duplicate_escaped_and_excessive_members_are_refused(self):
        for entries in ([('x.npy',b'a'),('x.npy',b'b')], [('../x.npy',b'a')],
                        [('sub\\x.npy',b'a')], [('code.py',b'a')], [],
                        [(str(i)+'.npy',b'a') for i in range(101)]):
            with self.subTest(entries=len(entries)),self.assertRaisesRegex(ValueError,'Unsupported'):
                self.check(self.archive(entries))
