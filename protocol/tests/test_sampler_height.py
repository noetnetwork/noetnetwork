"""Run the actual pure snapshot function without importing GPU libraries.

This verifies shared source behavior, not execution of an installed GPU miner.
"""
import ast
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1]/'.upstream/shared-utils/pow-utils/common_sampler_helper.py'


class SamplerHeightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SOURCE.read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == 'build_pow_snapshot']
        if len(functions) != 1:
            raise AssertionError('Expected exactly one actual snapshot builder')
        namespace = {}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), 'exec'), namespace)
        cls.snapshot = staticmethod(namespace['build_pow_snapshot'])

    def job(self):
        return {'tick':10000, 'request_id':1, 'difficulty':60000000,
                'block_hash':'ab'*32, 'vdf':'00'*200, 'target':'7fffff'+'00'*29,
                'header_prefix':'00'*76, 'stepbind':1, 'v4':1}

    def test_height_presence_and_uint32_boundaries(self):
        for height in (0, 1, 24150, 2**32-1):
            with self.subTest(height=height):
                data = self.job(); data['height'] = height
                snapshot = self.snapshot(data)
                self.assertEqual(snapshot['height'], height)
                self.assertIs(type(snapshot['height']), int)
                self.assertTrue(snapshot['v4'])
                self.assertTrue(snapshot['stepbind'])
                self.assertNotIn('height', self.snapshot(self.job()))
                self.assertEqual(snapshot['height'], height)

    def test_malformed_height_does_not_silently_disable_checks(self):
        for height in (None, True, False, -1, 1.0, 1.5, '1', {}, [], 2**32, 2**63, 2**64-1):
            with self.subTest(height=height):
                data = self.job(); data['height'] = height
                with self.assertRaisesRegex(ValueError, 'unsigned 32-bit integer'):
                    self.snapshot(data)

    def test_marker_validation_and_non_mining_requests_are_preserved(self):
        self.assertIsNone(self.snapshot(None))
        self.assertIsNone(self.snapshot({}))
        for marker in ('stepbind', 'v4'):
            for value in (True, '1', 1.0, 0, 2):
                with self.subTest(marker=marker, value=value):
                    data = self.job(); data[marker] = value
                    with self.assertRaises(ValueError): self.snapshot(data)
        data = self.job(); del data['stepbind']
        with self.assertRaisesRegex(ValueError, 'requires pow.stepbind'):
            self.snapshot(data)


if __name__ == '__main__':
    unittest.main()
