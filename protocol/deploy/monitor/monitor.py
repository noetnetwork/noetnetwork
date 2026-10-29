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
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, RUNPOD_API_KEY
  SMTP_HOST, SMTP_PORT (465 = TLS, else STARTTLS), SMTP_USER, SMTP_PASSWORD,
  ALERT_EMAIL_FROM, ALERT_EMAIL_TO
"""
from email.message import EmailMessage
import json
import os
import smtplib
import sys
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


def problems(health, stall_seconds):
    """Problems worth waking someone for, from one /healthz/chain reply (None = unreachable)."""
    if health is None:
        return ['health endpoint unreachable']
    found = []
    age = health.get('lastBlockAgeSeconds')
    if age is None or age > stall_seconds:
        minutes = 'never' if age is None else f'{age // 60} min ago'
        found.append(f'no block for over {stall_seconds // 60} min (last: {minutes}, height {health.get("height")})')
    if 'index stale' in (health.get('problems') or []):
        found.append('explorer index is stale')
    days = health.get('relayCertificateDaysLeft')
    if days is not None and days < CERT_DAYS:
        urgent = 'URGENT: ' if days <= 3 else ''
        found.append(f'{urgent}relay certificates expire in {days} days; rotate with deploy/rotate.py --apply')
    return found


def verdict_problems(info, limit=VERDICT_SECONDS):
    """A block accepted by the gateway that has waited too long for its verdict,
    from one /mining/info reply (None = unreachable)."""
    if info is None:
        return ['mining gateway unreachable']
    waiting = info.get('awaitingVerdict') or {}
    seconds = waiting.get('seconds')
    if not isinstance(seconds, int) or seconds < limit:
        return []
    return [f'block {waiting.get("height")} has waited {seconds // 60} min for its Full verdict; '
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
    """Per-target alert state: alert after two failing checks, remind, announce recovery."""

    def __init__(self, name, clock=time.time, remind=REMIND_SECONDS):
        self.name, self.clock, self.remind = name, clock, remind
        self.failing, self.alerted_at, self.last = 0, None, []

    def update(self, found):
        now = self.clock()
        if not found:
            self.failing, self.last = 0, []
            if self.alerted_at is not None:
                self.alerted_at = None
                return f'NOET {self.name}: recovered.'
            return None
        self.failing += 1
        if self.failing < 2:
            return None
        changed = found != self.last
        self.last = found
        if self.alerted_at is None or changed or now - self.alerted_at >= self.remind:
            first = self.alerted_at is None
            self.alerted_at = now
            return f'NOET {self.name}: ' + ('' if first else 'still: ') + '; '.join(found)
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


def fetch_blocks(url, timeout=20):
    """The newest blocks from a public explorer list, or None."""
    request = urllib.request.Request(url, headers={'User-Agent': 'noet-monitor'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            rows = json.loads(response.read(1 << 22))
        return [{'height': int(r['height']), 'hash': str(r['hash'])} for r in rows]
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, KeyError, TypeError):
        return None


def send(text, env=os.environ):
    sent = []
    if env.get('TELEGRAM_BOT_TOKEN') and env.get('TELEGRAM_CHAT_ID'):
        data = urllib.parse.urlencode({'chat_id': env['TELEGRAM_CHAT_ID'], 'text': text}).encode()
        try:
            urllib.request.urlopen(f'https://api.telegram.org/bot{env["TELEGRAM_BOT_TOKEN"]}/sendMessage',
                                   data=data, timeout=20).read()
            sent.append('telegram')
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            log(event='telegram-failed', type=type(error).__name__)
    if env.get('SMTP_HOST') and env.get('ALERT_EMAIL_TO'):
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


def main():
    watched = targets(os.environ.get('NOET_MONITOR_TARGETS', 'mainnet=https://noet-mainnet.fly.dev'))
    stall = int(os.environ.get('NOET_MONITOR_STALL', '5400'))
    interval = int(os.environ.get('NOET_MONITOR_INTERVAL', '120'))
    watches = {name: Watch(name) for name, _ in watched}
    switch_height = int(os.environ.get('NOET_MONITOR_SWITCH_HEIGHT') or 0)
    switch_ready = os.environ.get('NOET_MONITOR_SWITCH_READY') == '1'
    switch_watch = Watch('model switch', remind=86400)
    gateway = os.environ.get('NOET_MONITOR_GATEWAY')
    verdict_limit = int(os.environ.get('NOET_MONITOR_VERDICT') or VERDICT_SECONDS)
    verdict_watch = Watch('verifier')
    key, pod = os.environ.get('RUNPOD_API_KEY'), os.environ.get('NOET_MONITOR_POD')
    blocks_url = os.environ.get('NOET_MONITOR_BLOCKS', 'https://www.noet.network/api/chain/blocks')
    reorgs, reorg_watch = Reorgs(), Watch('chain', remind=86400)
    names = [name for name, _ in watched] + (['verdicts'] if gateway else []) + ['reorganisations']
    if key and pod:
        pod_watch, credit_watch = Watch('GPU pod'), Watch('GPU credit', remind=86400)
        names.append(f'GPU pod {pod}')
        reported = False
    log(event='monitor-started', targets=names, channels=send('NOET monitor started: watching ' + ', '.join(names)))
    while True:
        health = {name: fetch(url) for name, url in watched}
        checks = [(watches[name], problems(health[name], stall)) for name, _ in watched]
        checks.append((switch_watch, switch_problems(health.get('mainnet'), switch_height, switch_ready)))
        # A reorganisation is reported once: the next list shows the new chain as seen.
        reorg_watch.failing = 1
        checks.append((reorg_watch, reorgs.update(fetch_blocks(blocks_url))))
        if gateway:
            checks.append((verdict_watch, verdict_problems(fetch(gateway, path='/mining/info'), verdict_limit)))
        if key and pod:
            account = runpod(key)
            if account and not reported:
                reported = True
                log(event='gpu-account', pod=pod, status={p['id']: p['desiredStatus'] for p in account['pods']}.get(pod),
                    balance=round(account['clientBalance'], 2), spendPerHr=account['currentSpendPerHr'])
            checks += [(pod_watch, pod_problems(account, pod)), (credit_watch, credit_problems(account))]
        for watch, found in checks:
            alert = watch.update(found)
            if alert:
                log(event='alert', target=watch.name, text=alert, channels=send(alert))
        time.sleep(interval)


if __name__ == '__main__':
    sys.exit(main())
