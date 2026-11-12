#!/usr/bin/env python3
"""Watch the hosted NOET networks and alert by Telegram and email.

Polls each network's public `/healthz/chain` (height, last block and verdict
age, relay certificate days) and alerts when a chain stops producing blocks,
its index goes stale, its certificates near expiry or it cannot be reached.
With a Runpod API key it also watches the verifier's GPU pod: it alerts when
the pod is not running or the account's credit will run out within a week.
It only alerts; it never stops or deletes anything. A problem must persist for
two checks before it alerts; while it lasts, a reminder follows every few
hours (daily for credit), and recovery is announced once.

The Telegram bot also answers: /status (chain, verifier, GPU pod now) in any
approved chat. The owner adds it to a group to approve that group; anyone else
who adds it or sends /start waits for the owner's /approve. Reorganisation
alerts go to the owner's chats only. Every morning at 08:00 Dutch time (CET,
CEST in summer) every approved chat gets a short summary: health, blocks since
the last summary (ours and outside), the verifier, GPU credit, open alerts.

Configuration (environment; secrets as Fly secrets):
  NOET_MONITOR_TARGETS   name=url,... (default: mainnet=https://noet-mainnet.fly.dev)
  NOET_MONITOR_STALL     seconds without a block before alerting (default 5400:
                         at 600-second blocks a quiet gap that long happens
                         about once in two months)
  NOET_MONITOR_INTERVAL  seconds between checks (default 120)
  NOET_MONITOR_GATEWAY   mainnet's mining gateway (https://...:9443): alert when a
                         block has waited NOET_MONITOR_VERDICT seconds (default
                         900) for its Full verdict, i.e. the verifier is down
  NOET_MONITOR_POD       Runpod pod ID of the mainnet verifier (with RUNPOD_API_KEY;
                         a read-only key is enough)
  NOET_MONITOR_BLOCKS    a public blocks list (default https://www.noet.network/api/chain/blocks):
                         alert when a block the monitor has seen is replaced (a reorganisation)
  NOET_MONITOR_SWITCH_HEIGHT  mainnet's model switch height (mainnet/network.json):
                         a daily reminder in the three days before it, until
  NOET_MONITOR_SWITCH_READY=1 says a verifier with the new model serves first
  TELEGRAM_BOT_TOKEN, RUNPOD_API_KEY
  TELEGRAM_CHAT_ID       the owner's chats (comma-separated): always alerted; their
                         users (with TELEGRAM_ADMIN_IDS) approve other chats
  NOET_MONITOR_CHATS     file that keeps the approved chats (default /data/chats.json,
                         a Fly volume; without it they last until a restart). The
                         morning summary keeps its state beside it (morning.json)
  NOET_MONITOR_PRODUCERS blocks per reward address (default
                         https://www.noet.network/api/chain/producers)
  NOET_MONITOR_OURS      our reward addresses (comma-separated, a secret): the
                         morning summary counts their blocks as ours
  SMTP_HOST, SMTP_PORT (465 = TLS, else STARTTLS), SMTP_USER, SMTP_PASSWORD,
  ALERT_EMAIL_FROM, ALERT_EMAIL_TO
"""
from email.message import EmailMessage
import calendar
import json
import os
import smtplib
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

CERT_DAYS = 14
REMIND_SECONDS = 3 * 3600
CREDIT_DAYS = 7
SWITCH_DAYS = 3
BLOCKS_PER_DAY = 144
RUNPOD_API = 'https://api.runpod.io/graphql'
VERDICT_SECONDS = 900
MORNING_HOUR = 8  # Dutch time
WARN = '\u26A0\uFE0F'


def problems(health, stall_seconds):
    """Problems worth waking someone for, from one /healthz/chain reply (None = unreachable)."""
    if health is None:
        return ['health endpoint unreachable']
    found = []
    age = health.get('lastBlockAgeSeconds')
    if age is None or age > stall_seconds:
        # No running count in the text: a text that changes every check is
        # an alert every check (7 Nov: one every two minutes for two days).
        found.append(f'no block for over {stall_seconds // 60} min (height {health.get("height")})')
    if 'index stale' in (health.get('problems') or []):
        found.append('explorer index is stale')
    days = health.get('relayCertificateDaysLeft')
    if days is not None and days < CERT_DAYS:
        urgent = 'URGENT: ' if days <= 3 else ''
        found.append(f'{urgent}relay certificates expire in {days} days; rotate with deploy/rotate.py --apply')
    return found


