"""The verifier's request order (realwork/verify.py Pending) and its receive loop.

The loop test runs verify.run with stand-ins for ZMQ, the model and the
detector, so it checks the reader thread, de-duplication, the order replays run
in and the verdict cache without a GPU.
"""
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

from realwork.verify import Pending, request_fields


def entry_order(pending):
    out = []
    while (entry := pending.pop()) is not None:
        out.append((entry['height'], entry['kind']))
    return out


class PendingTests(unittest.TestCase):
    def test_highest_height_first_quick_before_full_then_arrival(self):
        p = Pending()
        for n, (height, kind) in enumerate([(3662, 2), (3670, 2), (3666, 2), (3670, 1), (3662, 2), (3671, 2), (3671, 1)]):
            self.assertEqual(p.add(f'sha{n}', b'', height, kind, 0.0), [])
        self.assertEqual(entry_order(p), [(3671, 1), (3671, 2), (3670, 1), (3670, 2), (3666, 2), (3662, 2), (3662, 2)])

    def test_heights_at_or_below_a_green_full_wait_behind_higher_ones(self):
        p = Pending()
        p.answered(3671, 2, 'GREEN')
        p.add('a', b'', 3671, 2, 0.0)    # a competing block at the answered height
        p.add('b', b'', 3670, 1, 0.0)
        p.add('c', b'', 3672, 2, 0.0)    # the candidate above the tip
        p.add('d', b'', 3673, 2, 0.0)
        self.assertEqual(entry_order(p), [(3673, 2), (3672, 2), (3671, 2), (3670, 1)])

    def test_only_a_green_full_moves_the_mark(self):
        p = Pending()
        p.answered(3700, 2, 'RED')
        p.answered(3700, 2, 'AMBER')
        p.answered(3700, 1, None)
        p.answered(None, 2, 'GREEN')
        self.assertEqual(p.green, 0)
        p.answered(3671, 2, 'GREEN')
        p.answered(3650, 2, 'GREEN')
        self.assertEqual(p.green, 3671)

    def test_a_waiting_request_is_queued_once(self):
        p = Pending()
        self.assertEqual(p.add('x', b'1', 5, 1, 0.0), [])
        self.assertEqual(p.add('x', b'1', 5, 1, 1.0), 'duplicate')
        self.assertEqual(len(p), 1)
        self.assertEqual(p.pop()['arrived'], 0.0)
        self.assertEqual(p.add('x', b'1', 5, 1, 2.0), [])  # after it ran, a re-send queues again

    def test_over_the_limit_the_worst_request_goes(self):
        p = Pending(limit=3)
        p.answered(3670, 2, 'GREEN')
        p.add('old', b'', 3662, 2, 0.0)
        p.add('tip', b'', 3671, 2, 0.0)
        p.add('mid', b'', 3666, 2, 0.0)
        dropped = p.add('new', b'', 3671, 1, 0.0)
        self.assertEqual([e['sha'] for e in dropped], ['old'])
        dropped = p.add('older', b'', 3600, 2, 0.0)
        self.assertEqual([e['sha'] for e in dropped], ['older'])  # ranks worst itself
        self.assertEqual(entry_order(p), [(3671, 1), (3671, 2), (3666, 2)])

    def test_unparsed_requests_still_run_last(self):
        p = Pending()
        p.add('bad', b'', None, None, 0.0)
        p.add('ok', b'', 2, 2, 0.0)
        self.assertEqual([p.pop()['sha'], p.pop()['sha']], ['ok', 'bad'])

    def test_request_fields_tolerates_parse_failures(self):
        def boom(raw):
            raise ValueError('bad')
        self.assertEqual(request_fields(b'r', lambda raw: {'validation_type': 2}, lambda raw: (9, 2)), (9, 2))
        self.assertEqual(request_fields(b'r', boom, boom), (None, None))


# ---- run() with stand-ins -------------------------------------------------

class FakePull:
    def __init__(self):
        self.q, self.held = queue.Queue(), None

    def poll(self, ms):
        try:
            self.held = self.q.get(timeout=ms / 1000)
            return True
        except queue.Empty:
            return False

    def recv_multipart(self):
        frames, self.held = self.held, None
        return frames

    setsockopt = bind = connect = close = lambda self, *a: None


class FakePush:
    def __init__(self):
        self.sent = []

    def send_multipart(self, frames):
        self.sent.append(frames)

    setsockopt = bind = connect = close = lambda self, *a: None


def request(height, kind, tag):
    return json.dumps({'height': height, 'kind': kind, 'tag': tag}).encode().ljust(64)


