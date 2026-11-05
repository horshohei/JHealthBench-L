"""Submit a run (built by tasks.py) through the providers' batch APIs instead of synchronous calls.

Supported: OpenAI Batch API (openai_responses / openai_chat), Anthropic Message Batches
(anthropic_messages) and Gemini Batch mode with inline requests (gemini_generate).

The request body of every item is exactly what the synchronous runner would send
(`runner.build_request`), so the condition-table checks apply unchanged. Results are written into
the same files as the synchronous runner (requests.jsonl, attempts.jsonl, results.jsonl,
failures.jsonl), so `score`, `gold-metrics`, `verify` and the checks work on either path.

Lifecycle per run directory (state in batches/state.json, every provider reply saved next to it):
  prepare  -> one round = the pending items, split into chunks within the provider limits
  submit   -> create the provider batch for each prepared chunk
  poll     -> refresh the status of submitted chunks
  collect  -> download the results of ended chunks, import them; failed, expired or unparsable
              items stay pending and go into the next round, up to max_attempts per item
API keys are read from the environment when a call is made and are never written to disk.
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import parsing, providers, runner
from .conditions import model as table_model

FAMILY = {'openai_responses': 'openai', 'openai_chat': 'openai', 'anthropic_messages': 'anthropic',
          'gemini_generate': 'gemini'}
OPENAI_ENDPOINT = {'openai_responses': '/v1/responses', 'openai_chat': '/v1/chat/completions'}
# Documented per-batch limits (sources in condition_table_v3.json decisions.D15); a margin is kept for the envelope.
DEFAULT_LIMITS = {'openai': {'requests': 50000, 'bytes': 190 * 1024 * 1024},
                  'anthropic': {'requests': 100000, 'bytes': 240 * 1024 * 1024},
                  'gemini': {'requests': 100000, 'bytes': 18 * 1024 * 1024}}
ENVELOPE_KEYS = {'openai': {'custom_id', 'method', 'url', 'body'}, 'anthropic': {'custom_id', 'params'},
                 'gemini': {'key', 'request'}}
TERMINAL = {'openai': {'completed', 'failed', 'expired', 'cancelled'},
            'anthropic': {'ended'},
            'gemini': {'JOB_STATE_SUCCEEDED', 'JOB_STATE_FAILED', 'JOB_STATE_CANCELLED', 'JOB_STATE_EXPIRED',
                       'BATCH_STATE_SUCCEEDED', 'BATCH_STATE_FAILED', 'BATCH_STATE_CANCELLED', 'BATCH_STATE_EXPIRED'}}
OPENAI_BASE = 'https://api.openai.com'
ANTHROPIC_BASE = 'https://api.anthropic.com'
GEMINI_BASE = 'https://generativelanguage.googleapis.com'


def custom_id(task_id):
    """Anthropic requires ^[a-zA-Z0-9_-]{1,64}$; task ids contain ':' and '|', so a hash is used."""
    return 'h' + hashlib.sha256(task_id.encode('utf-8')).hexdigest()[:40]


def line_for(family, provider, cid, body):
    if family == 'openai':
        return {'custom_id': cid, 'method': 'POST', 'url': OPENAI_ENDPOINT[provider], 'body': body}
    if family == 'anthropic':
        return {'custom_id': cid, 'params': body}
    if family == 'gemini':
        return {'key': cid, 'request': body}
    raise ValueError(family)


def body_of(family, line):
    return {'openai': 'body', 'anthropic': 'params', 'gemini': 'request'}[family], \
        line.get({'openai': 'body', 'anthropic': 'params', 'gemini': 'request'}[family])


# ---------------------------------------------------------------- HTTP (injectable for tests)
class HTTPError(Exception):
    def __init__(self, status, body):
        super().__init__(f'HTTP {status}: {body[:300] if isinstance(body, str) else body}')
        self.status = status
        self.body = body


def http_request(method, url, headers, data=None, timeout=600):
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise HTTPError(e.code, e.read().decode('utf-8', 'replace')) from None


def _key(m):
    env = m.get('api_key_env')
    key = os.environ.get(env or '')
    if not key:
        raise RuntimeError(f'environment variable {env} is not set')
    return key


def _json(b):
    return json.loads(b.decode('utf-8') if isinstance(b, (bytes, bytearray)) else b)


def _jsonl(b):
    text = b.decode('utf-8') if isinstance(b, (bytes, bytearray)) else b
    return [json.loads(x) for x in text.splitlines() if x.strip()]


class OpenAIBatch:
    def __init__(self, m, http):
        self.m, self.http = m, http

    def _h(self, extra=None):
        return {'authorization': f'Bearer {_key(self.m)}', **(extra or {})}

    def submit(self, input_path, provider, meta):
        boundary = 'hb' + uuid.uuid4().hex
        content = Path(input_path).read_bytes()
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nbatch\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{Path(input_path).name}"\r\n'
                'Content-Type: application/jsonl\r\n\r\n').encode('utf-8') + content + f'\r\n--{boundary}--\r\n'.encode('utf-8')
        f = _json(self.http('POST', f'{OPENAI_BASE}/v1/files',
                            self._h({'content-type': f'multipart/form-data; boundary={boundary}'}), body))
        b = _json(self.http('POST', f'{OPENAI_BASE}/v1/batches', self._h({'content-type': 'application/json'}),
                            json.dumps({'input_file_id': f['id'], 'endpoint': OPENAI_ENDPOINT[provider],
                                        'completion_window': '24h', 'metadata': meta}).encode('utf-8')))
        return b['id'], {'file': f, 'batch': b}

    def status(self, bid):
        b = _json(self.http('GET', f'{OPENAI_BASE}/v1/batches/{bid}', self._h()))
        return b.get('status'), b

    def results(self, status_obj):
        """Yield (cid, kind, payload): kind 'ok' -> raw body, 'error' -> (retryable, message)."""
        out = []
        for fid in (status_obj.get('output_file_id'), status_obj.get('error_file_id')):
            if not fid:
                continue
            for line in _jsonl(self.http('GET', f'{OPENAI_BASE}/v1/files/{fid}/content', self._h())):
                resp = line.get('response') or {}
                if resp.get('status_code') == 200 and not line.get('error'):
                    out.append((line['custom_id'], 'ok', resp.get('body')))
                else:
                    err = line.get('error') or (resp.get('body') or {}).get('error') or {}
                    code = resp.get('status_code')
                    retryable = not (code is not None and 400 <= code < 500 and code not in (408, 409, 429))
                    if (err or {}).get('code') == 'batch_expired':
                        retryable = True
                    out.append((line['custom_id'], 'error', (retryable, json.dumps({'status_code': code, 'error': err}, ensure_ascii=False))))
        if status_obj.get('status') == 'failed' and not out:
            # validation failure of the whole batch: the request itself is invalid -> not retried
            out.append(('*', 'error', (False, json.dumps(status_obj.get('errors'), ensure_ascii=False))))
        return out


class AnthropicBatch:
    def __init__(self, m, http):
        self.m, self.http = m, http

    def _h(self, extra=None):
        return {'x-api-key': _key(self.m), 'anthropic-version': providers.ANTHROPIC_VERSION, **(extra or {})}

    def submit(self, input_path, provider, meta):
        lines = runner.read_jsonl(input_path)
        b = _json(self.http('POST', f'{ANTHROPIC_BASE}/v1/messages/batches', self._h({'content-type': 'application/json'}),
                            json.dumps({'requests': lines}, ensure_ascii=False).encode('utf-8')))
        return b['id'], {'batch': b}

    def status(self, bid):
        b = _json(self.http('GET', f'{ANTHROPIC_BASE}/v1/messages/batches/{bid}', self._h()))
        return b.get('processing_status'), b

    def results(self, status_obj):
        out = []
        url = status_obj.get('results_url')
        if not url:
            return out
        for line in _jsonl(self.http('GET', url, self._h())):
            r = line.get('result') or {}
            t = r.get('type')
            if t == 'succeeded':
                out.append((line['custom_id'], 'ok', r.get('message')))
            else:
                etype = (((r.get('error') or {}).get('error')) or {}).get('type')
                retryable = not (t == 'errored' and etype == 'invalid_request_error')
                out.append((line['custom_id'], 'error', (retryable, json.dumps({'type': t, 'error': r.get('error')}, ensure_ascii=False))))
        return out


class GeminiBatch:
    def __init__(self, m, http):
        self.m, self.http = m, http

    def _h(self, extra=None):
        return {'x-goog-api-key': _key(self.m), **(extra or {})}

    def submit(self, input_path, provider, meta):
        lines = runner.read_jsonl(input_path)
        reqs = [{'request': x['request'], 'metadata': {'key': x['key']}} for x in lines]
        payload = {'batch': {'display_name': meta.get('display_name', 'hb-redo'),
                             'input_config': {'requests': {'requests': reqs}}}}
        model = self.m['send']['model']
        b = _json(self.http('POST', f'{GEMINI_BASE}/v1beta/models/{model}:batchGenerateContent',
                            self._h({'content-type': 'application/json'}), json.dumps(payload, ensure_ascii=False).encode('utf-8')))
        name = b.get('name') or (b.get('batch') or {}).get('name')
        return name, {'batch': b, 'submitted_keys': [x['key'] for x in lines]}

    @staticmethod
    def state_of(b):
        for obj in (b, b.get('metadata') or {}, b.get('batch') or {}):
            if isinstance(obj, dict) and obj.get('state'):
                return obj['state']
        return 'JOB_STATE_SUCCEEDED' if b.get('done') and not b.get('error') else ('JOB_STATE_FAILED' if b.get('error') else None)

    def status(self, bid):
        b = _json(self.http('GET', f'{GEMINI_BASE}/v1beta/{bid}', self._h()))
        return self.state_of(b), b

    @staticmethod
    def _find(obj, key):
        if isinstance(obj, dict):
            if key in obj:
                return obj[key]
            for v in obj.values():
                r = GeminiBatch._find(v, key)
                if r is not None:
                    return r
        elif isinstance(obj, list):
            for v in obj:
                r = GeminiBatch._find(v, key)
                if r is not None:
                    return r
        return None

    def results(self, status_obj, submitted_keys=()):
        out = []
        inl = self._find(status_obj, 'inlinedResponses')
        while isinstance(inl, dict) and 'inlinedResponses' in inl:  # REST nests the list one level
            inl = inl['inlinedResponses']
        rows = list(inl) if isinstance(inl, list) else []
        if not rows:
            fname = self._find(status_obj, 'responsesFile')
            if fname:
                rows = _jsonl(self.http('GET', f'{GEMINI_BASE}/download/v1beta/{fname}:download?alt=media', self._h()))
        for i, r in enumerate(rows):
            key = (r.get('metadata') or {}).get('key') or r.get('key')
            if key is None and i < len(submitted_keys):
                key = submitted_keys[i]  # inline responses come back in request order
            if r.get('response') is not None and not r.get('error'):
                out.append((key, 'ok', r['response']))
            else:
                err = r.get('error') or r.get('status') or {}
                code = err.get('code') if isinstance(err, dict) else None
                retryable = not (code in (3, 400, 'INVALID_ARGUMENT'))
                out.append((key, 'error', (retryable, json.dumps(err, ensure_ascii=False))))
        state = self.state_of(status_obj)
        if state in ('JOB_STATE_FAILED', 'BATCH_STATE_FAILED') and not out:
            out.append(('*', 'error', (True, json.dumps(status_obj.get('error'), ensure_ascii=False))))
        return out


ADAPTERS = {'openai': OpenAIBatch, 'anthropic': AnthropicBatch, 'gemini': GeminiBatch}


# ---------------------------------------------------------------- run-level driver
class BatchRun:
    def __init__(self, run_dir, table, http=http_request, clock=time.time, log=print):
        self.dir = Path(run_dir)
        self.manifest = json.loads((self.dir / 'manifest.json').read_text(encoding='utf-8'))
        if self.manifest['table_sha256'] != table['_sha256']:
            raise RuntimeError('condition table changed since the run was built; build a new run')
        self.table = table
        self.m = table_model(table, self.manifest['model_id'])
        if json.dumps(self.m, sort_keys=True) != json.dumps(self.manifest['model_entry'], sort_keys=True):
            raise RuntimeError('model entry differs from the manifest')
        self.provider = self.m['provider']
        if self.provider not in FAMILY:
            raise RuntimeError(f'{self.provider} has no batch API here; use run.py execute')
        self.family = FAMILY[self.provider]
        self.adapter = ADAPTERS[self.family](self.m, http)
        self.store = runner.Store(run_dir)
        self.inputs = runner.Inputs(run_dir)
        self.tasks = {t['task_id']: t for t in runner.read_jsonl(self.dir / 'tasks.jsonl')}
        pol = table['run_policy']
        self.max_attempts = (pol.get('batch') or {}).get('max_attempts_per_item', pol['max_parse_attempts'])
        self.limits = dict(DEFAULT_LIMITS[self.family])
        self.bdir = self.dir / 'batches'
        self.bdir.mkdir(exist_ok=True)
        self.clock = clock
        self.log = log  # documented limits are recorded in the table; DEFAULT_LIMITS keeps a margin below them

    # ---------------- state
    def state(self):
        p = self.bdir / 'state.json'
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'chunks': []}

    def save(self, st):
        tmp = self.bdir / 'state.json.tmp'
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(self.bdir / 'state.json')

    def event(self, **kw):
        self.store.append('batches/events.jsonl', {'at': runner.now(), **kw})

    def attempts(self):
        n = {}
        for a in runner.read_jsonl(self.dir / 'attempts.jsonl'):
            n[a['task_id']] = n.get(a['task_id'], 0) + 1
        return n

    def pending(self, st=None):
        st = st or self.state()
        done = {r['task_id'] for r in runner.read_jsonl(self.dir / 'results.jsonl')}
        failed = {r['task_id'] for r in runner.read_jsonl(self.dir / 'failures.jsonl')}
        open_ = set()
        for c in st['chunks']:
            if not c.get('collected'):
                open_.update(c['task_ids'])
        return [t for t in self.tasks if t not in done | failed | open_]

    # ---------------- prepare
    def prepare(self, max_requests=None):
        st = self.state()
        todo = self.pending(st)
        if not todo:
            return []
        rnd = 1 + max([c['round'] for c in st['chunks']] or [0])
        cap_n = min(self.limits['requests'], max_requests or self.limits['requests'])
        chunks, cur, cur_bytes = [], [], 0
        for tid in todo:
            task = self.tasks[tid]
            url, headers, body = runner.build_request(self.m, task, self.inputs)
            cid = custom_id(tid)
            line = json.dumps(line_for(self.family, self.provider, cid, body), ensure_ascii=False)
            size = len(line.encode('utf-8')) + 1
            if cur and (len(cur) >= cap_n or cur_bytes + size > self.limits['bytes']):
                chunks.append(cur)
                cur, cur_bytes = [], 0
            cur.append((tid, cid, line, url, headers, body))
            cur_bytes += size
        if cur:
            chunks.append(cur)
        made = []
        for i, ch in enumerate(chunks, 1):
            name = f'round{rnd:02d}_chunk{i:03d}'
            with open(self.bdir / f'{name}.input.jsonl', 'w', encoding='utf-8') as f:
                for tid, cid, line, url, headers, body in ch:
                    f.write(line + '\n')
                    self.store.append('requests.jsonl', {'task_id': tid, 'url': url, 'headers': headers, 'body': body,
                                                         'body_sha256': runner.body_sha(body), 'at': runner.now(),
                                                         'batch_chunk': name, 'custom_id': cid})
            c = {'name': name, 'round': rnd, 'status': 'prepared', 'n': len(ch), 'task_ids': [x[0] for x in ch],
                 'map': {x[1]: x[0] for x in ch}, 'input_sha256': runner.sha256_text((self.bdir / f'{name}.input.jsonl').read_text(encoding='utf-8')),
                 'prepared_at': runner.now()}
            st['chunks'].append(c)
            made.append(name)
            self.event(action='prepare', chunk=name, n=len(ch))
        self.save(st)
        return made

    # ---------------- submit / poll
    def submit(self):
        st = self.state()
        problems = verify_batch_inputs(self.dir, self.table)
        if problems:
            raise RuntimeError(f'batch input verification failed: {problems[:5]}')
        out = []
        for c in st['chunks']:
            if c['status'] != 'prepared':
                continue
            bid, raw = self.adapter.submit(self.bdir / f"{c['name']}.input.jsonl", self.provider,
                                           {'run': self.dir.name, 'chunk': c['name'], 'display_name': f"{self.dir.name}-{c['name']}"})
            (self.bdir / f"{c['name']}.submit.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding='utf-8')
            c.update(status='submitted', provider_batch_id=bid, submitted_at=runner.now())
            if raw.get('submitted_keys'):
                c['submitted_keys'] = raw['submitted_keys']
            self.save(st)
            self.event(action='submit', chunk=c['name'], provider_batch_id=bid)
            out.append((c['name'], bid))
        return out

    def poll(self):
        st = self.state()
        out = []
        for c in st['chunks']:
            if c['status'] not in ('submitted', 'running'):
                continue
            s, raw = self.adapter.status(c['provider_batch_id'])
            (self.bdir / f"{c['name']}.status.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding='utf-8')
            c['provider_status'] = s
            if s in TERMINAL[self.family]:
                c['status'] = 'ended'
                c['ended_at'] = runner.now()
            else:
                c['status'] = 'running'
            out.append((c['name'], s))
        self.save(st)
        return out

    # ---------------- collect / import
    def collect(self):
        st = self.state()
        n_attempts = self.attempts()
        summary = {'ok': 0, 'parse_failed': 0, 'errors': 0, 'missing': 0, 'gave_up': 0}
        for c in st['chunks']:
            if c['status'] != 'ended' or c.get('collected'):
                continue
            raw_status = json.loads((self.bdir / f"{c['name']}.status.json").read_text(encoding='utf-8'))
            if self.family == 'gemini':
                rows = self.adapter.results(raw_status, c.get('submitted_keys', []))
            else:
                rows = self.adapter.results(raw_status)
            with open(self.bdir / f"{c['name']}.output.jsonl", 'w', encoding='utf-8') as f:
                for r in rows:
                    f.write(json.dumps({'custom_id': r[0], 'kind': r[1], 'payload': r[2]}, ensure_ascii=False) + '\n')
            seen = set()
            whole_batch_error = next((r for r in rows if r[0] == '*'), None)
            for cid, kind, payload in rows:
                if cid == '*':
                    continue
                tid = c['map'].get(cid)
                if tid is None or tid in seen:
                    continue
                seen.add(tid)
                self._import(tid, kind, payload, c, n_attempts, summary)
            for cid, tid in c['map'].items():
                if tid in seen:
                    continue
                if whole_batch_error:
                    self._import(tid, 'error', whole_batch_error[2], c, n_attempts, summary)
                else:
                    summary['missing'] += 1
                    self._import(tid, 'error', (True, 'missing in batch output'), c, n_attempts, summary)
            c['collected'] = True
            c['collected_at'] = runner.now()
            self.save(st)
            self.event(action='collect', chunk=c['name'], **summary)
        return summary

    def _import(self, tid, kind, payload, chunk, n_attempts, summary):
        task = self.tasks[tid]
        n_attempts[tid] = n_attempts.get(tid, 0) + 1
        att = n_attempts[tid]
        base = {'task_id': tid, 'attempt': att, 'at': runner.now(), 'batch_chunk': chunk['name'],
                'provider_batch_id': chunk.get('provider_batch_id'), 'latency_s': None}
        if kind == 'ok':
            raw = payload
            info = providers.read(self.provider, raw)
            rec = {**base, 'ok': True, 'raw': raw, 'finish': info['finish'], 'truncated': info['truncated'],
                   'refusal': info['refusal'], 'usage': info['usage']}
            if task['kind'] == 'answer':
                self.store.append('attempts.jsonl', rec)
                self.store.append('results.jsonl', {'task_id': tid, 'at': runner.now(), 'text': info['text'],
                                                    'finish': info['finish'], 'truncated': info['truncated'],
                                                    'refusal': info['refusal'], 'usage': info['usage'], 'attempt': att,
                                                    'via': 'batch'})
                summary['ok'] += 1
                return
            label, parsed = parsing.grade_label(info['text'])
            rec['parsed_label'] = label
            self.store.append('attempts.jsonl', rec)
            if label is not None:
                self.store.append('results.jsonl', {'task_id': tid, 'at': runner.now(), 'criteria_met': label,
                                                    'explanation': parsed.get('explanation'), 'usage': info['usage'],
                                                    'attempt': att, 'parse_retries': att - 1, 'via': 'batch'})
                summary['ok'] += 1
                return
            summary['parse_failed'] += 1
            reason, retryable = f'no valid criteria_met after {att} attempts', True
        else:
            retryable, message = payload
            self.store.append('attempts.jsonl', {**base, 'ok': False, 'error': message})
            summary['errors'] += 1
            reason = f'batch error: {message}'
        if not retryable or att >= self.max_attempts:
            self.store.append('failures.jsonl', {'task_id': tid, 'at': runner.now(), 'reason': reason, 'attempts': att,
                                                 'via': 'batch'})
            summary['gave_up'] += 1

    # ---------------- loop
    def run_until_done(self, poll_seconds=300, max_rounds=None, sleep=time.sleep, max_requests=None):
        rounds = 0
        while True:
            if not any(not c.get('collected') for c in self.state()['chunks']):
                if not self.pending():
                    break
                if max_rounds is not None and rounds >= max_rounds:
                    break
                self.prepare(max_requests)
                rounds += 1
            self.submit()
            while True:
                self.poll()
                if all(c['status'] in ('ended',) or c.get('collected') for c in self.state()['chunks']):
                    break
                sleep(poll_seconds)
            self.log(json.dumps(self.collect(), ensure_ascii=False))
        done = len(runner.read_jsonl(self.dir / 'results.jsonl'))
        failed = len(runner.read_jsonl(self.dir / 'failures.jsonl'))
        return {'tasks': len(self.tasks), 'done': done, 'failed': failed, 'rounds': rounds}


def verify_batch_inputs(run_dir, table):
    """Every batch input line must carry only the envelope keys, and its body must equal the request body
    stored for that task and chunk (which runner.verify_requests checks against the condition table)."""
    d = Path(run_dir)
    manifest = json.loads((d / 'manifest.json').read_text(encoding='utf-8'))
    m = table_model(table, manifest['model_id'])
    fam = FAMILY.get(m['provider'])
    stored = {(r.get('batch_chunk'), r.get('custom_id')): r for r in runner.read_jsonl(d / 'requests.jsonl') if r.get('batch_chunk')}
    allowed_body = set(m['send']) | {'input', 'messages', 'contents'}
    if m['provider'] == 'gemini_generate':
        allowed_body.discard('model')
    problems = []
    for p in sorted((d / 'batches').glob('*.input.jsonl')):
        chunk = p.name.replace('.input.jsonl', '')
        for line in runner.read_jsonl(p):
            extra = set(line) - ENVELOPE_KEYS[fam]
            if extra:
                problems.append(f'{chunk}: envelope keys {sorted(extra)}')
            cid = line.get('custom_id') or line.get('key')
            _, body = body_of(fam, line)
            ref = stored.get((chunk, cid))
            if ref is None:
                problems.append(f'{chunk}:{cid}: no stored request')
                continue
            if body != ref['body']:
                problems.append(f'{chunk}:{cid}: body differs from requests.jsonl')
            bad = set(body or {}) - allowed_body
            if bad:
                problems.append(f'{chunk}:{cid}: unexpected body keys {sorted(bad)}')
            if fam == 'openai' and line.get('url') != OPENAI_ENDPOINT[m['provider']]:
                problems.append(f'{chunk}:{cid}: url {line.get("url")}')
    return problems
