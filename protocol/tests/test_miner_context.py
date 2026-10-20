"""Concurrency regressions against the actual pinned mining context."""
import importlib.util
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / '.upstream/services/miner-api/src/components/context.py'


@unittest.skipUnless(SOURCE.exists(), 'Run protocol/bootstrap.py first')
class MinerContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('noet_test_miner_context', SOURCE)
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        spec.loader.exec_module(cls.module)

    def setUp(self):
        self.context = self.module.LockFreeContext('a' * 64, '1d00ffff')
        self.context.update_mining('a' * 64, '00' * 76, '1d00ffff', 1)

    def overlap(self, second):
        entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
        failures = []
        real_snapshot = self.module.MiningSnapshot

        def snapshot(**fields):
            if threading.current_thread().name == 'first-proof':
                entered.set()
                if not release.wait(5):
                    raise AssertionError('Timed out releasing the first writer')
            return real_snapshot(**fields)

        def guarded(fn):
            try:
                fn()
            except BaseException as error:
                failures.append(error)

        def second_writer():
            second_started.set()
            second()

        with patch.object(self.module, 'MiningSnapshot', side_effect=snapshot):
            first = threading.Thread(name='first-proof', target=guarded, args=(lambda: self.context.update_vdf('aa', 10, block_hash='a' * 64),))
            other = threading.Thread(target=guarded, args=(second_writer,))
            first.start()
            try:
                self.assertTrue(entered.wait(5))
                other.start()
                self.assertTrue(second_started.wait(5))
                # Without writer serialization, the second update completes and
                # is then overwritten by the paused first writer.
                other.join(.1)
            finally:
                release.set()
                first.join(5)
                if other.ident is not None:
                    other.join(5)
            self.assertFalse(first.is_alive())
            self.assertFalse(other.is_alive())
        self.assertEqual(failures, [])

    def test_inflight_proof_cannot_restore_old_block(self):
        self.overlap(lambda: self.context.update_mining('b' * 64, '11' * 76, '1d00ffff', 2, stepbind=True, v4=True, height=5))
        current = self.context.read()
        self.assertEqual(current.block_hash, 'b' * 64)
        self.assertEqual(current.request_id, 2)
        self.assertIsNone(current.vdf_proof)
        self.assertEqual(current.vdf_tick, 0)
        self.assertTrue(current.v4)
        self.assertEqual(current.height, 5)
        self.assertFalse(self.context.vdf_initialised)
        self.assertFalse(self.context.update_vdf('bb', 100, block_hash='a' * 64))

    def test_competing_proofs_cannot_lower_tick(self):
        self.overlap(lambda: self.context.update_vdf('bb', 100, block_hash='a' * 64))
        self.assertEqual(self.context.read().vdf_tick, 100)
        self.assertEqual(self.context.read().vdf_proof, 'bb')

    def test_job_modes_reset_and_snapshots_remain_immutable(self):
        self.context.update_mining('a' * 64, '11' * 76, '1d00ffff', 2, base_share_target='ff', stepbind=True, v4=True, height=5)
        self.context.update_vdf('aa', 10, block_hash='a' * 64)
        old = self.context.read()
        self.context.update_mining('a' * 64, '22' * 76, '1d00ffff', 3)
        current = self.context.read()
        self.assertEqual(current.vdf_tick, 10)
        self.assertFalse(current.stepbind)
        self.assertFalse(current.v4)
        self.assertIsNone(current.height)
        self.assertIsNone(current.base_share_target)
        self.assertTrue(old.v4)
        self.assertEqual(old.request_id, 2)
        with self.assertRaises(ValueError):
            self.context.update_mining('b' * 64, '22' * 76, '1d00ffff', 4, v4=True)
        self.assertIs(self.context.read(), current)
