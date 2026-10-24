from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from noet.gateway import Gateway as Service, handler_for

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'miner'))
import noet_miner  # noqa: E402

GENESIS = 'ab' * 32
ADDRESS = 'tnoet1q' + 'q' * 38
BLOCK = 'cd' * 32
NETWORK = {'gateway': '', 'hrp': 'tnoet', 'profile': 'noet-testnet-1', 'genesis': GENESIS}


class Node:
    def __init__(self, status='accepted_pending_connect', models=None):
        self.status, self.submitted = status, []
        self.models = models or [noet_miner.MODEL]

    def __call__(self, method, *params):
        if method == 'validateaddress':
            return {'isvalid': params[0].startswith('tnoet1'), 'scriptPubKey': '0014' + '22' * 20}
        if method == 'create_mining_work_unit':
            return {'req_id': 9, 'height': 12, 'header_prefix': '01000000' + 'ee' * 32 + '00' * 40,
                    'target': 'ff' * 32, 'expires_at': 4e9}
        if method == 'submit_mining_response':
            self.submitted.append(params)
            return {'status': self.status, 'accepted': False, 'block_hash': BLOCK}
        if method == 'getblockchaininfo':
            return {'chain': 'tensor-reg', 'blocks': 11, 'bestblockhash': 'ee' * 32}
        if method == 'getblockhash':
            return GENESIS
        if method == 'getmodelslist':
            return [{'model_name': m.split('@')[0], 'model_commit': m.split('@')[1], 'difficulty': 1000000}
                    for m in self.models]
        if method == 'getrecentvalidations':
            return [{'block_hash': BLOCK, 'type': 'full', 'full_status': 'green'}]
        if method == 'getblockheader':
            return {'confirmations': 1, 'height': 12}
        raise AssertionError(method)


class Producer:
    """Stands in for the engine; writes one candidate proof for the lease."""
    def __init__(self):
        self.commands, self.envs = [], []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        self.envs.append(kwargs.get('env'))
        out = Path(command[command.index('--out') + 1])
        (out / 'proofs').mkdir(parents=True)
        (out / 'proofs/9_1_x.bin').write_bytes(b'proof-bytes')

        class Process:
            pid, returncode = 999999, 0

            def wait(self, timeout=None):
                return 0
        return Process()


