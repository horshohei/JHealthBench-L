"""Offline tests for condition_table_v2.json (no network).

uv run --no-project python -m unittest discover -s Scratch/2026-10-13/experiment_redo/pipeline/tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPE = HERE.parent
BASE = PIPE.parent
sys.path.insert(0, str(PIPE))

from hb import conditions, prompting, providers, runner, tasks  # noqa: E402

V2 = BASE / 'conditions' / 'condition_table_v2.json'
SAMPLING = ('temperature', 'top_p', 'top_k', 'min_p', 'presence_penalty', 'repetition_penalty', 'max_tokens')
PILOT = ['04_03', '08_10', '09_02', '12_05', '08_07']


class ConditionTableV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table = conditions.load_table(V2)

    def test_conditions_are_t_and_l_only(self):
        self.assertEqual(set(self.table['datasets']), {'T', 'L', 'GOLD'})
        ids = set(self.table['models'])
        for gone in ('gpt41_original', 'gpt54_low', 'haiku55'):
            self.assertNotIn(gone, ids)

    def test_lab_models_send_every_sampling_value(self):
        for mid, m in self.table['models'].items():
            if m['provider'] != 'vllm_chat':
                continue
            for k in SAMPLING:
                self.assertIn(k, m['send'], f'{mid} lacks {k}')
                self.assertIsInstance(m['send'][k], (int, float), f'{mid}.{k}')
            self.assertTrue('chat_template_kwargs' in m['send'] or 'reasoning_effort' in m['send'], mid)
            params = {b['param'] for b in m['basis']}
            for k in m['send']:
                if k != 'model':
                    self.assertIn(k, params, f'{mid}: {k} has no basis')

    def test_recommended_values(self):
        s = self.table['models']
        self.assertEqual({k: s['qwen38_27b']['send'][k] for k in SAMPLING[:6]},
                         {'temperature': 1.0, 'top_p': 0.95, 'top_k': 20, 'min_p': 0.0,
                          'presence_penalty': 0.0, 'repetition_penalty': 1.0})
        self.assertEqual(s['qwen38_27b']['send']['chat_template_kwargs'], {'enable_thinking': True, 'reasoning_effort': 'xhigh'})
        none = s['qwen38_grader_none']['send']
        self.assertEqual((none['temperature'], none['top_p'], none['presence_penalty']), (0.7, 0.8, 1.5))
        self.assertEqual(none['chat_template_kwargs'], {'enable_thinking': False})
        self.assertEqual(s['gptoss120b_grader_low']['send']['reasoning_effort'], 'low')
        self.assertEqual((s['deepseek_v4_flash_think']['send']['temperature'], s['deepseek_v4_flash_think']['send']['top_p']), (1.0, 1.0))
        self.assertEqual(s['deepseek_v4_flash_nothink']['send']['chat_template_kwargs'], {'thinking': False})
        self.assertEqual(s['llmjp41_33b']['send']['max_tokens'], 65536 - 16384)

    def test_commercial_models_send_no_sampling(self):
        for mid, m in self.table['models'].items():
            if m['provider'] == 'vllm_chat':
                continue
            for k in ('temperature', 'top_p', 'top_k'):
                self.assertNotIn(k, m['send'], mid)
        self.assertEqual(self.table['models']['haiku55_grader_nothink']['send']['thinking'], {'type': 'disabled'})
        self.assertEqual(self.table['models']['haiku55_grader_low']['send']['output_config'], {'effort': 'low'})

    def test_no_forbidden_server_flags(self):
        for mid, m in self.table['models'].items():
            args = ' '.join((m.get('server') or {}).get('args', []))
            self.assertNotIn('--generation-config', args, mid)
            self.assertNotIn('--no-enable-prefix-caching', args, mid)

    def test_pilot_gold_run_bodies(self):
        with tempfile.TemporaryDirectory() as d:
            for mid in ('qwen38_grader_none', 'gptoss120b_grader_medium', 'deepseek_v4_flash_think', 'haiku55_grader_low'):
                out = Path(d) / mid
                man = tasks.build_gold_run(self.table, mid, out, repeats=1, case_labels=PILOT)
                self.assertEqual(man['n_tasks'], 57)
                reqs = runner.dry_run(out, self.table)
                m = self.table['models'][mid]
                for r in reqs:
                    body = r['body']
                    extra = set(body) - set(m['send']) - {'messages', 'input', 'contents'}
                    self.assertFalse(extra, f'{mid}: {extra}')
                    for k, v in m['send'].items():
                        self.assertEqual(body[k], v)
                # dry-run bodies equal what verify would rebuild
                Path(out / 'requests.jsonl').write_text(
                    ''.join(json.dumps({'task_id': r['task_id'], 'body': r['body']}, ensure_ascii=False) + '\n' for r in reqs))
                self.assertEqual(runner.verify_requests(out, self.table), [])

    def test_pilot_answer_run_t_l_dedupe(self):
        with tempfile.TemporaryDirectory() as d:
            man = tasks.build_answer_run(self.table, 'qwen38_27b', Path(d) / 'a', ['T', 'L'], PILOT, 1)
            self.assertEqual(man['n_tasks'], man['unique_conversations'])
            self.assertTrue(5 <= man['unique_conversations'] <= 10)
            req = runner.dry_run(Path(d) / 'a', self.table)[0]['body']
            self.assertNotIn('system', json.dumps([m['role'] for m in req['messages']]))


if __name__ == '__main__':
    unittest.main()