def fakes(pull, push, calls, release):
    zmq = ModuleType('zmq')
    zmq.PULL, zmq.PUSH, zmq.LINGER, zmq.MAXMSGSIZE, zmq.RCVHWM, zmq.SNDHWM = 1, 2, 3, 4, 5, 6
    zmq.Context = lambda: SimpleNamespace(socket=lambda kind: pull if kind == 1 else push, term=lambda: None)

    def unpack(raw):
        r = json.loads(raw)
        return {'request_type': 1, 'validation_type': r['kind'], 'hash_id': r['tag'].encode(),
                'request': {'difficulty': 0, 'pow_blob': {'model_identifier': 'm'}}}

    class Verifier:
        model = None

        def __init__(self, logger):
            pass

        def full_verify(self, raw):
            r = json.loads(raw)
            if r['tag'] == 'blocker':
                release.wait(10)
            calls.append((r['height'], 2, r['tag']))
            return 'GREEN'

        def quick_verify_smell_test(self, raw):
            r = json.loads(raw)
            calls.append((r['height'], 1, r['tag']))
            return 2

    class Builder:
        def __init__(self, size):
            pass
        CreateByteVector = lambda self, b: b
        Finish = lambda self, x: None
        Output = lambda self: b'response'

    noop = lambda *a, **k: None
    utils, proof, context = ModuleType('utils'), ModuleType('utils.proof'), ModuleType('utils.request_context')
    proof.ValidationResponse = SimpleNamespace(Start=noop, AddHashIdentifier=noop, AddEnumResponse=noop, End=noop)
    context.read_block_validation_fields = lambda raw: (json.loads(raw)['height'], 2)
    runtime = SimpleNamespace(score=None, close=noop)
    modules = {
        'zmq': zmq,
        'flatbuffers': SimpleNamespace(Builder=Builder),
        'numpy': SimpleNamespace(random=SimpleNamespace(seed=noop)),
        'pfunpack': SimpleNamespace(unpack_validation_request=unpack),
        'torch': SimpleNamespace(set_num_threads=noop, set_num_interop_threads=noop, manual_seed=noop,
                                 cuda=SimpleNamespace(is_available=lambda: False)),
        'detector_gate': SimpleNamespace(warm_on_start=noop, detector_ready=lambda: True, runtime=lambda: runtime),
        'enhanced_logger': SimpleNamespace(create_logger=noop),
        'proof_verifier': SimpleNamespace(ProofVerifier=Verifier),
        'utils': utils, 'utils.proof': proof, 'utils.request_context': context,
        'zmq_send_broker': SimpleNamespace(build_envelope=lambda *a: b'envelope'),
    }
    return modules


class RunLoopTests(unittest.TestCase):
    def test_reader_drains_while_a_replay_runs_and_the_tip_goes_first(self):
        from realwork import verify
        pull, push, calls, release = FakePull(), FakePush(), [], threading.Event()
        env = {'VALIDATOR_SHARED_SECRET': '11' * 32, 'VALIDATOR_PUSH_PORT': '18555', 'VALIDATOR_PULL_PORT': '18556'}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(sys.modules, fakes(pull, push, calls, release)), \
                mock.patch.dict(os.environ, env):
            out = Path(tmp) / 'session'
            worker = threading.Thread(target=verify.run, args=(out, 4))
            worker.start()
            pull.q.put([request(3600, 2, 'blocker')])
            for _ in range(100):
                if (out / '0').exists():
                    break
                time.sleep(0.02)
            # While the blocker replays, the backlog arrives in node order:
            # stale competitors first, the candidate above the tip last.
            backlog = [(3662, 2, 'a'), (3662, 2, 'b'), (3670, 1, 'q'), (3670, 1, 'q'), (3670, 2, 'c'),
                       (3666, 2, 'd'), (3670, 1, 'q'), (3671, 1, 'tq'), (3671, 2, 'tf'), (3671, 2, 'tf')]
            for height, kind, tag in backlog:
                pull.q.put([request(height, kind, tag)])
            while not pull.q.empty():
                time.sleep(0.02)
            time.sleep(0.3)
            release.set()
            for _ in range(150):
                if len(calls) >= 8:
                    break
                time.sleep(0.02)
            # A re-send of an answered request is answered from the cache.
            pull.q.put([request(3671, 2, 'tf')])
            pull.q.put([request(3670, 1, 'q')])
            worker.join(10)
            self.assertFalse(worker.is_alive())
            self.assertEqual([c[2] for c in calls], ['blocker', 'tq', 'tf', 'q', 'c', 'd', 'a', 'b'])
            results = [json.loads((d / 'result.json').read_text())
                       for d in sorted(out.iterdir(), key=lambda d: int(d.name) if d.name.isdigit() else -1)
                       if d.name.isdigit()]
            self.assertEqual(len(results), 10)
            self.assertEqual(sum(1 for r in results if r.get('reusedExactEnvelope')), 2)
            self.assertTrue(all(r['responseSent'] for r in results))
            self.assertEqual(len(push.sent), 10)
            self.assertEqual(results[2]['queueGreenHeight'], 3600)
            self.assertEqual(results[3]['queueGreenHeight'], 3671)
            self.assertFalse((out / 'queue.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
