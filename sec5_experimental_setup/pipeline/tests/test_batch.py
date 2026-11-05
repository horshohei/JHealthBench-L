"""Offline tests for the batch-API path (hb/batch.py). A fake HTTP layer stands in for the providers.

uv run --no-project python -m unittest discover -s Scratch/2026-10-13/experiment_redo/pipeline/tests
"""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PIPE = HERE.parent
BASE = PIPE.parent
sys.path.insert(0, str(PIPE))

from hb import batch, conditions, runner, tasks  # noqa: E402

V3 = BASE / 'conditions' / 'condition_table_v3.json'
PILOT = ['04_03', '08_10', '09_02', '12_05', '08_07']
SECRET = 'sk-test-SECRET-do-not-store'
OK_TRUE = '```json\n{"explanation": "ok", "criteria_met": true}\n```'
OK_FALSE = '{"explanation": "no", "criteria_met": false}'
GARBAGE = 'I think it is fine.'


def raw_for(provider, text):
    if provider == 'openai_responses':
        return {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}], 'status': 'completed',
                'usage': {'input_tokens': 900, 'output_tokens': 40, 'input_tokens_details': {'cached_tokens': 800}}}
    if provider == 'anthropic_messages':
        return {'content': [{'type': 'text', 'text': text}], 'stop_reason': 'end_turn',
                'usage': {'input_tokens': 900, 'output_tokens': 40, 'cache_read_input_tokens': 800}}
    if provider == 'gemini_generate':
        return {'candidates': [{'content': {'parts': [{'text': text}]}, 'finishReason': 'STOP'}],
                'usageMetadata': {'promptTokenCount': 900, 'candidatesTokenCount': 40}}
    raise ValueError(provider)


