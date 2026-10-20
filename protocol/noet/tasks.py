"""Useful work for miners: a queue of public AI requests, and an open dataset.

Anyone (a person through the website, or an agent through the gateway) may
queue a prompt. A miner that asks for a task with its work lease runs that
prompt as the inference behind its proof and returns the answer, so every
mining attempt answers someone. When no request is waiting, the miner gets the
next item of the open dataset (`open_dataset.py`) instead.

Prompts and answers are public: the prompt is part of the proof the network's
verifier replays, and answers are published here. Screening refuses what would
break the chat template or looks like a key or secret; an operator can redact a
row afterwards (`python -m noet.tasks redact ID`). An answer counts as checked
only when the attempt that produced it became a block the verifier accepted.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

PROMPT_CHARS = 1500
ANSWER_CHARS = 8000
QUEUE_LIMIT = 200
CLAIM_ATTEMPTS = 3
KEEP_REQUESTS = 30 * 86400
TASK_ID = re.compile(r'[0-9a-f]{24}')
CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
# Chat-template markers would let a prompt rewrite the conversation around it.
TEMPLATE = re.compile(r'<\|[^|<>]{0,40}\|>|</?think>', re.I)
SECRET = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY|\b[xtuvyz]prv[1-9A-HJ-NP-Za-km-z]{100,}\b'
                    r'|\b[0-9a-fA-F]{64}\b|\b[5KLc9][1-9A-HJ-NP-Za-km-z]{50,51}\b')
NOTICE = ('Prompts and answers are public: they are published by the network and may be part of a block. '
          'Do not send personal data or secrets.')


class TaskRefused(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def screen(prompt):
    """The prompt as it will be queued, or TaskRefused."""
    if not isinstance(prompt, str):
        raise TaskRefused(400, 'Send the prompt as text')
    prompt = prompt.replace('\r\n', '\n').strip()
    if len(prompt) < 3:
        raise TaskRefused(400, 'The prompt is too short')
    if len(prompt) > PROMPT_CHARS:
        raise TaskRefused(413, f'Keep the prompt under {PROMPT_CHARS} characters')
    if CONTROL.search(prompt):
        raise TaskRefused(400, 'The prompt contains control characters')
    if TEMPLATE.search(prompt):
        raise TaskRefused(400, 'The prompt contains chat-template markers')
    if SECRET.search(prompt):
        raise TaskRefused(400, 'This looks like it contains a key or secret. Prompts are public.')
    return prompt


def clean_answer(text):
    if not isinstance(text, str):
        raise TaskRefused(400, 'Send the answer as text')
    text = CONTROL.sub('', text.replace('\r\n', '\n')).strip()
    if len(text) > ANSWER_CHARS:
        raise TaskRefused(413, 'Answer is too long')
    return text


SCHEMA = '''
create table if not exists tasks (
  id text primary key, kind text not null, prompt text not null, source text,
  client text, created real not null, state text not null, attempts integer not null default 0,
  lease text, claimed_until real, answer text, truncated integer, answered real,
  miner text, block text, height integer, redacted integer not null default 0);
create index if not exists tasks_queue on tasks (state, kind, created);
create index if not exists tasks_lease on tasks (lease);
create index if not exists tasks_answered on tasks (kind, answered);
create table if not exists meta (key text primary key, value text not null);
'''


class TaskStore:
    def __init__(self, path, dataset, clock=time.time):
        self.dataset, self.clock, self.lock = dataset, clock, threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def _release_stale(self, now):
        self.db.execute("update tasks set state = case when attempts >= ? then 'failed' else 'queued' end, "
                        "lease = null, claimed_until = null where state = 'claimed' and claimed_until <= ?",
                        (CLAIM_ATTEMPTS, now))

    def add(self, prompt, client):
        prompt = screen(prompt)
        now = self.clock()
        with self.lock:
            self._release_stale(now)
            waiting = self.db.execute("select count(*) from tasks where kind = 'request' "
                                      "and state in ('queued', 'claimed')").fetchone()[0]
            if waiting >= QUEUE_LIMIT:
                raise TaskRefused(503, 'The request queue is full. Try again in a few minutes.')
            task = secrets.token_hex(12)
            self.db.execute("insert into tasks (id, kind, prompt, client, created, state) "
                            "values (?, 'request', ?, ?, ?, 'queued')", (task, prompt, client, now))
        return {'id': task, 'state': 'queued', 'position': waiting + 1, 'notice': NOTICE}

    def claim(self, lease, expires, miner):
        """The task for one work lease: the oldest waiting request, else the next dataset item."""
        now = self.clock()
        with self.lock:
            self._release_stale(now)
            row = self.db.execute("select * from tasks where state = 'queued' order by "
                                  "kind = 'dataset', created limit 1").fetchone()
            if row is None:
                position = int((self.db.execute("select value from meta where key = 'dataset'").fetchone()
                                or ['0'])[0])
                source, prompt = self.dataset(position)
                task = secrets.token_hex(12)
                self.db.execute("insert into tasks (id, kind, prompt, source, created, state) "
                                "values (?, 'dataset', ?, ?, ?, 'queued')", (task, prompt, source, now))
                self.db.execute("insert or replace into meta values ('dataset', ?)", (str(position + 1),))
                row = self.db.execute('select * from tasks where id = ?', (task,)).fetchone()
            self.db.execute("update tasks set state = 'claimed', lease = ?, claimed_until = ?, "
                            "attempts = attempts + 1, miner = ? where id = ?", (lease, expires, miner, row['id']))
        return {'id': row['id'], 'kind': row['kind'], 'prompt': row['prompt']}

    def answer(self, lease, task, text, truncated):
        if not isinstance(task, str) or not TASK_ID.fullmatch(task):
            raise TaskRefused(400, 'Invalid task id')
        text = clean_answer(text)
        with self.lock:
            done = self.db.execute("update tasks set state = 'answered', answer = ?, truncated = ?, answered = ?, "
                                   "claimed_until = null where id = ? and lease = ? and state = 'claimed'",
                                   (text, int(bool(truncated)), self.clock(), task, lease)).rowcount
        if not done:
            raise TaskRefused(404, 'This lease does not hold that task')
        return {'answered': True}

    def released(self, lease):
        """A lease ended without an answer: the task waits for the next miner."""
        with self.lock:
            self.db.execute("update tasks set state = case when attempts >= ? then 'failed' else 'queued' end, "
                            "lease = null, claimed_until = null where lease = ? and state = 'claimed'",
                            (CLAIM_ATTEMPTS, lease))

    def mined(self, lease, block, height):
        with self.lock:
            self.db.execute('update tasks set block = ?, height = ? where lease = ?', (block, height, lease))

    @staticmethod
    def public(row):
        hidden = bool(row['redacted'])
        return {'id': row['id'], 'kind': row['kind'], 'state': row['state'],
                'prompt': None if hidden else row['prompt'], 'source': row['source'],
                'answer': None if hidden else row['answer'],
                'truncated': None if row['truncated'] is None else bool(row['truncated']),
                'createdAt': row['created'], 'answeredAt': row['answered'],
                'miner': row['miner'] if row['state'] == 'answered' else None,
                'block': row['block'], 'height': row['height'], 'redacted': hidden}

    def get(self, task):
        if not isinstance(task, str) or not TASK_ID.fullmatch(task):
            raise TaskRefused(400, 'Invalid task id')
        with self.lock:
            self._release_stale(self.clock())
            row = self.db.execute('select * from tasks where id = ?', (task,)).fetchone()
            if row is None:
                raise TaskRefused(404, 'Unknown task')
            view = self.public(row)
            if row['state'] == 'queued':
                view['position'] = self.db.execute(
                    "select count(*) from tasks where state = 'queued' and kind = 'request' and created <= ?",
                    (row['created'],)).fetchone()[0]
        return view

    def recent(self, kind=None, before=None, limit=50):
        if kind not in (None, 'request', 'dataset'):
            raise TaskRefused(400, 'Unknown kind')
        if before is not None and not (isinstance(before, (int, float)) and before > 0):
            raise TaskRefused(400, 'Invalid cursor')
        limit = max(1, min(int(limit), 200))
        query = "select * from tasks where state = 'answered' and redacted = 0"
        args = []
        if kind:
            query += ' and kind = ?'
            args.append(kind)
        if before:
            query += ' and answered < ?'
            args.append(before)
        with self.lock:
            rows = self.db.execute(query + ' order by answered desc limit ?', (*args, limit)).fetchall()
        items = [self.public(r) for r in rows]
        return {'items': items, 'next': items[-1]['answeredAt'] if len(items) == limit else None}

    def counts(self):
        with self.lock:
            rows = self.db.execute('select kind, state, count(*) n from tasks group by kind, state').fetchall()
        return {f"{r['kind']}:{r['state']}": r['n'] for r in rows}

    def redact(self, task):
        with self.lock:
            return self.db.execute('update tasks set redacted = 1 where id = ?', (task,)).rowcount

    def prune(self):
        with self.lock:
            self._release_stale(self.clock())
            self.db.execute("delete from tasks where kind = 'request' and created < ?",
                            (self.clock() - KEEP_REQUESTS,))


def open_store(path=None):
    from .open_dataset import item
    return TaskStore(path or os.environ['NOET_TASKS_DB'], item)


def main():
    parser = argparse.ArgumentParser(description='Operator tools for the task queue')
    parser.add_argument('action', choices=('redact', 'counts'))
    parser.add_argument('task', nargs='?')
    args = parser.parse_args()
    store = open_store(Path(os.environ.get('NOET_TASKS_DB', '/data/tasks.sqlite3')))
    if args.action == 'redact':
        print(json.dumps({'redacted': store.redact(args.task)}))
    else:
        print(json.dumps(store.counts()))


if __name__ == '__main__':
    main()
