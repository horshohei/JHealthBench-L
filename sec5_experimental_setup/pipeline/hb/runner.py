"""Execute answer / grading tasks with resume, full request/response logging and cache-aware order.

Run directory layout (all JSONL is append-only):
  manifest.json            table sha256, model entry, code hashes, input hashes, task count
  tasks.jsonl              task definitions (references into inputs/)
  inputs/conversations.jsonl, inputs/answers.jsonl, inputs/templates/*.md
  requests.jsonl           exact request body per task (auth headers are never stored)
  attempts.jsonl           every attempt: raw provider response or error, parsed label, usage
  results.jsonl            one line per completed task
  failures.jsonl           one line per task that exhausted its attempts (never counted as No)
"""
import hashlib
import json
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from . import parsing, prompting, providers
from .conditions import model as table_model


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256_text(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def body_sha(body):
    return sha256_text(json.dumps(body, ensure_ascii=False, sort_keys=True))


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


class Store:
    def __init__(self, run_dir):
        self.dir = Path(run_dir)
        self.lock = threading.Lock()

    def append(self, name, row):
        line = json.dumps(row, ensure_ascii=False) + '\n'
        with self.lock:
            with open(self.dir / name, 'a', encoding='utf-8') as f:
                f.write(line)
                f.flush()

    def load(self, name):
        return read_jsonl(self.dir / name)


class Inputs:
    def __init__(self, run_dir):
        d = Path(run_dir) / 'inputs'
        self.conversations = {r['conv_key']: r['messages'] for r in read_jsonl(d / 'conversations.jsonl')}
        self.answers = {r['answer_key']: r['text'] for r in read_jsonl(d / 'answers.jsonl')}
        self.templates = {p.stem: p.read_text(encoding='utf-8') for p in (d / 'templates').glob('*.md')}

    def prompt(self, task):
        msgs = self.conversations[task['conv_key']]
        resp = self.answers[task['answer_key']]
        tmpl = self.templates[task['template']]
        if task.get('meta_eval'):
            return prompting.render_meta_eval(tmpl, msgs, resp, task['criterion'])
        return prompting.render(tmpl, msgs, resp, task['criterion'], task['points'])


def build_request(m, task, inputs):
    if task['kind'] == 'answer':
        return providers.build(m, 'answer', messages=inputs.conversations[task['conv_key']])
    return providers.build(m, 'grade', prompt=inputs.prompt(task))


class Runner:
    def __init__(self, run_dir, table, transport=providers.http_post, sleep=time.sleep,
                 workers=8, timeout=900, retry_failed=False, log=print):
        self.dir = Path(run_dir)
        self.store = Store(run_dir)
        self.manifest = json.loads((self.dir / 'manifest.json').read_text(encoding='utf-8'))
        if self.manifest['table_sha256'] != table['_sha256']:
            raise RuntimeError('condition table changed since the run was built; build a new run')
        self.model_id = self.manifest['model_id']
        self.m = table_model(table, self.model_id)
        if json.dumps(self.m, sort_keys=True) != json.dumps(self.manifest['model_entry'], sort_keys=True):
            raise RuntimeError('model entry differs from the manifest')
        self.policy = table['run_policy']
        self.inputs = Inputs(run_dir)
        self.transport = transport
        self.sleep = sleep
        self.workers = workers
        self.timeout = timeout
        self.retry_failed = retry_failed
        self.log = log
        self.stats = {'ok': 0, 'failed': 0}

    # ------------------------------------------------------------- one task
    def run_task(self, task):
        url, headers, body = build_request(self.m, task, self.inputs)
        bsha = body_sha(body)
        self.store.append('requests.jsonl', {'task_id': task['task_id'], 'url': url, 'headers': headers,
                                             'body': body, 'body_sha256': bsha, 'at': now()})
        max_parse = self.policy['max_parse_attempts'] if task['kind'] == 'grade' else 1
        max_transport = self.policy['max_transport_attempts']
        parse_fail = 0
        transport_fail = 0
        attempt = 0
        while True:
            attempt += 1
            t0 = time.monotonic()
            try:
                raw = self.transport(url, {**headers, **providers.auth_headers(self.m)}, body, self.timeout)
            except providers.TransportError as e:
                transport_fail += 1
                self.store.append('attempts.jsonl', {
                    'task_id': task['task_id'], 'attempt': attempt, 'body_sha256': bsha, 'at': now(),
                    'ok': False, 'error': str(e), 'http_status': e.status, 'error_body': e.body,
                    'latency_s': round(time.monotonic() - t0, 3)})
                if not e.retryable or transport_fail >= max_transport:
                    return self.fail(task, f'transport: {e}', attempt)
                self.sleep(providers.backoff(transport_fail))
                continue
            info = providers.read(self.m['provider'], raw)
            rec = {'task_id': task['task_id'], 'attempt': attempt, 'body_sha256': bsha, 'at': now(), 'ok': True,
                   'latency_s': round(time.monotonic() - t0, 3), 'raw': raw,
                   'finish': info['finish'], 'truncated': info['truncated'], 'refusal': info['refusal'],
                   'usage': info['usage']}
            if task['kind'] == 'answer':
                self.store.append('attempts.jsonl', rec)
                return self.succeed(task, {'text': info['text'], 'finish': info['finish'],
                                           'truncated': info['truncated'], 'refusal': info['refusal'],
                                           'usage': info['usage'], 'attempt': attempt})
            label, parsed = parsing.grade_label(info['text'])
            rec['parsed_label'] = label
            self.store.append('attempts.jsonl', rec)
            if label is not None:
                return self.succeed(task, {'criteria_met': label,
                                           'explanation': parsed.get('explanation'),
                                           'usage': info['usage'], 'attempt': attempt,
                                           'parse_retries': parse_fail})
            # simple-evals re-samples on unparsable output; we cap and record instead of looping forever
            parse_fail += 1
            if parse_fail >= max_parse:
                return self.fail(task, f'no valid criteria_met after {parse_fail} outputs', attempt)

    def succeed(self, task, result):
        self.store.append('results.jsonl', {'task_id': task['task_id'], 'at': now(), **result})
        self.stats['ok'] += 1
        return True

    def fail(self, task, reason, attempts):
        self.store.append('failures.jsonl', {'task_id': task['task_id'], 'at': now(), 'reason': reason,
                                             'attempts': attempts})
        self.stats['failed'] += 1
        return False

    # ------------------------------------------------------------- scheduling
    def pending(self):
        tasks = read_jsonl(self.dir / 'tasks.jsonl')
        done = {r['task_id'] for r in self.store.load('results.jsonl')}
        failed = {r['task_id'] for r in self.store.load('failures.jsonl')}
        skip = done if self.retry_failed else done | failed
        return [t for t in tasks if t['task_id'] not in skip]

    def run(self):
        todo = self.pending()
        groups = {}
        for t in todo:
            groups.setdefault(t.get('cache_group') or t['task_id'], []).append(t)
        # First item of each group runs alone; the rest are released when it finishes, so their
        # shared prefix (instructions + conversation + graded answer) can be read from the cache.
        ready = deque(g[0] for g in groups.values())
        rest = {id(g[0]): g[1:] for g in groups.values()}
        lock = threading.Condition()
        state = {'inflight': 0}

        def worker():
            while True:
                with lock:
                    while not ready and state['inflight'] > 0:
                        lock.wait()
                    if not ready:
                        lock.notify_all()
                        return
                    task = ready.popleft()
                    state['inflight'] += 1
                try:
                    self.run_task(task)
                except Exception as e:  # programming/data error: record and continue with other tasks
                    self.fail(task, f'internal: {type(e).__name__}: {e}', 0)
                finally:
                    with lock:
                        followers = rest.pop(id(task), [])
                        ready.extendleft(reversed(followers))
                        state['inflight'] -= 1
                        lock.notify_all()

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, self.workers))]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.log(f'{self.model_id}: ok={self.stats["ok"]} failed={self.stats["failed"]} '
                 f'(pending before run={len(todo)})')
        return self.stats


