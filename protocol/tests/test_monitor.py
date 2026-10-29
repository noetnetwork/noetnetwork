import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('noet_monitor', ROOT / 'deploy/monitor/monitor.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class MonitorTests(unittest.TestCase):
    def test_problems_from_health(self):
        healthy = {'ok': True, 'height': 10, 'lastBlockAgeSeconds': 300, 'relayCertificateDaysLeft': 20}
        self.assertEqual(monitor.problems(healthy, 5400), [])
        self.assertEqual(monitor.problems(None, 5400), ['health endpoint unreachable'])
        stalled = {**healthy, 'lastBlockAgeSeconds': 6000, 'problems': ['no recent block', 'index stale'],
                   'relayCertificateDaysLeft': 3}
        found = monitor.problems(stalled, 5400)
        self.assertEqual(len(found), 3)
        self.assertIn('no block for over 90 min', found[0])
        # The node's own one-hour stall flag alone is not an alert: quiet hours happen.
        self.assertEqual(monitor.problems({**healthy, 'lastBlockAgeSeconds': 4000,
                                           'problems': ['no recent block']}, 5400), [])

    def test_alerts_after_two_checks_reminds_and_recovers(self):
        now = [0.0]
        watch = monitor.Watch('mainnet', clock=lambda: now[0])
        self.assertIsNone(watch.update(['x']))
        self.assertEqual(watch.update(['x']), 'NOET mainnet: x')
        now[0] += 600
        self.assertIsNone(watch.update(['x']))
        self.assertEqual(watch.update(['x', 'y']), 'NOET mainnet: still: x; y')
        now[0] += monitor.REMIND_SECONDS
        self.assertEqual(watch.update(['x', 'y']), 'NOET mainnet: still: x; y')
        self.assertEqual(watch.update([]), 'NOET mainnet: recovered.')
        self.assertIsNone(watch.update([]))
        self.assertIsNone(watch.update(['x']))  # a single failed check stays quiet

    def test_a_block_waiting_long_for_its_verdict_alerts(self):
        self.assertEqual(monitor.verdict_problems({'height': 9}), [])
        self.assertEqual(monitor.verdict_problems({'awaitingVerdict': {'height': 10, 'seconds': 120}}), [])
        found = monitor.verdict_problems({'awaitingVerdict': {'height': 10, 'seconds': 960}})
        self.assertEqual(len(found), 1)
        self.assertIn('block 10 has waited 16 min', found[0])
        self.assertEqual(monitor.verdict_problems(None), ['mining gateway unreachable'])

    def test_gpu_pod_and_credit_problems(self):
        account = {'clientBalance': 224.11, 'currentSpendPerHr': 0.281,
                   'pods': [{'id': 'pod1', 'desiredStatus': 'RUNNING'}, {'id': 'other', 'desiredStatus': 'EXITED'}]}
        self.assertEqual(monitor.pod_problems(account, 'pod1'), [])
        self.assertEqual(monitor.credit_problems(account), [])
        self.assertEqual(monitor.pod_problems(None, 'pod1'), ['Runpod API unreachable'])
        self.assertEqual(monitor.credit_problems(None), [])
        self.assertEqual(monitor.pod_problems(account, 'other'), ['pod other is exited; the verifier and miner are down'])
        self.assertEqual(monitor.pod_problems(account, 'missing'), ['pod missing is gone; the verifier and miner are down'])
        low = {**account, 'clientBalance': 40.0}  # 5.9 days at 0.281/h
        self.assertEqual(monitor.credit_problems(low),
                         ['Runpod credit runs out in about 5 days at $0.281/h; add credit or the pod stops'])
        self.assertEqual(monitor.credit_problems({**low, 'currentSpendPerHr': 0}), [])

    def test_credit_warning_repeats_daily_not_every_check(self):
        now = [0.0]
        watch = monitor.Watch('GPU credit', clock=lambda: now[0], remind=86400)
        account = {'clientBalance': 40.0, 'currentSpendPerHr': 0.281, 'pods': []}
        alerts = []
        for _ in range(24 * 30):  # two-minute checks for a day
            alerts.append(watch.update(monitor.credit_problems(account)))
            now[0] += 120
            account['clientBalance'] -= 0.281 / 30
        sent = [a for a in alerts if a]
        self.assertEqual(len(sent), 2)  # about 5 days, then about 4 days
        self.assertIn('about 5 days', sent[0])
        self.assertIn('about 4 days', sent[1])

    def test_switch_reminder_in_the_last_three_days_until_ready(self):
        health = {'ok': True, 'height': 3500 - 433, 'lastBlockAgeSeconds': 60}
        self.assertEqual(monitor.switch_problems(health, 3500, False), [])
        for height, when in ((3500 - 432, 'in about 3 days'), (3500 - 200, 'in about 1 day'),
                             (3499, 'within a day')):
            found = monitor.switch_problems({**health, 'height': height}, 3500, False)
            self.assertEqual(len(found), 1)
            self.assertIn(f'block 3,500 comes {when};', found[0])
        self.assertEqual(monitor.switch_problems({**health, 'height': 3400}, 3500, True), [])
        self.assertEqual(monitor.switch_problems({**health, 'height': 3500}, 3500, False), [])
        self.assertEqual(monitor.switch_problems({**health, 'height': 3400}, 0, False), [])
        self.assertEqual(monitor.switch_problems(None, 3500, False), [])
        # Two-minute checks over two days of 10-minute blocks: one alert as each day begins.
        now = [0.0]
        watch = monitor.Watch('model switch', clock=lambda: now[0], remind=86400)
        sent = []
        for i in range(24 * 30 * 2):
            sent.append(watch.update(monitor.switch_problems({**health, 'height': 3500 - 300 + i // 5}, 3500, False)))
            now[0] += 120
        sent = [a for a in sent if a]
        self.assertEqual([a.split(' comes ')[1].split(';')[0] for a in sent],
                         ['in about 2 days', 'in about 1 day', 'within a day'])

    def test_runpod_reply_is_parsed_and_failures_are_none(self):
        reply = {'data': {'myself': {'clientBalance': 224.1118, 'currentSpendPerHr': 0.281,
                                     'pods': [{'id': 'pod1', 'desiredStatus': 'RUNNING'}]}}}

        class Response:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                return self.body

        seen = []

        def opener(request, timeout):
            seen.append(request)
            return Response(json.dumps(reply).encode())
        with mock.patch.object(monitor.urllib.request, 'urlopen', opener):
            account = monitor.runpod('k')
        self.assertEqual(account, {'clientBalance': 224.1118, 'currentSpendPerHr': 0.281,
                                   'pods': [{'id': 'pod1', 'desiredStatus': 'RUNNING'}]})
        self.assertEqual(seen[0].get_header('Authorization'), 'Bearer k')
        self.assertNotIn(b'mutation', seen[0].data)
        for body in (b'not json', b'{"errors": [{"message": "unauthorized"}]}', b'{"data": {"myself": null}}'):
            with mock.patch.object(monitor.urllib.request, 'urlopen', lambda request, timeout: Response(body)):
                self.assertIsNone(monitor.runpod('k'))

    def test_targets_need_https(self):
        self.assertEqual(monitor.targets('mainnet=https://a.example, testnet=https://b.example'),
                         [('mainnet', 'https://a.example'), ('testnet', 'https://b.example')])
        with self.assertRaises(SystemExit):
            monitor.targets('mainnet=http://a.example')

    def test_send_without_channels_sends_nothing(self):
        self.assertEqual(monitor.send('hello', env={}), [])


if __name__ == '__main__':
    unittest.main()