def verdict_problems(info, limit=VERDICT_SECONDS, health=None, open_alert=None):
    """A block accepted by the gateway that has waited too long for its verdict,
    from one /mining/info reply (None = unreachable).

    The gateway stops naming the block after an hour, which is not a verdict: while
    an alert is open (`open_alert`, its text) it stays open until the chain's
    /healthz shows a verdict newer than `limit` (12 Nov: a false "back" an hour
    into a halt)."""
    if info is None:
        return ['mining gateway unreachable']
    waiting = info.get('awaitingVerdict') or {}
    seconds = waiting.get('seconds')
    if not isinstance(seconds, int) or seconds < limit:
        if open_alert and not waiting and health is not None:
            age = health.get('lastVerdictAgeSeconds')
            if not isinstance(age, (int, float)) or age >= limit:
                return list(open_alert)
        return []
    return [f'block {waiting.get("height")} has waited over {limit // 60} min for its Full verdict; '
            'the GPU verifier is probably down']


def pod_problems(account, pod_id):
    """Problems with the GPU pod, from one Runpod reply (None = unreachable)."""
    if account is None:
        return ['Runpod API unreachable']
    pods = {pod['id']: pod['desiredStatus'] for pod in account['pods']}
    if pods.get(pod_id) != 'RUNNING':
        return [f'pod {pod_id} is {pods.get(pod_id, "gone").lower()}; the verifier and miner are down']
    return []


# Hours of credit left at which the warning changes, and with it an alert goes out.
CREDIT_STEPS_HOURS = (24, 12, 6, 3, 1)


def credit_problems(account, days=CREDIT_DAYS):
    """A warning while the Runpod credit lasts less than `days` at the current spend:
    daily while days are left, then at 24, 12, 6, 3 and 1 hours."""
    if account is None or account['currentSpendPerHr'] <= 0:
        return []
    hours = account['clientBalance'] / account['currentSpendPerHr']
    if hours >= days * 24:
        return []
    # Only the rate, not the balance: the text must stay the same between steps.
    spend = f'at ${account["currentSpendPerHr"]:.3f}/h'
    if hours >= 48:
        # Whole days, so the text (and with it an alert) changes once a day.
        return [f'Runpod credit runs out in about {int(hours // 24)} days ({spend}); add credit or the pod stops']
    under = min((step for step in CREDIT_STEPS_HOURS if hours < step), default=None)
    if under is None:
        return [f'Runpod credit runs out within 2 days ({spend}); add credit or the pod stops']
    return [f'URGENT: Runpod credit runs out in under {under} hour{"s" if under > 1 else ""} ({spend}); '
            'when it does the pod stops, its disk (verifier and models) is wiped and no block becomes final']


def switch_problems(health, switch_height, ready):
    """A reminder in the days before the model switch, until the operator confirms
    that a verifier with the new model is the first relay endpoint."""
    if ready or not switch_height or not health or health.get('height') is None:
        return []
    left = switch_height - health['height']
    if not 0 < left <= SWITCH_DAYS * BLOCKS_PER_DAY:
        return []
    # Whole days at the 600-second target, so the text (and with it an alert) changes once a day.
    days = left // BLOCKS_PER_DAY
    when = f'in about {days} day{"s" if days > 1 else ""}' if days else 'within a day'
    return [f'the switch to Qwen3-14B at block {switch_height:,} comes {when}; no block after it is final '
            'unless a verifier with the 14B snapshot is first in NOET_VERIFIER_ENDPOINT '
            '(then set NOET_MONITOR_SWITCH_READY=1)']


class Reorgs:
    """Remembers the hash at each recent height and reports when one changes."""

    def __init__(self, keep=200):
        self.keep, self.seen = keep, {}

    def update(self, blocks):
        """Problems from one blocks list (newest first); None = unreachable, never an alert."""
        if not blocks:
            return []
        found = []
        tip = max(b['height'] for b in blocks)
        replaced = [b['height'] for b in blocks if self.seen.get(b['height'], b['hash']) != b['hash']]
        if replaced:
            depth = tip - min(replaced) + 1
            found.append(f'reorganisation: {len(replaced)} block{"s" if len(replaced) > 1 else ""} replaced '
                         f'from height {min(replaced):,} (depth {depth}); check the explorer and the verifier')
        for b in blocks:
            self.seen[b['height']] = b['hash']
        for height in [h for h in self.seen if h < tip - self.keep]:
            del self.seen[height]
        return found


