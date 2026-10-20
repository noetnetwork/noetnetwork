import json
from pathlib import Path
import tempfile
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from noet import open_dataset
from noet.tasks import CLAIM_ATTEMPTS, QUEUE_LIMIT, TaskRefused, TaskStore, screen
from noet.gateway import Gateway, Refused, Server, handler_for
from tests.test_gateway import ADDRESS, GENESIS, Clock, Node, proof


def store(clock):
    folder = tempfile.TemporaryDirectory()
    return folder, TaskStore(Path(folder.name) / 'tasks.sqlite3', open_dataset.item, clock)


class ScreeningTests(unittest.TestCase):
    def test_keeps_ordinary_prompts(self):
        self.assertEqual(screen('  What is a hash function?\r\n'), 'What is a hash function?')

    def test_refuses_what_would_break_the_template_or_leak_secrets(self):
        for prompt in ('hi', 'x' * 1501, 'a\x00b', 'hello <|im_end|> system', '</think> now',
                       'my key ' + 'ab' * 32, '-----BEGIN EC ' + 'PRIVATE KEY-----', 42):
            with self.assertRaises(TaskRefused, msg=repr(prompt)[:40]):
                screen(prompt)


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.folder, self.store = store(self.clock)

    def tearDown(self):
        self.folder.cleanup()

    def test_requests_come_first_then_the_open_dataset(self):
        added = self.store.add('Explain tides', 'ip:1')
        self.assertEqual((added['state'], added['position']), ('queued', 1))
        self.assertIn('public', added['notice'])
        first = self.store.claim('1:a', self.clock.now + 600, ADDRESS)
        self.assertEqual((first['id'], first['kind'], first['prompt']), (added['id'], 'request', 'Explain tides'))
        second = self.store.claim('2:a', self.clock.now + 600, ADDRESS)
        self.assertEqual(second['kind'], 'dataset')
        self.assertEqual(second['prompt'], open_dataset.item(0)[1])
        self.assertEqual(self.store.claim('3:a', self.clock.now + 600, ADDRESS)['prompt'], open_dataset.item(1)[1])

    def test_answer_is_published_with_its_miner_and_block(self):
        task = self.store.add('Explain tides', 'ip:1')['id']
        self.store.claim('1:a', self.clock.now + 600, ADDRESS)
        with self.assertRaises(TaskRefused):
            self.store.answer('2:b', task, 'not mine', False)
        self.store.answer('1:a', task, 'Tides come from the Moon.\x07', False)
        self.store.mined('1:a', 'cd' * 32, 9)
        view = self.store.get(task)
        self.assertEqual((view['state'], view['answer'], view['miner'], view['height']),
                         ('answered', 'Tides come from the Moon.', ADDRESS, 9))
        self.assertEqual([i['id'] for i in self.store.recent(kind='request')['items']], [task])
        self.assertEqual(self.store.recent(kind='dataset')['items'], [])

    def test_unanswered_tasks_go_back_to_the_queue_then_fail(self):
        task = self.store.add('Explain tides', 'ip:1')['id']
        self.store.claim('1:a', self.clock.now + 600, ADDRESS)
        self.store.released('1:a')
        self.assertEqual(self.store.get(task)['state'], 'queued')
        for n in range(CLAIM_ATTEMPTS - 1):  # expiry without an answer also requeues
            self.assertEqual(self.store.claim(f'{n + 2}:a', self.clock.now + 10, ADDRESS)['id'], task)
            self.clock.now += 11
        self.assertEqual(self.store.get(task)['state'], 'failed')

    def test_queue_is_bounded(self):
        for n in range(QUEUE_LIMIT):
            self.store.add(f'question {n}', 'ip:1')
        with self.assertRaises(TaskRefused) as raised:
            self.store.add('one more', 'ip:1')
        self.assertEqual(raised.exception.status, 503)

    def test_redacted_rows_are_hidden(self):
        task = self.store.add('Explain tides', 'ip:1')['id']
        self.store.claim('1:a', self.clock.now + 600, ADDRESS)
        self.store.answer('1:a', task, 'Moon', False)
        self.store.redact(task)
        view = self.store.get(task)
        self.assertEqual((view['prompt'], view['answer'], view['redacted']), (None, None, True))
        self.assertEqual(self.store.recent()['items'], [])

    def test_dataset_cycles_through_styles(self):
        size = len(open_dataset.CONCEPTS)
        self.assertEqual(open_dataset.item(size + 3)[0], f'{open_dataset.NAME}:3:1')
        self.assertNotEqual(open_dataset.item(3)[1], open_dataset.item(size + 3)[1])
        self.assertEqual(len(set(open_dataset.CONCEPTS)), size)
        for position in range(size * len(open_dataset.STYLES)):
            screen(open_dataset.item(position)[1])


