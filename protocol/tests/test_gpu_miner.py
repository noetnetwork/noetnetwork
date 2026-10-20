import json
from pathlib import Path
import tempfile
import unittest
from gpu import miner

REGISTRY = {'model_name': 'Qwen/Qwen3-0.6B', 'model_commit': 'c1899de289a04d12100db370d81485cdf75e47ca',
            'difficulty': 1000000}


class Node:
    def __init__(self, verdict='green', status='accepted'):
        self.calls, self.verdict, self.status, self.tip = [], verdict, status, 'p' * 64

    def __call__(self, method, *params):
        self.calls.append((method, params))
        if method == 'getblockhash':
            return miner.GENESIS
        if method == 'getmodelslist':
            return [REGISTRY]
        if method == 'validateaddress':
            return {'isvalid': params[0].startswith('nwrk1'), 'scriptPubKey': '0014' + '00' * 20}
        if method == 'getbestblockhash':
            return self.tip
        if method == 'create_mining_work_unit':
            return {'req_id': 7, 'height': 5, 'expires_at': 2e9}
        if method == 'submit_mining_response':
            if self.status == 'accepted':
                self.tip = 'b' * 64
            return {'status': self.status, 'block_hash': 'b' * 64}
        if method == 'getrecentvalidations':
            return [{'block_hash': 'b' * 64, 'type': 'full', 'full_status': self.verdict}]
        if method == 'getblockcount':
            return 5
        raise AssertionError(method)


class Producer:
    """Stands in for the engine process; optionally writes one proof."""
    def __init__(self, write=True):
        self.write = write

    def __call__(self, command, **kwargs):
        out = Path(command[command.index('--out') + 1])
        if self.write:
            (out / 'proofs').mkdir(parents=True)
            (out / 'proofs/7_1_abc.bin').write_bytes(b'proof-bytes')

        class Process:
            pid = 999999
            returncode = 0
            def wait(self, timeout=None):
                return 0
        return Process()


class MinerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.out = Path(folder.name)

    def test_registry_and_reward_address_are_pinned(self):
        self.assertEqual(miner.checked_registry(Node())['difficulty'], 1000000)
        class WrongModel(Node):
            def __call__(self, method, *params):
                if method == 'getmodelslist':
                    return [dict(REGISTRY, model_commit='other')]
                return Node.__call__(self, method, *params)
        with self.assertRaises(RuntimeError):
            miner.checked_registry(WrongModel())
        self.assertTrue(miner.reward_script(Node(), 'nwrk1q' + 'q' * 38).startswith('0014'))
        for bad in ('nlab1q' + 'q' * 38, 'bc1qxyz'):
            with self.assertRaises(ValueError):
                miner.reward_script(Node(), bad)

    def test_accepted_proof_waits_for_green_and_canonical_tip(self):
        node = Node()
        result = miner.attempt(node, REGISTRY, 'script', self.out, 0, runner=Producer())
        self.assertEqual(result['outcome'], 'green')
        self.assertEqual(result['acceptedHeight'], 5)
        recorded = json.loads((self.out / 'attempt-000000/result.json').read_text())
        self.assertEqual(recorded['submissions'][0]['proofSha256'], '0005c2611f054c05b5dea8487ea5da314102f43296837073e3ac8b99aa1cfe8b')
        self.assertEqual(sum(1 for c in node.calls if c[0] == 'submit_mining_response'), 1)

    def test_red_verdict_is_recorded_and_never_resubmitted(self):
        node = Node(verdict='red')
        result = miner.attempt(node, REGISTRY, 'script', self.out, 1, runner=Producer())
        self.assertEqual(result['outcome'], 'red')
        self.assertNotIn('acceptedHeight', result)
        self.assertEqual(sum(1 for c in node.calls if c[0] == 'submit_mining_response'), 1)

    def test_no_eligible_proof_and_rejected_submission(self):
        result = miner.attempt(Node(), REGISTRY, 'script', self.out, 2, runner=Producer(write=False))
        self.assertEqual(result['outcome'], 'no-eligible-proof')
        rejected = miner.attempt(Node(status='rejected'), REGISTRY, 'script', self.out, 3, runner=Producer())
        self.assertEqual(rejected['outcome'], 'no-eligible-proof')
        self.assertEqual(rejected['submissions'][0]['response']['status'], 'rejected')

    def test_settle_times_out_without_verdict(self):
        class Silent(Node):
            def __call__(self, method, *params):
                return [] if method == 'getrecentvalidations' else Node.__call__(self, method, *params)
        clock = iter(range(0, 10000, 500))
        self.assertIsNone(miner.settle(Silent(), 'b' * 64, 900, sleep=lambda s: None, clock=lambda: next(clock)))


if __name__ == '__main__':
    unittest.main()