class Watch:
    """Per-target alert state: alert after two failing checks, remind, announce recovery.

    `label` heads the alert (default "NOET <name>"), `icon` marks it (red for an
    outage, a warning sign for something to act on soon), `up` is the recovery text.
    """

    def __init__(self, name, clock=time.time, remind=REMIND_SECONDS, label=None, icon='\U0001F534', up=None):
        self.name, self.clock, self.remind = name, clock, remind
        self.label, self.icon = label or f'NOET {name}', icon
        self.up = up or f'\U0001F7E2 NOET {name}: recovered.'
        self.failing, self.alerted_at, self.last = 0, None, []

    def update(self, found):
        now = self.clock()
        if not found:
            self.failing, self.last = 0, []
            if self.alerted_at is not None:
                self.alerted_at = None
                return self.up
            return None
        self.failing += 1
        if self.failing < 2:
            return None
        changed = found != self.last
        self.last = found
        if self.alerted_at is None or changed or now - self.alerted_at >= self.remind:
            first = self.alerted_at is None
            self.alerted_at = now
            return f'{self.icon} ' + ('' if first else 'Still: ') + f'{self.label}: ' + '; '.join(found)
        return None


def fetch(url, timeout=20, path='/healthz/chain'):
    request = urllib.request.Request(url.rstrip('/') + path, headers={'User-Agent': 'noet-monitor'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(65536))
    except urllib.error.HTTPError as error:  # 503 still carries the health body
        try:
            return json.loads(error.read(65536))
        except ValueError:
            return None
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError):
        return None


def runpod(key, timeout=20):
    """Account balance, spend per hour and pods (read-only query), or None."""
    query = {'query': 'query { myself { clientBalance currentSpendPerHr pods { id desiredStatus } } }'}
    request = urllib.request.Request(RUNPOD_API, data=json.dumps(query).encode(),
                                     headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                                              'User-Agent': 'noet-monitor'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            account = json.loads(response.read(1 << 20))['data']['myself']
        return {'clientBalance': float(account['clientBalance']),
                'currentSpendPerHr': float(account['currentSpendPerHr']),
                'pods': [{'id': pod['id'], 'desiredStatus': pod['desiredStatus']} for pod in account['pods']]}
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, KeyError, TypeError):
        return None