class FakeAPI:
    """In-memory OpenAI / Anthropic / Gemini batch endpoints. `plan(cid, attempt)` picks each outcome:
    'true', 'false', 'garbage', 'retry' (retryable error), 'fatal' (invalid request), 'missing'."""

    def __init__(self, provider, plan, gemini_keys=True, gemini_file=False, openai_fail=False):
        self.provider, self.plan = provider, plan
        self.gemini_keys, self.gemini_file, self.openai_fail = gemini_keys, gemini_file, openai_fail
        self.files, self.batches, self.calls, self.seen = {}, {}, [], {}
        self.headers_seen = []

    def outcome(self, cid):
        self.seen[cid] = self.seen.get(cid, 0) + 1
        return self.plan(cid, self.seen[cid])

    def __call__(self, method, url, headers, data=None, timeout=600):
        self.calls.append((method, url))
        self.headers_seen.append(headers)
        if url.endswith('/v1/files') and method == 'POST':
            text = data.decode('utf-8')
            content = text.split('Content-Type: application/jsonl\r\n\r\n', 1)[1].rsplit('\r\n--', 1)[0]
            fid = f'file-{len(self.files)}'
            self.files[fid] = content
            return json.dumps({'id': fid}).encode()
        if url.endswith('/v1/batches') and method == 'POST':
            body = json.loads(data)
            bid = f'batch_{len(self.batches)}'
            self.batches[bid] = {'lines': [json.loads(x) for x in self.files[body['input_file_id']].splitlines() if x], 'polls': 0,
                                 'endpoint': body['endpoint']}
            return json.dumps({'id': bid, 'status': 'validating'}).encode()
        m = re.search(r'/v1/batches/(batch_\d+)$', url)
        if m:
            b = self.batches[m.group(1)]
            b['polls'] += 1
            if b['polls'] < 2:
                return json.dumps({'id': m.group(1), 'status': 'in_progress'}).encode()
            if self.openai_fail:
                return json.dumps({'id': m.group(1), 'status': 'failed', 'errors': {'data': [{'code': 'invalid_request'}]}}).encode()
            out, err = [], []
            for line in b['lines']:
                o = self.outcome(line['custom_id'])
                if o in ('true', 'false', 'garbage'):
                    text = {'true': OK_TRUE, 'false': OK_FALSE, 'garbage': GARBAGE}[o]
                    out.append({'custom_id': line['custom_id'], 'response': {'status_code': 200, 'body': raw_for(self.provider, text)}, 'error': None})
                elif o == 'retry':
                    err.append({'custom_id': line['custom_id'], 'response': {'status_code': 500, 'body': {'error': {'message': 'server'}}}, 'error': None})
                elif o == 'fatal':
                    err.append({'custom_id': line['custom_id'], 'response': {'status_code': 400, 'body': {'error': {'message': 'bad'}}}, 'error': None})
            self.files[f'out-{m.group(1)}'] = '\n'.join(json.dumps(x) for x in out)
            self.files[f'err-{m.group(1)}'] = '\n'.join(json.dumps(x) for x in err)
            return json.dumps({'id': m.group(1), 'status': 'completed', 'output_file_id': f'out-{m.group(1)}',
                               'error_file_id': f'err-{m.group(1)}'}).encode()
        m = re.search(r'/v1/files/([^/]+)/content$', url)
        if m:
            return self.files[m.group(1)].encode()
        if url.endswith('/v1/messages/batches') and method == 'POST':
            body = json.loads(data)
            bid = f'msgbatch_{len(self.batches)}'
            self.batches[bid] = {'lines': body['requests'], 'polls': 0}
            return json.dumps({'id': bid, 'processing_status': 'in_progress'}).encode()
        m = re.search(r'/v1/messages/batches/(msgbatch_\d+)$', url)
        if m:
            b = self.batches[m.group(1)]
            b['polls'] += 1
            if b['polls'] < 2:
                return json.dumps({'id': m.group(1), 'processing_status': 'in_progress', 'results_url': None}).encode()
            return json.dumps({'id': m.group(1), 'processing_status': 'ended',
                               'results_url': f'https://api.anthropic.com/v1/messages/batches/{m.group(1)}/results'}).encode()
        m = re.search(r'/v1/messages/batches/(msgbatch_\d+)/results$', url)
        if m:
            rows = []
            for line in self.batches[m.group(1)]['lines']:
                o = self.outcome(line['custom_id'])
                if o in ('true', 'false', 'garbage'):
                    text = {'true': OK_TRUE, 'false': OK_FALSE, 'garbage': GARBAGE}[o]
                    rows.append({'custom_id': line['custom_id'], 'result': {'type': 'succeeded', 'message': raw_for(self.provider, text)}})
                elif o == 'retry':
                    rows.append({'custom_id': line['custom_id'], 'result': {'type': 'errored', 'error': {'type': 'error', 'error': {'type': 'api_error'}}}})
                elif o == 'fatal':
                    rows.append({'custom_id': line['custom_id'], 'result': {'type': 'errored', 'error': {'type': 'error', 'error': {'type': 'invalid_request_error'}}}})
            return '\n'.join(json.dumps(r) for r in rows).encode()
        if ':batchGenerateContent' in url:
            body = json.loads(data)
            name = f'batches/{len(self.batches)}'
            self.batches[name] = {'lines': body['batch']['input_config']['requests']['requests'], 'polls': 0}
            return json.dumps({'name': name, 'metadata': {'state': 'JOB_STATE_PENDING'}}).encode()
        m = re.search(r'/v1beta/(batches/\d+)$', url)
        if m:
            b = self.batches[m.group(1)]
            b['polls'] += 1
            if b['polls'] < 2:
                return json.dumps({'name': m.group(1), 'metadata': {'state': 'JOB_STATE_RUNNING'}}).encode()
            rows = []
            for r in b['lines']:
                key = r['metadata']['key']
                o = self.outcome(key)
                row = {} if not self.gemini_keys else {'metadata': {'key': key}}
                if o in ('true', 'false', 'garbage'):
                    row['response'] = raw_for(self.provider, {'true': OK_TRUE, 'false': OK_FALSE, 'garbage': GARBAGE}[o])
                elif o == 'retry':
                    row['error'] = {'code': 13, 'message': 'internal'}
                elif o == 'fatal':
                    row['error'] = {'code': 3, 'message': 'invalid'}
                if o != 'missing':
                    rows.append(row)
            if self.gemini_file:
                self.files[f'files/res-{m.group(1)}'] = '\n'.join(json.dumps({**({'key': r['metadata']['key']} if 'metadata' in r else {}),
                                                                             **{k: v for k, v in r.items() if k != 'metadata'}}) for r in rows)
                return json.dumps({'name': m.group(1), 'metadata': {'state': 'JOB_STATE_SUCCEEDED',
                                                                    'output': {'responsesFile': f'files/res-{m.group(1)}'}}}).encode()
            return json.dumps({'name': m.group(1), 'done': True, 'metadata': {'state': 'JOB_STATE_SUCCEEDED'},
                               'response': {'inlinedResponses': {'inlinedResponses': rows}}}).encode()
        m = re.search(r'/download/v1beta/(files/[^:]+):download', url)
        if m:
            return self.files[m.group(1)].encode()
        raise AssertionError(f'unexpected call {method} {url}')


class BatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table = conditions.load_table(V3)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {'OPENAI_API_KEY': SECRET, 'ANTHROPIC_API_KEY': SECRET, 'GEMINI_API_KEY': SECRET}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def gold_run(self, model_id):
        out = Path(self.tmp.name) / model_id
        tasks.build_gold_run(self.table, model_id, out, repeats=1, case_labels=PILOT)
        return out

    def test_custom_id_is_valid_for_every_provider(self):
        ids = {batch.custom_id(f'gold:x:{i}:criteria_{i:03d}:r1') for i in range(2000)}
        self.assertEqual(len(ids), 2000)
        for c in ids:
            self.assertRegex(c, r'^[a-zA-Z0-9_-]{1,64}$')

    def test_prepare_lines_equal_sync_bodies_and_verify(self):
        for mid, env_keys in (('sol61_grader_ref', {'custom_id', 'method', 'url', 'body'}),
                              ('sonnet55_grader_low', {'custom_id', 'params'}),
                              ('gemini38flash_grader_low', {'key', 'request'})):
            run = self.gold_run(mid)
            br = batch.BatchRun(run, self.table, http=FakeAPI(self.table['models'][mid]['provider'], lambda c, a: 'true'))
            chunks = br.prepare()
            self.assertEqual(len(chunks), 1)
            lines = runner.read_jsonl(run / 'batches' / f'{chunks[0]}.input.jsonl')
            self.assertEqual(len(lines), 57)
            inputs = runner.Inputs(run)
            tk = {batch.custom_id(t['task_id']): t for t in runner.read_jsonl(run / 'tasks.jsonl')}
            for line in lines:
                self.assertEqual(set(line), env_keys)
                _, body = batch.body_of(batch.FAMILY[self.table['models'][mid]['provider']], line)
                _, _, sync_body = runner.build_request(self.table['models'][mid], tk[line.get('custom_id') or line.get('key')], inputs)
                self.assertEqual(body, sync_body)
            self.assertEqual(runner.verify_requests(run, self.table), [])
            self.assertEqual(batch.verify_batch_inputs(run, self.table), [])
            # tampering is detected
            p = run / 'batches' / f'{chunks[0]}.input.jsonl'
            first = json.loads(p.read_text().splitlines()[0])
            key = {'openai': 'body', 'anthropic': 'params', 'gemini': 'request'}[batch.FAMILY[self.table['models'][mid]['provider']]]
            first[key]['temperature'] = 0
            p.write_text(json.dumps(first) + '\n' + '\n'.join(p.read_text().splitlines()[1:]) + '\n')
            problems = batch.verify_batch_inputs(run, self.table)
            self.assertTrue(any('differs' in x for x in problems) and any('unexpected body keys' in x for x in problems), problems)
            with self.assertRaises(RuntimeError):
                br.submit()

    def test_chunking_by_count(self):
        run = self.gold_run('sonnet55_grader_nothink')
        br = batch.BatchRun(run, self.table, http=FakeAPI('anthropic_messages', lambda c, a: 'true'))
        self.assertEqual(len(br.prepare(max_requests=20)), 3)
        self.assertEqual(len(br.pending()), 0)

    def _cycle(self, mid, **fake_kw):
        run = self.gold_run(mid)
        prov = self.table['models'][mid]['provider']
        cids = sorted(batch.custom_id(t['task_id']) for t in runner.read_jsonl(run / 'tasks.jsonl'))
        always_bad, fatal, flaky, missing_once = cids[0], cids[1], cids[2], cids[3]

        def plan(cid, attempt):
            if cid == always_bad:
                return 'garbage'
            if cid == fatal:
                return 'fatal'
            if cid == flaky:
                return 'retry' if attempt == 1 else 'false'
            if cid == missing_once and attempt == 1 and prov == 'gemini_generate':
                return 'missing'
            return 'garbage' if attempt == 1 and cid[1] in '01' else 'true'

        fake = FakeAPI(prov, plan, **fake_kw)
        br = batch.BatchRun(run, self.table, http=fake, log=lambda *a: None)
        res = br.run_until_done(poll_seconds=0, sleep=lambda s: None)
        self.assertEqual(res['done'] + res['failed'], 57)
        self.assertEqual(res['failed'], 2)
        fails = {r['task_id']: r for r in runner.read_jsonl(run / 'failures.jsonl')}
        att = {}
        for a in runner.read_jsonl(run / 'attempts.jsonl'):
            att.setdefault(a['task_id'], []).append(a)
        by_cid = {batch.custom_id(t): t for t in att}
        self.assertEqual(len(att[by_cid[always_bad]]), 10)  # capped at the approved 10 attempts
        self.assertEqual(len(att[by_cid[fatal]]), 1)        # invalid requests are not resubmitted
        self.assertIn(by_cid[always_bad], fails)
        self.assertIn(by_cid[fatal], fails)
        self.assertEqual(len(att[by_cid[flaky]]), 2)
        self.assertEqual(runner.verify_requests(run, self.table), [])
        self.assertEqual(batch.verify_batch_inputs(run, self.table), [])
        # results look like the synchronous runner's, so gold-metrics works
        res_rows = runner.read_jsonl(run / 'results.jsonl')
        self.assertTrue(all(isinstance(r['criteria_met'], bool) for r in res_rows))
        self.assertEqual(len({r['task_id'] for r in res_rows}), len(res_rows))
        # no API key anywhere on disk; no auth header stored
        for p in run.rglob('*'):
            if p.is_file():
                self.assertNotIn(SECRET, p.read_text(encoding='utf-8', errors='ignore'), p)
        return run, fake

    def test_full_cycle_openai(self):
        run, fake = self._cycle('sol61_grader_ref')
        self.assertTrue(any(u.endswith('/v1/batches') for _, u in fake.calls))
        self.assertTrue(all(b['endpoint'] == '/v1/responses' for b in fake.batches.values()))

    def test_full_cycle_anthropic(self):
        run, fake = self._cycle('sonnet55_grader_low')
        st = json.loads((run / 'batches' / 'state.json').read_text())
        self.assertGreaterEqual(len(st['chunks']), 2)  # retries went into later rounds
        self.assertTrue(all(h.get('anthropic-version') for h in fake.headers_seen if 'x-api-key' in h))

    def test_full_cycle_gemini_inline_with_keys(self):
        self._cycle('gemini38flash_grader_low')

    def test_full_cycle_gemini_inline_without_keys_uses_order(self):
        run = self.gold_run('gemini38flash_grader_default')
        fake = FakeAPI('gemini_generate', lambda c, a: 'false', gemini_keys=False)
        br = batch.BatchRun(run, self.table, http=fake, log=lambda *a: None)
        res = br.run_until_done(poll_seconds=0, sleep=lambda s: None)
        self.assertEqual(res['done'], 57)
        self.assertTrue(all(r['criteria_met'] is False for r in runner.read_jsonl(run / 'results.jsonl')))

    def test_gemini_results_file(self):
        run = self.gold_run('gemini38flash_grader_default')
        fake = FakeAPI('gemini_generate', lambda c, a: 'true', gemini_file=True)
        res = batch.BatchRun(run, self.table, http=fake, log=lambda *a: None).run_until_done(poll_seconds=0, sleep=lambda s: None)
        self.assertEqual(res['done'], 57)
        self.assertTrue(any(':download' in u for _, u in fake.calls))

    def test_openai_failed_batch_is_not_resubmitted(self):
        run = self.gold_run('sol61_grader_ref')
        fake = FakeAPI('openai_responses', lambda c, a: 'true', openai_fail=True)
        res = batch.BatchRun(run, self.table, http=fake, log=lambda *a: None).run_until_done(poll_seconds=0, sleep=lambda s: None)
        self.assertEqual((res['done'], res['failed'], res['rounds']), (0, 57, 1))

    def test_answer_runs_can_be_batched(self):
        out = Path(self.tmp.name) / 'ans'
        tasks.build_answer_run(self.table, 'sonnet55', out, ['T', 'L'], PILOT, 1)
        fake = FakeAPI('anthropic_messages', lambda c, a: 'true')
        res = batch.BatchRun(out, self.table, http=fake, log=lambda *a: None).run_until_done(poll_seconds=0, sleep=lambda s: None)
        self.assertEqual(res['done'], 7)
        self.assertTrue(all(r['text'] == OK_TRUE for r in runner.read_jsonl(out / 'results.jsonl')))

    def test_local_models_and_changed_tables_are_refused(self):
        run = self.gold_run('qwen38_grader_none')
        with self.assertRaises(RuntimeError):
            batch.BatchRun(run, self.table)
        run2 = self.gold_run('sonnet55_grader_low')
        t2 = dict(self.table)
        t2['_sha256'] = '0' * 64
        with self.assertRaises(RuntimeError):
            batch.BatchRun(run2, t2)


if __name__ == '__main__':
    unittest.main()