class PublicMinerTests(unittest.TestCase):
    def setUp(self):
        self.node = Node()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(Service(self.node, 'tnoet', GENESIS), False))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.gateway = noet_miner.Gateway(f'http://127.0.0.1:{self.server.server_address[1]}')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_full_round_trip_pays_the_miner(self):
        info = noet_miner.checked_info(self.gateway, NETWORK)
        with tempfile.TemporaryDirectory() as tmp:
            producer = Producer()
            result = noet_miner.attempt(self.gateway, NETWORK, info, ADDRESS, {'engine': 'e', 'models': {noet_miner.MODEL: 'm'}},
                                        Path(tmp), 0, runner=producer)
            record = json.loads((Path(tmp) / 'attempt-000000/job.json').read_text())
        self.assertEqual(result['outcome'], 'green')
        self.assertEqual(result['block'], BLOCK)
        self.assertEqual(record['profile'], 'noet-testnet-1')
        self.assertEqual(record['parent'], 'ee' * 32)
        self.assertEqual(self.node.submitted[0][0], 9)
        self.assertIn('--accelerator', producer.commands[0])

    def test_answers_a_public_request_through_the_gateway(self):
        from noet import open_dataset
        from noet.tasks import TaskStore
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / 'tasks.sqlite3', open_dataset.item)
            service = Service(self.node, 'tnoet', GENESIS, tasks=store)
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(service, False))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                gateway = noet_miner.Gateway(f'http://127.0.0.1:{server.server_address[1]}')
                task = service.add_task('Why is the sky blue?', '9.9.9.9')['id']
                (Path(tmp) / 'work').mkdir()

                class Answering(Producer):
                    def __call__(self, command, **kwargs):
                        process = super().__call__(command, **kwargs)
                        out = Path(command[command.index('--out') + 1])
                        (out / 'completion.json').write_text(json.dumps({'choices': [{'text':
                            ' Air scatters blue light most.\n\nQuestion: more'}]}))
                        return process
                result = noet_miner.attempt(gateway, NETWORK, noet_miner.checked_info(gateway, NETWORK), ADDRESS,
                                            {'engine': 'e', 'models': {noet_miner.MODEL: 'm'}}, Path(tmp) / 'work', 0, runner=Answering())
                record = json.loads((Path(tmp) / 'work/attempt-000000/job.json').read_text())
                view = service.task(task)
            finally:
                server.shutdown()
                server.server_close()
        self.assertEqual(record['task']['prompt'], 'Why is the sky blue?')
        self.assertEqual(result['task'], {'id': task, 'kind': 'request', 'answered': True, 'chars': 29,
                                          'truncated': False})
        self.assertEqual((view['answer'], view['block']), ('Air scatters blue light most.', BLOCK))

    def test_answer_ends_at_the_end_of_turn(self):
        self.assertEqual(noet_miner.answer_from(' Yes, it is.\n\nQuestion: next one\nAnswer: no'), ('Yes, it is.', False))
        self.assertEqual(noet_miner.answer_from('<think>\n\n</think>\n\nYes.<|im_end|>junk'), ('Yes.', False))
        self.assertEqual(noet_miner.answer_from('No end in sight'), ('No end in sight', True))
        self.assertEqual(noet_miner.answer_from(' First try.\nAnswer: second try'), ('First try.', False))
        self.assertEqual(noet_miner.answer_from('A<|endoftext|>B<|im_end|>'), ('A', False))

    def test_no_tasks_mines_without_asking_for_one(self):
        info = noet_miner.checked_info(self.gateway, NETWORK)
        with tempfile.TemporaryDirectory() as tmp:
            result = noet_miner.attempt(self.gateway, NETWORK, info, ADDRESS, {'engine': 'e', 'models': {noet_miner.MODEL: 'm'}},
                                        Path(tmp), 0, runner=Producer(), tasks=False)
            record = json.loads((Path(tmp) / 'attempt-000000/job.json').read_text())
        self.assertIsNone(record['task'])
        self.assertNotIn('task', result)

    def test_refuses_a_gateway_on_another_network(self):
        with self.assertRaises(RuntimeError):
            noet_miner.checked_info(self.gateway, {**NETWORK, 'genesis': '00' * 32})
        with self.assertRaises(RuntimeError):
            noet_miner.checked_info(self.gateway, {**NETWORK, 'hrp': 'nwrk'})

    def test_rejected_proof_is_recorded_not_retried(self):
        self.node.status = 'quick_verify_failed'
        info = noet_miner.checked_info(self.gateway, NETWORK)
        with tempfile.TemporaryDirectory() as tmp:
            result = noet_miner.attempt(self.gateway, NETWORK, info, ADDRESS, {'engine': 'e', 'models': {noet_miner.MODEL: 'm'}},
                                        Path(tmp), 0, runner=Producer())
        self.assertEqual(result['outcome'], 'no-eligible-proof')
        self.assertEqual(len(self.node.submitted), 1)

    def test_an_attempt_without_a_proof_hands_its_lease_back(self):
        class Empty(Producer):
            def __call__(self, command, **kwargs):
                process = super().__call__(command, **kwargs)
                for proof in Path(command[command.index('--out') + 1]).glob('proofs/*.bin'):
                    proof.unlink()
                return process
        info = noet_miner.checked_info(self.gateway, NETWORK)
        with tempfile.TemporaryDirectory() as tmp:
            result = noet_miner.attempt(self.gateway, NETWORK, info, ADDRESS, {'engine': 'e', 'models': {noet_miner.MODEL: 'm'}},
                                        Path(tmp), 0, runner=Empty())
        self.assertEqual(result['outcome'], 'no-eligible-proof')
        self.assertTrue(result['leaseReleased'])
        self.assertEqual(self.node.submitted, [])
        # The run time is recorded so a crash can be told from a normal miss.
        self.assertIsInstance(result['producerSeconds'], float)
        self.assertEqual(self.gateway.info()['served']['released'], 1)

    def test_attempts_continue_after_the_highest_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(noet_miner.next_attempt(Path(tmp)), 0)
            for n in (0, 1, 7):
                (Path(tmp) / f'attempt-{n:06d}').mkdir()
            self.assertEqual(noet_miner.next_attempt(Path(tmp)), 8)

    def test_address_and_url_checks(self):
        with self.assertRaises(ValueError):
            noet_miner.checked_address('nwrk1q' + 'q' * 38, NETWORK)
        noet_miner.checked_address(ADDRESS, NETWORK)
        for url in ('http://example.com', 'https://user:pw@example.com', 'https://example.com/path',
                    'ftp://example.com'):
            with self.assertRaises(ValueError):
                noet_miner.Gateway(url)
        noet_miner.Gateway('https://example.com:9443')

    def test_gateway_errors_carry_status(self):
        with self.assertRaises(noet_miner.GatewayError) as caught:
            self.gateway.lease('tnoet1bad')
        self.assertEqual(caught.exception.status, 400)


    def test_preflight_reports_every_problem_in_plain_words(self):
        import argparse
        import subprocess
        from unittest import mock

        def smi(rows):
            def run(command, **kwargs):
                return subprocess.CompletedProcess(command, 0, stdout='\n'.join(rows) + '\n')
            return run
        with tempfile.TemporaryDirectory() as tmp:
            engine, model = Path(tmp) / 'llama-server', Path(tmp) / 'model.gguf'
            engine.write_text('#!/bin/sh\n')
            engine.chmod(0o755)
            model.write_bytes(b'not the model')
            args = argparse.Namespace(address=ADDRESS, engine=engine, model=[model], out=Path(tmp) / 'work')
            with mock.patch.object(noet_miner.shutil, 'which', return_value='/usr/bin/nvidia-smi'), \
                    mock.patch.dict(noet_miner.MODELS[noet_miner.MODEL], {'sha256': noet_miner.file_sha256(model)}):
                good = noet_miner.preflight(args, NETWORK, self.gateway,
                                            run=smi(['NVIDIA RTX A5000, 570.86, 24564, 8.6']))
                self.assertTrue(all(ok for _, ok, _ in good), good)
                bad = noet_miner.preflight(args, NETWORK, self.gateway,
                                           run=smi(['GeForce GTX 1080, 535.10, 8192, 6.1']))
            failed = {name: detail for name, ok, detail in bad if not ok}
            self.assertEqual(set(failed), {'NVIDIA GPU'})
            self.assertIn('driver 535.10', failed['NVIDIA GPU'])
            self.assertIn('compute capability 6.1', failed['NVIDIA GPU'])
            with mock.patch.object(noet_miner.shutil, 'which', return_value=None):
                missing = dict((n, ok) for n, ok, _ in noet_miner.preflight(args, NETWORK, self.gateway))
            self.assertFalse(missing['NVIDIA GPU'])
            self.assertFalse(missing['Model'])

    def test_the_switch_height_picks_the_model_and_its_weights(self):
        # design/MODEL-SWITCH.md: the job's height decides the model, the GGUF and the proof's registry entry.
        switched = {**NETWORK, 'switch': (12, noet_miner.SWITCH_MODEL)}
        node = Node(models=[noet_miner.MODEL, noet_miner.SWITCH_MODEL])
        service = Service(node, 'tnoet', GENESIS)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(service, False))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        gateway = noet_miner.Gateway(f'http://127.0.0.1:{server.server_address[1]}')
        info = noet_miner.checked_info(gateway, switched)
        models = {noet_miner.MODEL: 'small.gguf', noet_miner.SWITCH_MODEL: 'large.gguf'}
        producer = Producer()
        with tempfile.TemporaryDirectory() as tmp:
            result = noet_miner.attempt(gateway, switched, info, ADDRESS, {'engine': 'e', 'models': models},
                                        Path(tmp), 0, runner=producer, tasks=False)
            record = json.loads((Path(tmp) / 'attempt-000000/job.json').read_text())
        self.assertEqual(result['model'], noet_miner.SWITCH_MODEL)  # the job is for height 12
        self.assertEqual(producer.commands[0][producer.commands[0].index('--model') + 1], 'large.gguf')
        self.assertEqual(producer.envs[0]['NOET_MODEL_ID'], noet_miner.SWITCH_MODEL)
        self.assertEqual(record['model']['model_name'] + '@' + record['model']['model_commit'],
                         noet_miner.SWITCH_MODEL)
        # Without the new model's weights the lease goes back and the miner is told what it needs.
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(noet_miner.ModelUnavailable) as caught:
            noet_miner.attempt(gateway, switched, info, ADDRESS, {'engine': 'e', 'models': {noet_miner.MODEL: 'm'}},
                               Path(tmp), 0, runner=Producer(), tasks=False)
        self.assertEqual((caught.exception.model, caught.exception.height), (noet_miner.SWITCH_MODEL, 12))
        self.assertEqual(service.stats['released'], 1)
        # A miner that does not know the switch refuses a node listing the new model.
        with self.assertRaisesRegex(RuntimeError, 'update the miner'):
            noet_miner.checked_info(gateway, NETWORK)

    def test_model_files_are_identified_by_their_pinned_checksum(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            small, large, other = (Path(tmp) / name for name in ('a.gguf', 'b.gguf', 'c.gguf'))
            for path in (small, large, other):
                path.write_bytes(path.name.encode())
            pins = {noet_miner.MODEL: {'sha256': noet_miner.file_sha256(small)},
                    noet_miner.SWITCH_MODEL: {'sha256': noet_miner.file_sha256(large)}}
            with mock.patch.dict(noet_miner.MODELS[noet_miner.MODEL], pins[noet_miner.MODEL]), \
                    mock.patch.dict(noet_miner.MODELS[noet_miner.SWITCH_MODEL], pins[noet_miner.SWITCH_MODEL]):
                self.assertEqual(noet_miner.models_given([large, small]),
                                 {noet_miner.MODEL: small, noet_miner.SWITCH_MODEL: large})
                with self.assertRaisesRegex(RuntimeError, 'differs from every pinned'):
                    noet_miner.models_given([other])
                with self.assertRaisesRegex(RuntimeError, 'not found'):
                    noet_miner.models_given([Path(tmp) / 'missing.gguf'])

    def test_model_files_are_found_across_the_image_and_work_directories(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            image, work = Path(tmp) / 'models', Path(tmp) / 'work-models'
            image.mkdir()
            work.mkdir()
            small = image / noet_miner.MODELS[noet_miner.MODEL]['gguf']
            large = work / noet_miner.MODELS[noet_miner.SWITCH_MODEL]['gguf']
            small.write_bytes(b'0.6b')
            with mock.patch.dict('os.environ', {'NOET_MODEL_DIR': f'{image}:{work}'}):
                self.assertEqual(noet_miner.default_models(), [small])
                large.write_bytes(b'14b')  # built into the volume on first start
                self.assertEqual(noet_miner.default_models(), [small, large])
            with mock.patch.dict('os.environ', {'NOET_MODEL_DIR': str(work)}):
                self.assertEqual(noet_miner.default_models(), [large])

    def test_the_miner_pins_each_network_model_schedule(self):
        spec = json.loads((Path(__file__).resolve().parents[1] / 'mainnet/network.json').read_text())
        switch = spec['modelSwitch']
        self.assertEqual(noet_miner.SWITCH_MODEL, switch['model'] + '@' + switch['revision'])
        expected = None if switch['height'] is None else (switch['height'], noet_miner.SWITCH_MODEL)
        self.assertEqual(noet_miner.NETWORKS['mainnet']['switch'], expected)
        profiles = json.loads((Path(__file__).resolve().parents[1] / 'models/profiles.json').read_text())
        for model, pin in noet_miner.MODELS.items():
            profile = next(p for p in profiles.values() if p['model'] + '@' + p['revision'] == model)
            self.assertEqual((pin['gguf'], pin['sha256']), (profile['gguf'], profile['ggufSha256']))

    def test_backoff_grows_and_stops_promptly(self):
        self.assertEqual([noet_miner.backoff(n, 0) for n in (1, 2, 3, 7, 20)], [15, 30, 60, 300, 300])
        self.assertEqual(noet_miner.backoff(5, 429), 60)
        slept = []
        stopping = []

        def sleep(seconds):
            slept.append(seconds)
            stopping.append(True)
        noet_miner.wait(300, stopping, sleep=sleep)
        self.assertEqual(len(slept), 1)


    def test_payout_address_decodes_like_the_chain(self):
        # Block 250 of the development network pays the faucet address; its
        # coinbase script is ground truth for the decoder.
        raw = bytes.fromhex((Path(__file__).parent / 'fixtures/block-private-work-250.hex').read_text().strip())
        coinbase = raw[118:122] + raw[124:]  # drop the segwit marker and flag
        _, script = noet_miner._first_paid_output(coinbase)
        self.assertEqual(noet_miner.payout_script('nwrk1qg4lfpy6502l59vgkx38sxm4yu75dzvyff5knq9', 'nwrk'), script)
        for bad in ('nwrk1qg4lfpy6502l59vgkx38sxm4yu75dzvyff5knq8', 'tnoet1qg4lfpy6502l59vgkx38sxm4yu75dzvyff5knq9'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                noet_miner.payout_script(bad, 'nwrk')

    def test_work_is_refused_unless_its_coinbase_pays_this_miner(self):
        import hashlib
        mine = noet_miner.payout_script('nwrk1qg4lfpy6502l59vgkx38sxm4yu75dzvyff5knq9', 'nwrk')
        other = noet_miner.payout_script('nwrk1qk86e9uufkl426ecte993kc8rwdl94rwkgngyst', 'nwrk')

        def coinbase(script):
            return (b'\x01\x00\x00\x00' + b'\x01' + bytes(32) + b'\xff\xff\xff\xff' + b'\x03\x01\x02\x03'
                    + b'\xff\xff\xff\xff' + b'\x02' + (0).to_bytes(8, 'little') + b'\x01\x6a'
                    + (22831050228).to_bytes(8, 'little') + bytes([len(script)]) + script + bytes(4))

        def job(cb, branch):
            d = lambda b: hashlib.sha256(hashlib.sha256(b).digest()).digest()
            root = d(cb)
            for sibling in branch:
                root = d(root + sibling)
            return {'header_prefix': (bytes(36) + root + bytes(8)).hex(), 'coinbase_tx': cb.hex(),
                    'coinbase_merkle_branch': [b.hex() for b in branch]}
        branch = [b'\x11' * 32, b'\x22' * 32]
        address = 'nwrk1qg4lfpy6502l59vgkx38sxm4yu75dzvyff5knq9'
        self.assertTrue(noet_miner.verify_payout(job(coinbase(mine), branch), address, 'nwrk'))
        with self.assertRaisesRegex(RuntimeError, 'different address'):
            noet_miner.verify_payout(job(coinbase(other), branch), address, 'nwrk')
        forged = job(coinbase(mine), branch)
        forged['coinbase_merkle_branch'][0] = '33' * 32
        with self.assertRaisesRegex(RuntimeError, 'does not match its header'):
            noet_miner.verify_payout(forged, address, 'nwrk')
        self.assertIsNone(noet_miner.verify_payout({'header_prefix': '00' * 76}, address, 'nwrk'))




class ProducerFailureTests(unittest.TestCase):
    def test_only_a_crashed_producer_counts_as_a_failure(self):
        failed = noet_miner.producer_failed
        # Pod 3, 16 Oct: "Unknown network profile" exited 1 after 0.1 s (a crash);
        # "No eligible evidence" (no proof under target) exited 1 after 22.5-28.5 s.
        self.assertTrue(failed({'producerExit': 1, 'producerSeconds': 0.1, 'submissions': []}))
        self.assertFalse(failed({'producerExit': 1, 'producerSeconds': 22.5, 'submissions': []}))
        self.assertFalse(failed({'producerExit': 0, 'producerSeconds': 0.1, 'submissions': []}))
        self.assertFalse(failed({'producerExit': -9, 'producerTimeout': True, 'producerSeconds': 900,
                                 'submissions': []}))
        self.assertFalse(failed({'producerExit': 1, 'producerSeconds': 0.1, 'submissions': [{'response': {}}]}))
        self.assertEqual(noet_miner.backoff(1, 0), 15)
        self.assertEqual(noet_miner.backoff(9, 0), 300)

class SettleTests(unittest.TestCase):
    class Gateway:
        def __init__(self, blocks, tips):
            self.blocks, self.tips = list(blocks), list(tips)

        def block(self, block_hash):
            return self.blocks.pop(0)

        def info(self):
            return {'height': self.tips.pop(0)}

    def settle(self, gateway, timeout=900):
        clock = iter(range(0, 10 ** 6, 5))
        return noet_miner.settle(gateway, BLOCK, timeout, 12, sleep=lambda s: None, clock=lambda: next(clock))

    def test_a_block_that_lost_the_race_is_stale_at_once(self):
        # 24 Oct 2026, height 1650: our proof reached the node 3 s after another
        # block at that height was connected; the miner then waited 15 minutes.
        pending = {'block': BLOCK, 'height': 12, 'inBestChain': False, 'full': None}
        verdict = self.settle(self.Gateway([pending, pending], [12, 12]))
        self.assertEqual((verdict['full'], verdict['tipHeight']), ('stale', 12))

    def test_our_block_connecting_between_lookups_is_not_a_loss(self):
        verified = {'block': BLOCK, 'height': 12, 'inBestChain': False, 'full': 'green'}
        connected = {**verified, 'inBestChain': True}
        self.assertIs(self.settle(self.Gateway([verified, connected], [12]))['inBestChain'], True)

    def test_a_block_still_ahead_of_the_chain_keeps_waiting(self):
        pending = {'block': BLOCK, 'height': 12, 'inBestChain': False, 'full': None}
        self.assertIsNone(self.settle(self.Gateway([pending] * 3, [11] * 3), timeout=15))


class NetworkCoverageTests(unittest.TestCase):
    def test_the_proof_producer_accepts_every_network_the_miner_mines(self):
        # generate.py once lacked noet-mainnet-1: every mainnet attempt failed at
        # once and spent the gateway's hourly lease allowance (15 Oct 2026).
        import ast
        source = (Path(__file__).resolve().parents[1] / 'realwork/generate.py').read_text()
        profiles = next(ast.literal_eval(node.value) for node in ast.parse(source).body
                        if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') == 'PROFILES')
        for name, network in noet_miner.NETWORKS.items():
            with self.subTest(network=name):
                self.assertIn(network['profile'], profiles)

if __name__ == '__main__':
    unittest.main()