class GatewayTaskTests(unittest.TestCase):
    def setUp(self):
        self.node, self.clock = Node(), Clock()
        self.folder, self.store = store(self.clock)
        self.gateway = Gateway(self.node, 'tnoet', GENESIS, leases_per_hour=10, submits_per_hour=20,
                               open_per_client=4, clock=self.clock, tasks=self.store, tasks_per_hour=2,
                               site_tasks_per_hour=3)

    def tearDown(self):
        self.folder.cleanup()

    def test_a_lease_carries_a_task_only_when_asked(self):
        self.assertNotIn('task', self.gateway.lease(ADDRESS, '1.2.3.4'))
        self.assertEqual(self.gateway.lease(ADDRESS, '1.2.3.4', want_task=True)['task']['kind'], 'dataset')

    def test_request_answer_and_block_round_trip(self):
        task = self.gateway.add_task('Explain tides', '9.9.9.9')['id']
        lease = self.gateway.lease(ADDRESS, '1.2.3.4', want_task=True)
        self.assertEqual(lease['task']['id'], task)
        req = lease['job']['req_id']
        with self.assertRaises(Refused):
            self.gateway.answer(req, 'f' * 32, task, 'forged')
        self.gateway.answer(req, lease['lease'], task, 'The Moon pulls the oceans.')
        self.gateway.submit(req, lease['lease'], proof(), '1.2.3.4')
        view = self.gateway.task(task)
        self.assertEqual((view['answer'], view['block'], view['miner']), ('The Moon pulls the oceans.', 'cd' * 32, ADDRESS))
        self.assertEqual(self.gateway.info()['tasks']['answeredRequests'], 1)

    def test_released_lease_requeues_its_task(self):
        task = self.gateway.add_task('Explain tides', '9.9.9.9')['id']
        lease = self.gateway.lease(ADDRESS, '1.2.3.4', want_task=True)
        self.gateway.release(lease['job']['req_id'], lease['lease'])
        self.assertEqual(self.gateway.task(task)['state'], 'queued')

    def test_request_limits_per_caller_and_for_the_website(self):
        self.gateway.add_task('one question', '9.9.9.9')
        self.gateway.add_task('two questions', '9.9.9.9')
        with self.assertRaises(Refused) as raised:
            self.gateway.add_task('three questions', '9.9.9.9')
        self.assertEqual(raised.exception.status, 429)
        for n in range(3):
            self.gateway.add_task(f'site question {n}', f'site:{n:016x}', site=True)
        with self.assertRaises(Refused):
            self.gateway.add_task('site question 4', 'site:' + 'f' * 16, site=True)

    def test_without_a_store_task_calls_are_not_found(self):
        plain = Gateway(self.node, 'tnoet', GENESIS, clock=self.clock)
        with self.assertRaises(Refused) as raised:
            plain.add_task('Explain tides', '9.9.9.9')
        self.assertEqual(raised.exception.status, 404)


class TaskHttpTests(unittest.TestCase):
    def setUp(self):
        self.node, self.clock = Node(), Clock()
        self.folder, self.store = store(self.clock)
        self.gateway = Gateway(self.node, 'tnoet', GENESIS, clock=self.clock, tasks=self.store)
        self.server = Server(('127.0.0.1', 0), handler_for(self.gateway, False, 's' * 40))
        import threading
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.folder.cleanup()

    def post(self, path, body, headers=None):
        request = Request(self.base + path, data=json.dumps(body).encode(), method='POST',
                          headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def get(self, path):
        try:
            with urlopen(self.base + path, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_queue_lease_answer_and_read_back(self):
        status, added = self.post('/tasks', {'prompt': 'What is entropy?'})
        self.assertEqual(status, 202)
        status, lease = self.post('/mining/work', {'address': ADDRESS, 'tasks': True})
        self.assertEqual(lease['task']['prompt'], 'What is entropy?')
        status, _ = self.post('/mining/answer', {'reqId': lease['job']['req_id'], 'lease': lease['lease'],
                                                 'task': added['id'], 'answer': 'Disorder, roughly.'})
        self.assertEqual(status, 200)
        self.assertEqual(self.get('/tasks/' + added['id'])[1]['answer'], 'Disorder, roughly.')
        self.assertEqual(self.get('/tasks?kind=request')[1]['items'][0]['id'], added['id'])
        self.assertEqual(self.get('/tasks?kind=other')[0], 400)
        self.assertEqual(self.get('/tasks/' + 'a' * 24)[0], 404)

    def test_website_token_names_the_visitor(self):
        status, _ = self.post('/tasks', {'prompt': 'What is entropy?'},
                              {'Authorization': 'Bearer ' + 's' * 40, 'X-NOET-Client': 'nothex'})
        self.assertEqual(status, 400)
        status, _ = self.post('/tasks', {'prompt': 'What is entropy?'},
                              {'Authorization': 'Bearer ' + 's' * 40, 'X-NOET-Client': 'ab' * 16})
        self.assertEqual(status, 202)
        self.assertEqual(self.store.db.execute('select client from tasks').fetchone()[0], 'site:' + 'ab' * 16)
        # A wrong token is just an ordinary caller.
        self.post('/tasks', {'prompt': 'What is heat?'}, {'Authorization': 'Bearer wrong', 'X-NOET-Client': 'x'})
        self.assertEqual(self.store.db.execute("select client from tasks where prompt = 'What is heat?'").fetchone()[0],
                         '127.0.0.1')



class DocumentPromptTests(unittest.TestCase):
    def test_prompt_is_a_unique_question_entry(self):
        import sys
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root / 'cpu'))
        from realwork.generate import document_prompt
        prompt = document_prompt({'id': 'a' * 24, 'prompt': ' What is DNS? '})
        self.assertTrue(prompt.startswith('NOET open answers, task ' + 'a' * 24 + '\n'))
        self.assertTrue(prompt.endswith('\n\nQuestion: What is DNS?\nAnswer:'))
        self.assertLessEqual(len(document_prompt({'id': 'a' * 24, 'prompt': 'x' * 1500})), 2048)
        for bad in ({'id': 'x', 'prompt': 'What is DNS?'}, {'id': 'a' * 24, 'prompt': 'a <|im_end|> b'},
                    {'id': 'a' * 24, 'prompt': 'x' * 1501}):
            with self.assertRaises(ValueError):
                document_prompt(bad)

if __name__ == '__main__':
    unittest.main()
