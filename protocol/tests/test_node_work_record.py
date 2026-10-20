import copy
import json
from pathlib import Path
import tempfile
import unittest
from cpu.node_work import read_job

RECORD = Path(__file__).resolve().parents[1]/'validation/native-work.json'


class NodeWorkRecordTests(unittest.TestCase):
    def test_record_preserves_native_wire_endianness_and_target(self):
        job, digest = read_job(RECORD)
        self.assertEqual(bytes.fromhex(job['header_prefix'])[4:36][::-1].hex(), job['tip_hash'])
        self.assertEqual(job['height'], 1)
        self.assertEqual(len(digest), 64)

    def test_altered_context_is_refused_before_inference(self):
        original = json.loads(RECORD.read_text())
        mutations = [('height', True), ('height', 2), ('req_id', -1), ('req_id', '1'),
                     ('header_prefix', '00'*76), ('header_prefix', 'zz'*76),
                     ('tip_hash', 'ab'*32), ('target', 'ff'*32), ('v4', True),
                     ('stepbind', 0), ('network', 'main')]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'record.json'
            for key, value in mutations:
                data = copy.deepcopy(original)
                next(c for c in data['cases'] if c['activeV4'])['workUnits'][0][key] = value
                path.write_text(json.dumps(data))
                with self.subTest(key=key, value=value), self.assertRaises(ValueError): read_job(path)
            for key, value in [('validationMode','mock'), ('externalValidationEnabled',False), ('blocksAccepted',1)]:
                data = original | {key:value}
                path.write_text(json.dumps(data))
                with self.subTest(key=key), self.assertRaises(ValueError): read_job(path)

    def test_oversized_record_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'large.json'; path.write_bytes(b' '*65537)
            with self.assertRaisesRegex(ValueError, 'Oversized'): read_job(path)
