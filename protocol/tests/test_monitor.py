import calendar
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
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
        self.assertEqual(watch.update(['x']), '\U0001F534 NOET mainnet: x')
        now[0] += 600
        self.assertIsNone(watch.update(['x']))
        self.assertEqual(watch.update(['x', 'y']), '\U0001F534 Still: NOET mainnet: x; y')
        now[0] += monitor.REMIND_SECONDS
        self.assertEqual(watch.update(['x', 'y']), '\U0001F534 Still: NOET mainnet: x; y')
        self.assertEqual(watch.update([]), '\U0001F7E2 NOET mainnet: recovered.')
        self.assertIsNone(watch.update([]))
        self.assertIsNone(watch.update(['x']))  # a single failed check stays quiet

    def test_a_long_stall_reminds_every_few_hours_not_every_check(self):
        now = [0.0]
        watch = monitor.Watch('mainnet', clock=lambda: now[0])
        health = {'ok': True, 'height': 3446, 'lastBlockAgeSeconds': 6000}
        sent = []
        for _ in range(30 * 6):  # two-minute checks for six hours
            sent.append(watch.update(monitor.problems(health, 5400)))
            now[0] += 120
            health['lastBlockAgeSeconds'] += 120
        sent = [a for a in sent if a]
        self.assertEqual(len(sent), 2)  # the alert, then one reminder three hours on
        self.assertTrue(sent[1].startswith('\U0001F534 Still: NOET mainnet: '))
        waiting = {'awaitingVerdict': {'height': 10, 'seconds': 960}}
        self.assertEqual(monitor.verdict_problems(waiting), monitor.verdict_problems({'awaitingVerdict': {'height': 10, 'seconds': 1500}}))

    def test_a_block_waiting_long_for_its_verdict_alerts(self):
        self.assertEqual(monitor.verdict_problems({'height': 9}), [])
        self.assertEqual(monitor.verdict_problems({'awaitingVerdict': {'height': 10, 'seconds': 120}}), [])
        found = monitor.verdict_problems({'awaitingVerdict': {'height': 10, 'seconds': 960}})
        self.assertEqual(len(found), 1)
        self.assertIn('block 10 has waited over 15 min', found[0])
        self.assertEqual(monitor.verdict_problems(None), ['mining gateway unreachable'])

    def test_an_open_verdict_alert_stays_open_when_the_gateway_lets_the_block_go(self):
        # After an hour the gateway no longer names the waiting block; that is not a verdict.
        alert = ['block 10 has waited over 15 min for its Full verdict; the GPU verifier is probably down']
        quiet = {'height': 9}
        self.assertEqual(monitor.verdict_problems(quiet, 900, {'lastVerdictAgeSeconds': 4000}, alert), alert)
        self.assertEqual(monitor.verdict_problems(quiet, 900, {}, alert), alert)
        # A fresh verdict closes it; so does no open alert, or a block named again but young.
        self.assertEqual(monitor.verdict_problems(quiet, 900, {'lastVerdictAgeSeconds': 30}, alert), [])
        self.assertEqual(monitor.verdict_problems(quiet, 900, {'lastVerdictAgeSeconds': 4000}, None), [])
        self.assertEqual(monitor.verdict_problems({'awaitingVerdict': {'height': 11, 'seconds': 20}}, 900,
                                                  {'lastVerdictAgeSeconds': 10}, alert), [])

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
                         ['Runpod credit runs out in about 5 days (at $0.281/h); add credit or the pod stops'])
        self.assertEqual(monitor.credit_problems({**low, 'currentSpendPerHr': 0}), [])

    def test_credit_warning_escalates_in_the_last_day(self):
        account = {'clientBalance': 0, 'currentSpendPerHr': 0.601, 'pods': []}
        texts = [monitor.credit_problems({**account, 'clientBalance': 0.601 * hours})[0] for hours in (30, 20, 10, 5, 2, 0.5)]
        self.assertIn('within 2 days', texts[0])
        self.assertIn('under 24 hours', texts[1])
        self.assertIn('under 12 hours', texts[2])
        self.assertIn('under 6 hours', texts[3])
        self.assertIn('under 3 hours', texts[4])
        self.assertIn('under 1 hour ', texts[5])
        self.assertTrue(all(t.startswith('URGENT') for t in texts[1:]))
        self.assertEqual(len(set(texts)), 6)  # each step changes the text, so each one alerts

    def test_reorganisation_reported_once(self):
        reorgs = monitor.Reorgs()
        chain = [{'height': h, 'hash': f'a{h}'} for h in range(10, 5, -1)]
        self.assertEqual(reorgs.update(chain), [])
        self.assertEqual(reorgs.update(None), [])
        self.assertEqual(reorgs.update([{'height': 11, 'hash': 'a11'}] + chain), [])
        forked = [{'height': 11, 'hash': 'b11'}, {'height': 10, 'hash': 'b10'}] + chain[1:]
        found = reorgs.update(forked)
        self.assertEqual(len(found), 1)
        self.assertIn('2 blocks replaced from height 10 (depth 2)', found[0])
        self.assertEqual(reorgs.update(forked), [])

    def test_certificate_warning_turns_urgent(self):
        healthy = {'ok': True, 'height': 10, 'lastBlockAgeSeconds': 300}
        self.assertNotIn('URGENT', monitor.problems({**healthy, 'relayCertificateDaysLeft': 10}, 5400)[0])
        self.assertIn('URGENT', monitor.problems({**healthy, 'relayCertificateDaysLeft': 3}, 5400)[0])

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


    def test_send_reaches_every_approved_chat_and_reorgs_only_the_owner(self):
        bot = monitor.Bot([111], status=lambda: 'ok')
        bot.approve(-5, '"Team"', 111)
        sent = []
        with mock.patch.object(monitor, 'deliver', lambda token, chat, text, b=None: sent.append(chat) or True):
            self.assertEqual(monitor.send('x', env={'TELEGRAM_BOT_TOKEN': 't'}, bot=bot), ['telegram'])
            self.assertEqual(sent, [111, -5])
            sent.clear()
            monitor.send('x', env={'TELEGRAM_BOT_TOKEN': 't'}, bot=bot, owner_only=True)
            self.assertEqual(sent, [111])
            sent.clear()
            # Without a bot the owner chats come from TELEGRAM_CHAT_ID, as before.
            monitor.send('x', env={'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_CHAT_ID': '111, -7'})
            self.assertEqual(sent, [111, -7])