def fetch_producers(url, timeout=20):
    """Blocks per reward address ({address: blocks}) from the public producers list, or None."""
    request = urllib.request.Request(url, headers={'User-Agent': 'noet-monitor'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            rows = json.loads(response.read(1 << 22))['producers']
        return {str(r['address']): int(r['blocks']) for r in rows}
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, KeyError, TypeError):
        return None


def last_sunday(year, month):
    last = calendar.monthrange(year, month)[1]
    return last - (calendar.weekday(year, month, last) + 1) % 7


def dutch(t):
    """Local time in the Netherlands: CEST from the last Sunday of March to the
    last Sunday of October (01:00 UTC both), CET otherwise."""
    year = time.gmtime(t).tm_year
    start = calendar.timegm((year, 3, last_sunday(year, 3), 1, 0, 0))
    end = calendar.timegm((year, 10, last_sunday(year, 10), 1, 0, 0))
    return time.gmtime(t + (7200 if start <= t < end else 3600))


class Morning:
    """When the morning summary is due, and the producer counts it compares against."""
    def __init__(self, path=None, hour=MORNING_HOUR):
        self.path, self.hour, self.state = path, hour, {}
        try:
            with open(path) as stream:
                self.state = json.load(stream)
        except (OSError, TypeError, ValueError):
            pass

    def due(self, now):
        """Once a day within the hour after `hour`; a summary missed by a restart is skipped."""
        local = dutch(now)
        return local.tm_hour == self.hour and self.state.get('sent') != time.strftime('%Y-%m-%d', local)

    def keep(self, now, producers, sent=False):
        if producers is not None:
            self.state['snapshot'] = {'at': now, 'producers': producers}
        if sent:
            self.state['sent'] = time.strftime('%Y-%m-%d', dutch(now))
        if self.path:
            try:
                with open(self.path + '.tmp', 'w') as stream:
                    json.dump(self.state, stream)
                os.replace(self.path + '.tmp', self.path)
            except OSError as error:
                log(event='morning-save-failed', type=type(error).__name__)


def morning_text(now, health, found, producers, snapshot, ours, account, alerts):
    """The short morning summary."""
    lines = [f'\u2600\uFE0F NOET morning update, {time.strftime("%a %d %b", dutch(now))}']
    height = (health or {}).get('height')
    if health is None:
        lines.append('\U0001F534 Mainnet: health endpoint unreachable')
    elif found:
        lines.append('\U0001F534 Mainnet: ' + '; '.join(found))
    else:
        lines.append('\U0001F7E2 Mainnet healthy' + (f', height {height:,}' if isinstance(height, int) else ''))
    if producers is not None and snapshot and snapshot.get('producers') is not None:
        before = snapshot['producers']
        total = max(0, sum(producers.values()) - sum(before.values()))
        mine = max(0, sum(producers.get(a, 0) - before.get(a, 0) for a in ours))
        since = snapshot.get('at', now)
        window = 'in the last 24 h' if abs(now - since - 86400) < 3600 else \
            f'since {time.strftime("%a %H:%M", dutch(since))}'
        share = f' ({round(100 * mine / total)}%)' if total else ''
        lines.append(f'\u26CF\uFE0F Blocks {window}: {total}, ours {mine}{share}, outside {max(0, total - mine)}')
    if health and health.get('lastVerdictAgeSeconds') is not None:
        lines.append(f'\U0001F50D Verifier: last verdict {ago(health["lastVerdictAgeSeconds"])}')
    if account and account.get('currentSpendPerHr', 0) > 0:
        hours = account['clientBalance'] / account['currentSpendPerHr']
        left = f'{hours / 24:.1f} days' if hours >= 48 else f'{hours:.0f} h'
        lines.append(f'\U0001F4B3 Runpod credit: about {left} left (${account["currentSpendPerHr"]:.2f}/h)')
    lines.append('Open alerts: ' + ('; '.join(alerts) if alerts else 'none'))
    return '\n'.join(lines)


def fetch_blocks(url, timeout=20):
    """The newest blocks from a public explorer list, or None."""
    request = urllib.request.Request(url, headers={'User-Agent': 'noet-monitor'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            rows = json.loads(response.read(1 << 22))
        return [{'height': int(r['height']), 'hash': str(r['hash'])} for r in rows]
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, KeyError, TypeError):
        return None


def telegram(token, method, params, timeout=20):
    """One Bot API call; raises on failure (HTTPError carries Telegram's reason)."""
    request = urllib.request.Request(f'https://api.telegram.org/bot{token}/{method}', data=json.dumps(params).encode(),
                                     headers={'Content-Type': 'application/json', 'User-Agent': 'noet-monitor'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(1 << 22))['result']


def telegram_error(error):
    """Telegram's JSON reply from a failed call, or {}."""
    try:
        return json.loads(error.read(65536))
    except (ValueError, OSError, AttributeError):
        return {}


def chat_ids(spec):
    return [int(item) for item in (spec or '').replace(' ', '').split(',') if item.lstrip('-').isdigit()]


def deliver(token, chat, text, bot=None):
    """Send one message; a chat that blocked or removed the bot is forgotten."""
    for _ in range(2):
        try:
            telegram(token, 'sendMessage', {'chat_id': chat, 'text': text, 'disable_web_page_preview': True})
            return True
        except urllib.error.HTTPError as error:
            reply = telegram_error(error)
            moved = (reply.get('parameters') or {}).get('migrate_to_chat_id')
            if moved and bot:  # a group became a supergroup and has a new id
                bot.migrate(chat, moved)
                chat = moved
                continue
            log(event='telegram-failed', chat=chat, status=error.code, reason=str(reply.get('description', ''))[:120])
            if bot and (error.code == 403 or 'chat not found' in str(reply.get('description', ''))):
                bot.forget(chat)
            return False
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, KeyError) as error:
            log(event='telegram-failed', chat=chat, type=type(error).__name__)
            return False
    return False


def send(text, env=os.environ, bot=None, owner_only=False, email=True):
    sent = []
    token = env.get('TELEGRAM_BOT_TOKEN')
    chats = (bot.owner_chats() if owner_only else bot.chats()) if bot else chat_ids(env.get('TELEGRAM_CHAT_ID'))
    if token and chats:
        delivered = [chat for chat in chats if deliver(token, chat, text, bot)]
        if delivered:
            sent.append('telegram')
    if email and env.get('SMTP_HOST') and env.get('ALERT_EMAIL_TO'):
        message = EmailMessage()
        message['Subject'] = text[:120]
        message['From'] = env.get('ALERT_EMAIL_FROM') or env.get('SMTP_USER', '')
        message['To'] = env['ALERT_EMAIL_TO']
        message.set_content(text + '\n\nStatus: https://www.noet.network/#status\n')
        port = int(env.get('SMTP_PORT', '465'))
        try:
            if port == 465:
                server = smtplib.SMTP_SSL(env['SMTP_HOST'], port, timeout=30)
            else:
                server = smtplib.SMTP(env['SMTP_HOST'], port, timeout=30)
                server.starttls()
            with server:
                if env.get('SMTP_USER'):
                    server.login(env['SMTP_USER'], env.get('SMTP_PASSWORD', ''))
                server.send_message(message)
            sent.append('email')
        except (smtplib.SMTPException, OSError) as error:
            log(event='email-failed', type=type(error).__name__)
    return sent


def log(**event):
    event['at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    print(json.dumps(event, sort_keys=True), flush=True)


def targets(spec):
    pairs = [item.split('=', 1) for item in spec.split(',') if item.strip()]
    if not pairs or any(len(p) != 2 or not p[1].startswith('https://') for p in pairs):
        raise SystemExit('NOET_MONITOR_TARGETS must be name=https://...,...')
    return [(name.strip(), url.strip()) for name, url in pairs]


def ago(seconds):
    if seconds is None:
        return 'unknown'
    seconds = int(seconds)
    if seconds < 90:
        return f'{seconds} s ago'
    if seconds < 90 * 60:
        return f'{round(seconds / 60)} min ago'
    return f'{seconds / 3600:.1f} h ago'


def status_text(health, info, account, pod, alerts, now=None):
    """The /status reply: what the monitor sees right now."""
    lines = [f'NOET mainnet, {time.strftime("%d %b %H:%M UTC", time.gmtime(now or time.time()))}']
    if health is None:
        lines.append('Node: health endpoint unreachable')
    else:
        height = health.get('height')
        lines.append(f'Height {height:,}' if isinstance(height, int) else 'Height unknown')
        lines[-1] += f', last block {ago(health.get("lastBlockAgeSeconds"))}'
    if info is None:
        lines.append('Verifier: mining gateway unreachable')
    else:
        waiting = info.get('awaitingVerdict') or {}
        verdict = f'last verdict {ago(health.get("lastVerdictAgeSeconds"))}' if health else ''
        if isinstance(waiting.get('seconds'), int):
            lines.append(f'Verifier: replaying block {waiting.get("height")} ({ago(waiting["seconds"]).replace(" ago", "")} so far)'
                         + (f'; {verdict}' if verdict else ''))
        else:
            lines.append('Verifier: idle' + (f'; {verdict}' if verdict else ''))
        served = info.get('served') or {}
        if served:
            lines.append(f'Miners: {served.get("accepted", 0)} blocks accepted of {served.get("submitted", 0)} '
                         f'submitted since the gateway started')
    if pod and account is not None:
        state = {p['id']: p['desiredStatus'] for p in account['pods']}.get(pod, 'gone').lower()
        line = f'GPU verifier pod: {state}'
        if account['currentSpendPerHr'] > 0:
            hours = account['clientBalance'] / account['currentSpendPerHr']
            left = f'{hours / 24:.1f} days' if hours >= 48 else f'{hours:.0f} h'
            line += f'; credit lasts about {left} at ${account["currentSpendPerHr"]:.2f}/h'
        lines.append(line)
    elif pod:
        lines.append('GPU verifier pod: Runpod API unreachable')
    if health and health.get('relayCertificateDaysLeft') is not None:
        lines.append(f'Relay certificates: {health["relayCertificateDaysLeft"]} days left')
    lines.append('Open alerts: ' + ('; '.join(alerts) if alerts else 'none'))
    lines.append('Explorer: https://www.noet.network/explorer')
    return '\n'.join(lines)


HELP = ('NOET monitor\n'
        '/status  chain, verifier and GPU pod right now\n'
        '/start   alerts in this chat (the owner approves it)\n'
        '/stop    no more alerts in this chat')
ADMIN_HELP = HELP + '\nOwner: /chats, /approve <id>, /deny <id>, /remove <id>'
GROUPS = ('group', 'supergroup', 'channel')


class Bot:
    """Which chats get alerts, and the bot's commands.

    The owner's chats (TELEGRAM_CHAT_ID) always get alerts and their users are
    admins. Another chat gets alerts once an admin approves it: a group an admin
    adds the bot to is approved at once; a group someone else adds it to, or a
    /start from anyone else, waits for an admin's /approve. Approved chats are
    kept in a file so a restart keeps them. `handle` returns what to do; it
    sends nothing itself.
    """

    MAX_PENDING = 20

    def __init__(self, owner, admins=(), path=None, username='', status=lambda: ''):
        self.owner, self.admins = list(owner), set(admins) | {c for c in owner if c > 0}
        self.path, self.username, self.status = path, username.lower(), status
        self.lock = threading.Lock()
        self.approved, self.pending = {}, {}
        if path and os.path.exists(path):
            try:
                with open(path) as f:
                    self.approved = {int(k): v for k, v in json.load(f)['chats'].items()}
            except (OSError, ValueError, KeyError, TypeError):
                log(event='chats-unreadable', path=path)

    def welcome(self):
        return ('NOET alerts are on in this chat: mainnet down and back up, the GPU verifier pod, '
                'and low Runpod credit. /status shows the chain any time.\n\n' + self.status())

    def owner_chats(self):
        return list(self.owner)

    def chats(self):
        with self.lock:
            return self.owner + [c for c in self.approved if c not in self.owner]

    def save(self):
        if not self.path or not os.path.isdir(os.path.dirname(self.path) or '.'):
            return
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({'chats': {str(k): v for k, v in self.approved.items()}}, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    def approve(self, chat, title, by):
        with self.lock:
            self.pending.pop(chat, None)
            self.approved[chat] = {'title': title, 'by': by, 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
            self.save()
        log(event='chat-approved', chat=chat, title=title, by=by)

    def forget(self, chat):
        with self.lock:
            self.pending.pop(chat, None)
            gone = self.approved.pop(chat, None)
            if gone:
                self.save()
        if gone:
            log(event='chat-removed', chat=chat, title=gone.get('title'))
        return gone

    def migrate(self, old, new):
        with self.lock:
            if old in self.approved:
                self.approved[new] = self.approved.pop(old)
                self.save()
        log(event='chat-migrated', chat=old, to=new)

    def allowed(self, chat):
        with self.lock:
            return chat in self.owner or chat in self.approved

    def _ask(self, chat, title, who):
        """Ask the admins about a chat once; returns the messages to send."""
        with self.lock:
            if chat in self.pending or len(self.pending) >= self.MAX_PENDING:
                return []
            self.pending[chat] = title
        log(event='chat-pending', chat=chat, title=title, who=who)
        ask = f'{who} wants NOET alerts in {title} (id {chat}).\n/approve {chat} to allow, /deny {chat} to refuse.'
        return [('send', chat, 'Asked the owner to approve this chat. Alerts and /status start once they do.')] + \
            [('send', admin, ask) for admin in self.owner]

    def handle(self, update):
        """Actions for one Telegram update: ('send', chat, text) or ('leave', chat)."""
        if 'my_chat_member' in update:
            return self._membership(update['my_chat_member'])
        message = update.get('message') or update.get('channel_post')
        if not message:
            return []
        chat = message['chat']
        if message.get('migrate_to_chat_id'):
            self.migrate(chat['id'], message['migrate_to_chat_id'])
            return []
        text = (message.get('text') or '').strip()
        if not text.startswith('/'):
            return []
        word, _, rest = text.partition(' ')
        command, _, target = word[1:].partition('@')
        if target and target.lower() != self.username:
            return []  # meant for another bot in the group
        sender = (message.get('from') or {}).get('id')
        return self._command(command.lower(), rest.strip(), chat, sender, describe(message.get('from'), chat))

    def _membership(self, change):
        chat, status = change['chat'], change['new_chat_member']['status']
        by = (change.get('from') or {}).get('id')
        title = describe(None, chat)
        if status in ('left', 'kicked'):
            gone = self.forget(chat['id'])
            return [('send', a, f'Removed from {title}; no more alerts there.') for a in self.owner] if gone else []
        if status not in ('member', 'administrator') or chat['type'] not in GROUPS or self.allowed(chat['id']):
            return []
        if by in self.admins:
            self.approve(chat['id'], title, by)
            return [('send', chat['id'], self.welcome())]
        return self._ask(chat['id'], title, describe(change.get('from'), None))

    def _command(self, command, arg, chat, sender, who):
        here, admin = chat['id'], sender in self.admins
        reply = lambda text: [('send', here, text)]
        if command in ('start', 'subscribe'):
            if self.allowed(here):
                return reply('Alerts are on in this chat. /status shows the chain any time.')
            if admin:
                self.approve(here, describe(None, chat), sender)
                return reply(self.welcome())
            return self._ask(here, describe(None, chat), who)
        if command == 'status':
            if self.allowed(here) or admin:
                return reply(self.status())
            return reply('This chat is not approved yet. Send /start to ask the owner.')
        if command in ('stop', 'unsubscribe'):
            if here in self.owner:
                return reply('This is the owner chat (TELEGRAM_CHAT_ID); it always gets alerts.')
            if chat['type'] in GROUPS and not admin:
                return reply('Only the owner can stop alerts in a group, or remove the bot from it.')
            return reply('Alerts are off in this chat.') if self.forget(here) else reply('Alerts were not on here.')
        if command == 'help':
            return reply(ADMIN_HELP if admin else HELP)
        if not admin or command not in ('approve', 'deny', 'remove', 'chats'):
            return []
        if command == 'chats':
            with self.lock:
                rows = [f'{c}  {v.get("title")}' for c, v in self.approved.items()]
                waiting = [f'{c}  {t} (waiting)' for c, t in self.pending.items()]
            return reply('Chats with alerts besides yours:\n' + '\n'.join(rows + waiting) if rows or waiting
                         else 'Only your chat gets alerts.')
        target = int(arg) if arg.lstrip('-').isdigit() else None
        if target is None:
            return reply(f'Usage: /{command} <chat id>  (see /chats)')
        if command == 'approve':
            with self.lock:
                title = self.pending.get(target) or str(target)
            self.approve(target, title, sender)
            return reply(f'Approved {title}.') + [('send', target, self.welcome())]
        if command == 'deny':
            with self.lock:
                title = self.pending.pop(target, None)
            if title is None:
                return reply(f'Nothing waiting from {target}.')
            log(event='chat-denied', chat=target, title=title)
            return reply(f'Refused {title}.') + ([('leave', target)] if target < 0 else [])
        gone = self.forget(target)
        if not gone:
            return reply(f'{target} was not getting alerts.')
        return reply(f'Removed {gone.get("title")}.') + ([('leave', target)] if target < 0 else [])


def describe(user, chat):
    """A short human name for a user or a chat."""
    if user:
        name = ' '.join(x for x in (user.get('first_name'), user.get('last_name')) if x) or 'someone'
        return name + (f' (@{user["username"]})' if user.get('username') else '')
    if chat.get('type') in GROUPS:
        return f'"{chat.get("title", chat["id"])}"'
    name = ' '.join(x for x in (chat.get('first_name'), chat.get('last_name')) if x) or str(chat['id'])
    return f'a private chat with {name}' + (f' (@{chat["username"]})' if chat.get('username') else '')


def listen(token, bot):
    """Answer Telegram commands until the process ends (long polling)."""
    offset = None
    while True:
        params = {'timeout': 50, 'allowed_updates': ['message', 'channel_post', 'my_chat_member']}
        if offset is not None:
            params['offset'] = offset
        try:
            updates = telegram(token, 'getUpdates', params, timeout=70)
        except urllib.error.HTTPError as error:
            log(event='telegram-poll-failed', status=error.code, reason=str(telegram_error(error).get('description', ''))[:120])
            time.sleep(30)
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, KeyError) as error:
            log(event='telegram-poll-failed', type=type(error).__name__)
            time.sleep(10)
            continue
        for update in updates:
            offset = update['update_id'] + 1
            try:
                actions = bot.handle(update)
            except Exception as error:  # one odd update must not stop the bot
                log(event='telegram-update-failed', type=type(error).__name__)
                continue
            for action in actions:
                if action[0] == 'send':
                    deliver(token, action[1], action[2], bot)
                elif action[0] == 'leave':
                    try:
                        telegram(token, 'leaveChat', {'chat_id': action[1]})
                    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, KeyError):
                        pass


def drop_root(path, uid=10001):
    """Started as root (a Fly volume mounts root-owned): hand the data directory to
    the monitor user, then run as that user."""
    if not hasattr(os, 'getuid') or os.getuid() != 0:
        return
    folder = os.path.dirname(path or '')
    if folder and os.path.isdir(folder):
        for item in [folder] + [os.path.join(folder, n) for n in os.listdir(folder)]:
            os.chown(item, uid, uid)
    os.setgroups([])
    os.setgid(uid)
    os.setuid(uid)


def main():
    chats_path = os.environ.get('NOET_MONITOR_CHATS', '/data/chats.json')
    drop_root(chats_path)
    watched = targets(os.environ.get('NOET_MONITOR_TARGETS', 'mainnet=https://noet-mainnet.fly.dev'))
    stall = int(os.environ.get('NOET_MONITOR_STALL', '5400'))
    interval = int(os.environ.get('NOET_MONITOR_INTERVAL', '120'))
    watches = {name: Watch(name, label=f'NOET {name} is down', up=f'\U0001F7E2 NOET {name} is back up: blocks are coming in again.')
               for name, _ in watched}
    # Certificate expiry is the owner's job (deploy/rotate.py), not an outage.
    cert_watches = {name: Watch(f'{name} certificates', icon=WARN, remind=86400) for name, _ in watched}
    switch_height = int(os.environ.get('NOET_MONITOR_SWITCH_HEIGHT') or 0)
    switch_ready = os.environ.get('NOET_MONITOR_SWITCH_READY') == '1'
    switch_watch = Watch('model switch', remind=86400, icon=WARN)
    gateway = os.environ.get('NOET_MONITOR_GATEWAY')
    verdict_limit = int(os.environ.get('NOET_MONITOR_VERDICT') or VERDICT_SECONDS)
    verdict_watch = Watch('verifier', label='NOET mainnet is stalled',
                          up='\U0001F7E2 NOET verifier is back: blocks are being finalised again.')
    key, pod = os.environ.get('RUNPOD_API_KEY'), os.environ.get('NOET_MONITOR_POD')
    blocks_url = os.environ.get('NOET_MONITOR_BLOCKS', 'https://www.noet.network/api/chain/blocks')
    reorgs, reorg_watch = Reorgs(), Watch('chain', remind=86400, icon=WARN)
    producers_url = os.environ.get('NOET_MONITOR_PRODUCERS', 'https://www.noet.network/api/chain/producers')
    ours = {a for a in (os.environ.get('NOET_MONITOR_OURS') or '').replace(' ', '').split(',') if a}
    morning = Morning(os.path.join(os.path.dirname(chats_path), 'morning.json'))
    if not morning.state.get('snapshot'):
        morning.keep(time.time(), fetch_producers(producers_url))
    names = [name for name, _ in watched] + (['verdicts'] if gateway else []) + ['reorganisations']
    if key and pod:
        pod_watch = Watch('GPU pod', label='NOET GPU verifier pod is down', up='\U0001F7E2 NOET GPU verifier pod is running again.')
        credit_watch = Watch('GPU credit', remind=86400, icon=WARN, label='NOET Runpod credit is low',
                             up='\U0001F7E2 NOET Runpod credit is topped up.')
        names.append(f'GPU pod {pod}')
        reported = False
    all_watches = list(watches.values()) + list(cert_watches.values()) + [switch_watch, verdict_watch, reorg_watch] + \
        ([pod_watch, credit_watch] if key and pod else [])

    def status():
        mainnet = dict(watched).get('mainnet') or watched[0][1]
        alerts = [f'{w.name}: {"; ".join(w.last)}' for w in all_watches if w.alerted_at is not None and w.last]
        return status_text(fetch(mainnet), fetch(gateway, path='/mining/info') if gateway else None,
                           runpod(key) if key and pod else None, pod, alerts)

    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    bot = Bot(chat_ids(os.environ.get('TELEGRAM_CHAT_ID')), chat_ids(os.environ.get('TELEGRAM_ADMIN_IDS')),
              chats_path, status=status)
    if token:
        try:
            bot.username = telegram(token, 'getMe', {}).get('username', '').lower()
            telegram(token, 'setMyCommands', {'commands': [
                {'command': 'status', 'description': 'Chain, verifier and GPU pod right now'},
                {'command': 'start', 'description': 'Alerts in this chat (the owner approves it)'},
                {'command': 'stop', 'description': 'No more alerts in this chat'}]})
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, KeyError) as error:
            log(event='telegram-setup-failed', type=type(error).__name__)
        threading.Thread(target=listen, args=(token, bot), daemon=True).start()
    log(event='monitor-started', targets=names, chats=len(bot.chats()), kept=os.path.isdir(os.path.dirname(chats_path)),
        channels=send('NOET monitor started: watching ' + ', '.join(names), bot=bot, owner_only=True))
    while True:
        health = {name: fetch(url) for name, url in watched}
        checks = []
        for name, _ in watched:
            found = problems(health[name], stall)
            checks.append((watches[name], [f for f in found if 'certificates' not in f]))
            checks.append((cert_watches[name], [f for f in found if 'certificates' in f]))
        checks.append((switch_watch, switch_problems(health.get('mainnet'), switch_height, switch_ready)))
        # A reorganisation is reported once: the next list shows the new chain as seen.
        reorg_watch.failing = 1
        checks.append((reorg_watch, reorgs.update(fetch_blocks(blocks_url))))
        if gateway:
            open_alert = verdict_watch.last if verdict_watch.alerted_at is not None else None
            checks.append((verdict_watch, verdict_problems(fetch(gateway, path='/mining/info'), verdict_limit,
                                                           health.get('mainnet'), open_alert)))
        if key and pod:
            account = runpod(key)
            if account and not reported:
                reported = True
                log(event='gpu-account', pod=pod, status={p['id']: p['desiredStatus'] for p in account['pods']}.get(pod),
                    balance=round(account['clientBalance'], 2), spendPerHr=account['currentSpendPerHr'])
            checks += [(pod_watch, pod_problems(account, pod)), (credit_watch, credit_problems(account))]
        now = time.time()
        if token and morning.due(now):
            # Shared alerts only: certificates, the model switch and reorganisations stay with the owner.
            private = [reorg_watch, switch_watch, *cert_watches.values()]
            alerts = [f'{w.name}: {"; ".join(w.last)}' for w in all_watches
                      if w.alerted_at is not None and w.last and w not in private]
            mainnet = health.get('mainnet')
            found = [f for f in problems(mainnet, stall) if 'certificates' not in f] if mainnet else []
            producers = fetch_producers(producers_url)
            text = morning_text(now, mainnet, found, producers, morning.state.get('snapshot'), ours,
                                account if key and pod else None, alerts)
            log(event='morning', channels=send(text, bot=bot, email=False), ours=len(ours))
            morning.keep(now, producers, sent=True)
        for watch, found in checks:
            alert = watch.update(found)
            if alert:
                # Outages and credit go to every approved chat; the owner's own tasks
                # (certificates, the model switch) and reorganisations stay with the owner.
                owner_only = watch is reorg_watch or watch is switch_watch or watch in cert_watches.values()
                log(event='alert', target=watch.name, text=alert, channels=send(alert, bot=bot, owner_only=owner_only))
        time.sleep(interval)


if __name__ == '__main__':
    sys.exit(main())
