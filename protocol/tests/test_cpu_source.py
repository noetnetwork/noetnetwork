import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from cpu.prepare_source import prepare

ROOT = Path(__file__).resolve().parents[1]


class CpuSourceTests(unittest.TestCase):
    def test_engine_patch_is_in_the_export_and_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = (Path(folder)/name for name in ('one.tar', 'two.tar'))
            self.assertEqual(prepare(first), prepare(second))
            with tarfile.open(first) as archive:
                record = json.load(archive.extractfile('NOET-CPU-SOURCE.json'))
                self.assertFalse(record['productionReady'])
                self.assertEqual(record['engine']['patchSha256'], hashlib.sha256(
                    (ROOT/'patches/noet-engine.patch').read_bytes()).hexdigest())
                # The actual Git-object export must contain every reviewed edit.
                source = ROOT/'.upstream/services/miner-api/llama.cpp'
                for name in ('server-task.h', 'server-task.cpp', 'server-context.cpp'):
                    relative = 'tools/server/'+name
                    self.assertEqual(archive.extractfile('llama/'+relative).read(),
                                     (source/relative).read_bytes())
            with self.assertRaises(FileExistsError):
                prepare(first)


if __name__ == '__main__':
    unittest.main()