class BotTests(unittest.TestCase):
    OWNER = 8795770673

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'chats.json')
        self.bot = monitor.Bot([self.OWNER], path=self.path, username='noet_alerts_bot', status=lambda: 'STATUS')

    def tearDown(self):
        self.dir.cleanup()

    def message(self, text, chat_id, chat_type='private', sender=None, title='Team'):
        chat = {'id': chat_id, 'type': chat_type, **({'title': title} if chat_type != 'private' else {'first_name': 'Ana'})}
        return {'update_id': 1, 'message': {'chat': chat, 'text': text,
                                            'from': {'id': sender or chat_id, 'first_name': 'Ana', 'username': 'ana'}}}

    def added(self, chat_id, by, status='member'):
        return {'update_id': 2, 'my_chat_member': {'chat': {'id': chat_id, 'type': 'supergroup', 'title': 'Team'},
                                                   'from': {'id': by, 'first_name': 'Ana'},
                                                   'new_chat_member': {'status': status}}}

    def test_owner_adding_the_bot_to_a_group_approves_it_and_it_survives_a_restart(self):
        actions = self.bot.handle(self.added(-100, self.OWNER))
        self.assertEqual(actions[0][:2], ('send', -100))
        self.assertTrue(actions[0][2].endswith('STATUS'))  # a new chat starts with the current status
        self.assertEqual(self.bot.chats(), [self.OWNER, -100])
        again = monitor.Bot([self.OWNER], path=self.path)
        self.assertEqual(again.chats(), [self.OWNER, -100])
        self.assertEqual(self.bot.handle(self.message('/status@noet_alerts_bot', -100, 'supergroup', sender=42)),
                         [('send', -100, 'STATUS')])
        self.assertEqual(self.bot.handle(self.message('/status@other_bot', -100, 'supergroup', sender=42)), [])
        # Removing the bot from the group stops the alerts there.
        self.assertEqual(self.bot.handle(self.added(-100, 42, 'left'))[0][1], self.OWNER)
        self.assertEqual(monitor.Bot([self.OWNER], path=self.path).chats(), [self.OWNER])

    def test_a_stranger_waits_for_the_owner_and_gets_no_status_meanwhile(self):
        actions = self.bot.handle(self.added(-200, 42))
        self.assertEqual([a[1] for a in actions], [-200, self.OWNER])
        self.assertIn('/approve -200', actions[1][2])
        self.assertEqual(self.bot.handle(self.added(-200, 42)), [])  # asked once, not again
        self.assertNotIn(-200, self.bot.chats())
        self.assertIn('not approved', self.bot.handle(self.message('/status', -200, 'supergroup', sender=42))[0][2])
        # Only an admin's /approve counts.
        self.assertEqual(self.bot.handle(self.message('/approve -200', 42)), [])
        self.assertNotIn(-200, self.bot.chats())
        actions = self.bot.handle(self.message('/approve -200', self.OWNER))
        self.assertIn(('send', -200), [a[:2] for a in actions])
        self.assertIn(-200, self.bot.chats())

    def test_private_start_deny_and_remove(self):
        actions = self.bot.handle(self.message('/start', 77))
        self.assertEqual([a[1] for a in actions], [77, self.OWNER])
        self.assertIn('Ana (@ana)', actions[1][2])
        self.assertIn('Refused', self.bot.handle(self.message('/deny 77', self.OWNER))[0][2])
        self.assertNotIn(77, self.bot.chats())
        self.bot.handle(self.message('/start', 77))
        self.bot.handle(self.message('/approve 77', self.OWNER))
        self.assertEqual(self.bot.handle(self.message('/status', 77)), [('send', 77, 'STATUS')])
        self.assertIn('77  a private chat with Ana', self.bot.handle(self.message('/chats', self.OWNER))[0][2])
        self.assertIn('off', self.bot.handle(self.message('/stop', 77))[0][2])
        self.assertEqual(self.bot.chats(), [self.OWNER])
        self.assertIn('always', self.bot.handle(self.message('/stop', self.OWNER))[0][2])
        # A group member cannot switch the group off; a group can be removed by the owner.
        self.bot.approve(-300, '"Team"', self.OWNER)
        self.assertIn('Only the owner', self.bot.handle(self.message('/stop', -300, 'supergroup', sender=42))[0][2])
        actions = self.bot.handle(self.message('/remove -300', self.OWNER))
        self.assertIn(('leave', -300), actions)
        self.assertEqual(self.bot.chats(), [self.OWNER])

    def test_labelled_alerts_say_down_and_back_up(self):
        watch = monitor.Watch('mainnet', label='NOET mainnet is down', up='back up')
        watch.update(['no block'])
        self.assertEqual(watch.update(['no block']), '\U0001F534 NOET mainnet is down: no block')
        self.assertEqual(watch.update([]), 'back up')
        credit = monitor.Watch('GPU credit', icon=monitor.WARN, label='NOET Runpod credit is low')
        credit.update(['x'])
        self.assertTrue(credit.update(['x']).startswith('\u26A0\uFE0F NOET Runpod credit is low: '))

    def test_ordinary_messages_are_ignored_and_groups_can_move(self):
        self.assertEqual(self.bot.handle(self.message('hello', 77)), [])
        self.assertEqual(self.bot.handle({'update_id': 3}), [])
        self.bot.approve(-400, '"Team"', self.OWNER)
        self.bot.handle({'update_id': 4, 'message': {'chat': {'id': -400, 'type': 'group', 'title': 'Team'},
                                                     'migrate_to_chat_id': -1004}})
        self.assertEqual(self.bot.chats(), [self.OWNER, -1004])

    def test_a_chat_that_blocked_the_bot_is_forgotten(self):
        self.bot.approve(77, 'Ana', self.OWNER)
        error = monitor.urllib.error.HTTPError('u', 403, 'Forbidden', {}, io.BytesIO(
            b'{"ok": false, "description": "Forbidden: bot was blocked by the user"}'))
        with mock.patch.object(monitor, 'telegram', mock.Mock(side_effect=error)):
            self.assertFalse(monitor.deliver('t', 77, 'x', self.bot))
        self.assertEqual(self.bot.chats(), [self.OWNER])

    def test_status_text(self):
        health = {'height': 3690, 'lastBlockAgeSeconds': 708, 'lastVerdictAgeSeconds': 221, 'relayCertificateDaysLeft': 13}
        info = {'awaitingVerdict': {'height': 3691, 'seconds': 308}, 'served': {'accepted': 27, 'submitted': 30}}
        account = {'clientBalance': 95.0, 'currentSpendPerHr': 2.38, 'pods': [{'id': 'p1', 'desiredStatus': 'RUNNING'}]}
        text = monitor.status_text(health, info, account, 'p1', [], now=0)
        self.assertIn('Height 3,690, last block 12 min ago', text)
        self.assertIn('replaying block 3691 (5 min so far); last verdict 4 min ago', text)
        self.assertIn('GPU verifier pod: running; credit lasts about 40 h at $2.38/h', text)
        self.assertIn('Open alerts: none', text)
        text = monitor.status_text(None, None, None, 'p1', ['verifier: down'], now=0)
        self.assertIn('unreachable', text)
        self.assertIn('Open alerts: verifier: down', text)