def dry_run(run_dir, table, limit=None):
    """Build and store request bodies without sending anything."""
    d = Path(run_dir)
    manifest = json.loads((d / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['table_sha256'] != table['_sha256']:
        raise RuntimeError('condition table changed since the run was built')
    m = table_model(table, manifest['model_id'])
    inputs = Inputs(run_dir)
    out = []
    for i, task in enumerate(read_jsonl(d / 'tasks.jsonl')):
        if limit is not None and i >= limit:
            break
        url, headers, body = build_request(m, task, inputs)
        out.append({'task_id': task['task_id'], 'url': url, 'headers': headers, 'body': body,
                    'body_sha256': body_sha(body)})
    with open(d / 'dry_run_requests.jsonl', 'w', encoding='utf-8') as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    return out


def verify_requests(run_dir, table):
    """Every stored request body must equal what the condition table produces (no extra keys)."""
    d = Path(run_dir)
    manifest = json.loads((d / 'manifest.json').read_text(encoding='utf-8'))
    m = table_model(table, manifest['model_id'])
    inputs = Inputs(run_dir)
    tasks = {t['task_id']: t for t in read_jsonl(d / 'tasks.jsonl')}
    problems = []
    for r in read_jsonl(d / 'requests.jsonl'):
        _, _, body = build_request(m, tasks[r['task_id']], inputs)
        if body != r['body']:
            problems.append(r['task_id'])
        allowed = set(m['send']) | {'input', 'messages', 'contents'}
        if m['provider'] == 'gemini_generate':
            allowed.discard('model')
        extra = set(r['body']) - allowed
        if extra:
            problems.append(f"{r['task_id']}: unexpected keys {sorted(extra)}")
    return problems