class MorningTests(unittest.TestCase):
    def test_dutch_time_follows_summer_time(self):
        # 2026: CEST from 29 March to 25 October, both 01:00 UTC.
        self.assertEqual((monitor.last_sunday(2026, 3), monitor.last_sunday(2026, 10)), (29, 25))
        winter = calendar.timegm((2026, 11, 11, 7, 0, 0))
        summer = calendar.timegm((2026, 7, 1, 6, 0, 0))
        self.assertEqual(monitor.dutch(winter).tm_hour, 8)
        self.assertEqual(monitor.dutch(summer).tm_hour, 8)
        self.assertEqual(monitor.dutch(calendar.timegm((2026, 10, 25, 0, 59, 0))).tm_hour, 2)
        self.assertEqual(monitor.dutch(calendar.timegm((2026, 10, 25, 1, 0, 0))).tm_hour, 2)
        self.assertEqual(monitor.dutch(calendar.timegm((2026, 3, 29, 1, 0, 0))).tm_hour, 3)

    def test_once_a_morning_and_it_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'morning.json')
            morning = monitor.Morning(path)
            seven = calendar.timegm((2026, 11, 11, 6, 59, 0))   # 07:59 CET
            eight = calendar.timegm((2026, 11, 11, 7, 0, 0))    # 08:00 CET
            self.assertFalse(morning.due(seven))
            self.assertTrue(morning.due(eight))
            morning.keep(eight, {'a': 1}, sent=True)
            again = monitor.Morning(path)
            self.assertFalse(again.due(eight + 120))
            self.assertEqual(again.state['snapshot'], {'at': eight, 'producers': {'a': 1}})
            # Missed by a restart past 09:00: skipped, not posted late.
            self.assertFalse(again.due(eight + 86400 + 3600))
            self.assertTrue(again.due(eight + 86400))
            # A failed producers fetch keeps the old snapshot.
            again.keep(eight + 86400, None, sent=True)
            self.assertEqual(again.state['snapshot']['producers'], {'a': 1})

    def test_the_summary_counts_ours_and_outside_since_the_last_one(self):
        now = calendar.timegm((2026, 11, 11, 7, 0, 0))
        health = {'height': 3870, 'lastVerdictAgeSeconds': 200}
        snapshot = {'at': now - 86400, 'producers': {'ours1': 10, 'ours2': 5, 'them': 100}}
        producers = {'ours1': 30, 'ours2': 5, 'them': 200, 'new': 4}
        account = {'clientBalance': 60.0, 'currentSpendPerHr': 2.5, 'pods': []}
        text = monitor.morning_text(now, health, [], producers, snapshot, {'ours1', 'ours2'}, account, [])
        self.assertEqual(text.splitlines(), [
            '\u2600\uFE0F NOET morning update, Wed 11 Nov',
            '\U0001F7E2 Mainnet healthy, height 3,870',
            '\u26CF\uFE0F Blocks in the last 24 h: 124, ours 20 (16%), outside 104',
            '\U0001F50D Verifier: last verdict 3 min ago',
            '\U0001F4B3 Runpod credit: about 24 h left ($2.50/h)',
            'Open alerts: none'])
        self.assertNotIn('ours1', text)

    def test_a_short_window_says_since_when_and_problems_show(self):
        now = calendar.timegm((2026, 11, 11, 7, 0, 0))
        snapshot = {'at': calendar.timegm((2026, 11, 10, 21, 15, 0)), 'producers': {'x': 1}}
        text = monitor.morning_text(now, {'height': 5}, ['no block for 2.0 h'], {'x': 3}, snapshot, set(), None,
                                    ['GPU pod: pod exited'])
        self.assertIn('\U0001F534 Mainnet: no block for 2.0 h', text)
        self.assertIn('Blocks since Tue 22:15: 2, ours 0 (0%), outside 2', text)
        self.assertIn('Open alerts: GPU pod: pod exited', text)
        self.assertNotIn('Runpod', text)
        self.assertNotIn('Blocks', monitor.morning_text(now, None, [], None, snapshot, set(), None, []))
        self.assertIn('health endpoint unreachable', monitor.morning_text(now, None, [], None, None, set(), None, []))


if __name__ == '__main__':
    unittest.main()
