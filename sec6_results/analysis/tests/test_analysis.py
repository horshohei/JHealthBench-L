"""Tests of analysis/hb_analysis.py on synthetic and dummy data only (no real results).

uv run --no-project python -m unittest discover -s analysis/tests
"""
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import hb_analysis as A  # noqa: E402
from hb.scoring import calculate_score  # noqa: E402

DUMMY = HERE.parent / 'dummy_runs'


def write_jsonl(p, rows):
    p.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')


def mini_data(tmp):
    """3 cases: c1, c2 with unchanged conversations, c3 changed. T has a deleted item in c1; L has an added item in c2
    and a points change in c1."""
    d = Path(tmp) / 'data'
    d.mkdir()
    cases = {'c1': '01_01', 'c2': '01_02', 'c3': '01_03'}
    T = {'c1': [('i1', 5), ('i2', 3), ('i3', -4), ('i4', 2)], 'c2': [('i1', 4), ('i2', 4)], 'c3': [('i1', 6), ('i2', -2)]}
    L = {'c1': [('i1', 5), ('i2', 6), ('i3', -4)], 'c2': [('i1', 4), ('i2', 4), ('a1', 3)], 'c3': [('i1', 6), ('i2', -2)]}
    changed = {('c1', 'i2'), ('c1', 'i3'), ('c2', 'i2'), ('c3', 'i1')}
    for name, src in (('T', T), ('L', L)):
        write_jsonl(d / f'{name}.jsonl', [{'prompt_id': c, 'case_label': cases[c], 'example_tags': ['theme:x'],
                                           'rubrics': [{'criterion_id': i, 'points': p, 'tags': ['axis:accuracy']} for i, p in its]}
                                          for c, its in src.items()])
    write_jsonl(d / 'case_map.jsonl', [{'case_id': c, 'case_label': lab, 'turns_changed_T_vs_L': [1] if c == 'c3' else [],
                                        'workbook_group': 'g', 'specialty': 's', 'scenario': 'x', 'source_dataset': 'healthbench',
                                        'example_tags': ['theme:x'], 'original_language': 'en'} for c, lab in cases.items()])
    cm = []
    for c in cases:
        ids = [i for i, _ in T[c]] + [i for i, _ in L[c] if i not in dict(T[c])]
        for i in ids:
            inT, inL = i in dict(T[c]), i in dict(L[c])
            cm.append({'case_id': c, 'case_label': cases[c], 'criterion_id': i,
                       'row_type': 'original' if inT else 'added_metareview_from_grader', 'deleted_in_L': inT and not inL,
                       'text_changed_T_vs_L': ((c, i) in changed) if inT else None, 'text_T': f'T {c} {i}' if inT else None,
                       'text_L': f'L {c} {i}' if inL else None})
    write_jsonl(d / 'criteria_map.jsonl', cm)
    return d, T, L


def mini_run(tmp, d, T, L, name, grade_fn, drop=()):
    """One grade run: models m1, m2; 2 samples; unchanged cases share the answer under T and L."""
    run = Path(tmp) / name
    run.mkdir()
    sha = {c: A.sha256_file(d / f'{c}.jsonl') for c in A.CONDS}
    (run / 'manifest.json').write_text(json.dumps({'kind': 'grade', 'model_id': 'g1', 'dataset_sha256': sha}))
    tasks, res = [], []
    for m in ('m1', 'm2'):
        for c, lab in (('c1', '01_01'), ('c2', '01_02'), ('c3', '01_03')):
            for s in (1, 2):
                for cond, src in (('T', T), ('L', L)):
                    akey = f'{m}|{c}|s{s}' + ('|L' if c == 'c3' and cond == 'L' else '')
                    for i, p in src[c]:
                        tid = f'{m}:{c}:{s}:{cond}:{i}'
                        tasks.append({'task_id': tid, 'condition': cond, 'case_id': c, 'case_label': lab, 'answer_model': m,
                                      'answer_key': akey, 'sample': s, 'repeat': 1, 'criterion_id': i, 'points': p})
                        if tid not in drop:
                            res.append({'task_id': tid, 'criteria_met': grade_fn(m, c, s, cond, i)})
    write_jsonl(run / 'tasks.jsonl', tasks)
    write_jsonl(run / 'results.jsonl', res)
    return run


class TestBasics(unittest.TestCase):
    def test_case_score_matches_simple_evals(self):
        rng = random.Random(1)
        for _ in range(200):
            items = [{'points': rng.choice([-8, -3, 1, 2, 5, 9])} for _ in range(rng.randint(1, 8))]
            met = [rng.random() < 0.5 for _ in items]
            self.assertEqual(A.case_score(items, met), calculate_score(items, met))

    def test_kendall(self):
        self.assertAlmostEqual(A.kendall_tau_b([1, 2, 3, 4], [1, 2, 3, 4]), 1.0)
        self.assertAlmostEqual(A.kendall_tau_b([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        self.assertAlmostEqual(A.kendall_tau_b([1, 2, 3], [1, 3, 2]), 1 / 3)
        self.assertEqual(A.ranks({'a': 0.5, 'b': 0.7, 'c': 0.5}), {'b': 1, 'a': 2.5, 'c': 2.5})

    def test_shapley_linear_equals_bruteforce(self):
        rng = random.Random(7)
        for _ in range(20):
            labels = ['noise', 'text:E', 'text:G']
            b = {T: rng.uniform(-1, 1) for T in A.subsets(A.STRUCT)}
            G = {f: {T: rng.uniform(-1, 1) for T in A.subsets(A.STRUCT)} for f in labels}
            fast = A.shapley_linear(b, G)
            brute = A.shapley_bruteforce(labels + list(A.STRUCT), lambda S: A.value_linear(b, G, S))
            for k in brute:
                self.assertAlmostEqual(fast[k], brute[k], places=12)
            total = A.value_linear(b, G, set(labels) | set(A.STRUCT)) - b[frozenset()]
            self.assertAlmostEqual(sum(fast.values()), total, places=12)

    def test_bootstrap_draws_reproducible(self):
        ids = [f'c{i}' for i in range(30)]
        self.assertEqual(A.draws_for(ids, 50, 3, 'x'), A.draws_for(ids, 50, 3, 'x'))
        self.assertNotEqual(A.draws_for(ids, 50, 3, 'x'), A.draws_for(ids, 50, 3, 'y'))
        self.assertNotEqual(A.draws_for(ids, 50, 3, 'x'), A.draws_for(ids, 50, 4, 'x'))


class TestRubricComponents(unittest.TestCase):
    items_T = [{'criterion_id': 'a', 'points': 5}, {'criterion_id': 'b', 'points': 3}, {'criterion_id': 'c', 'points': -4},
               {'criterion_id': 'd', 'points': 2}]
    items_L = [{'criterion_id': 'a', 'points': 5}, {'criterion_id': 'b', 'points': 6}, {'criterion_id': 'c', 'points': -4},
               {'criterion_id': 'e', 'points': 4}]
    factor = staticmethod(lambda c: 'noise' if c == 'a' else 'text:all')

    def test_endpoints_and_efficiency(self):
        labT = {'a': True, 'b': False, 'c': True, 'd': True}
        labL = {'a': False, 'b': True, 'c': False, 'e': True}
        b, G = A.rubric_case_components(self.items_T, self.items_L, labT, labL, self.factor)
        vT = A.case_score(self.items_T, [labT[x['criterion_id']] for x in self.items_T])
        vL = A.case_score(self.items_L, [labL[x['criterion_id']] for x in self.items_L])
        self.assertAlmostEqual(b[frozenset()], vT)
        self.assertAlmostEqual(A.value_linear(b, G, set(G) | set(A.STRUCT)), vL)
        phi = A.shapley_linear(b, G)
        self.assertAlmostEqual(sum(phi.values()), vL - vT)

    def test_only_deletion(self):
        items_L = [x for x in self.items_T if x['criterion_id'] != 'd']
        lab = {'a': True, 'b': True, 'c': False, 'd': False}
        b, G = A.rubric_case_components(self.items_T, items_L, lab, lab, self.factor)
        phi = A.shapley_linear(b, G)
        vT = A.case_score(self.items_T, [lab[x['criterion_id']] for x in self.items_T])
        vL = A.case_score(items_L, [lab[x['criterion_id']] for x in items_L])
        self.assertAlmostEqual(phi['deleted'], vL - vT)
        for k in ('noise', 'text:all', 'points', 'added'):
            self.assertAlmostEqual(phi[k], 0.0)

    def test_only_relabel(self):
        labT = {'a': True, 'b': False, 'c': False, 'd': True}
        labL = {'a': True, 'b': True, 'c': False, 'd': True}
        items_L = self.items_T
        b, G = A.rubric_case_components(self.items_T, items_L, labT, labL, self.factor)
        phi = A.shapley_linear(b, G)
        self.assertAlmostEqual(phi['text:all'], 3 / 10)
        self.assertAlmostEqual(sum(v for k, v in phi.items() if k != 'text:all'), 0.0)


class TestMiniRuns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d, self.T, self.L = mini_data(self.tmp.name)
        self.data = A.Data(self.d)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_and_shared(self):
        grade = lambda m, c, s, cond, i: (len(m + c + cond + i) + s) % 3 == 0  # noqa: E731
        run = mini_run(self.tmp.name, self.d, self.T, self.L, 'r1', grade, drop={'m1:c2:1:L:a1'})
        G = A.Grades([run], self.data)
        self.assertIsNone(G.scores[('m1', 'L', 'c2', 1)])
        self.assertIsNotNone(G.scores[('m1', 'T', 'c2', 1)])
        self.assertTrue(G.shared('m1', 'c1'))
        self.assertFalse(G.shared('m1', 'c3'))
        tab, _ = A.score_table(G, G.models, self.data.case_ids, A.draws_for(self.data.case_ids, 10, 1, 'x'))
        row = {x['model']: x for x in tab['rows']}['m1']
        self.assertEqual((row['n_undefined_L'], row['n_case_samples_L']), (1, 5))
        miss = A.missing_report(G, self.data)
        self.assertEqual([(x['model'], x['condition'], x['case_label'], x['sample']) for x in miss], [('m1', 'L', '01_02', 1)])
        dec = A.rubric_decomposition(G, self.data, G.models, ['c1', 'c2'], A.draws_for(['c1', 'c2'], 10, 1, 'r'))
        r1 = {x['model']: x for x in dec['rows']}['m1']
        self.assertEqual(r1['n_excluded'], 1)
        self.assertEqual(r1['n_case_samples'], 3)
        self.assertLess(abs(r1['shapley_sum_check']), 1e-9)

    def test_score_definition_clips_mean_only(self):
        # all negative items met, positives unmet -> negative case scores; the model mean is clipped to 0 once
        grade = lambda m, c, s, cond, i: i in ('i3',) or (c == 'c3' and i == 'i2')  # noqa: E731
        run = mini_run(self.tmp.name, self.d, self.T, self.L, 'r2', grade)
        G = A.Grades([run], self.data)
        tab, _ = A.score_table(G, G.models, self.data.case_ids, A.draws_for(self.data.case_ids, 5, 1, 'x'))
        row = tab['rows'][0]
        self.assertEqual(row['S_T'], 0.0)
        self.assertLess(row['mean_unclipped_T'], 0)
        self.assertTrue(row['clipping_changed'])

    def test_duplicate_grades_rejected(self):
        grade = lambda *a: True  # noqa: E731
        r1 = mini_run(self.tmp.name, self.d, self.T, self.L, 'ra', grade)
        r2 = mini_run(self.tmp.name, self.d, self.T, self.L, 'rb', grade)
        with self.assertRaises(ValueError):
            A.Grades([r1, r2], self.data)

    def test_sha_mismatch_rejected(self):
        run = mini_run(self.tmp.name, self.d, self.T, self.L, 'rs', lambda *a: True)
        man = json.loads((run / 'manifest.json').read_text())
        man['dataset_sha256']['T'] = '0' * 64
        (run / 'manifest.json').write_text(json.dumps(man))
        with self.assertRaises(ValueError):
            A.Grades([run], self.data)


@unittest.skipUnless((DUMMY / 'truth.json').exists(), 'run analysis/make_dummy.py first')
class TestDummyRecovery(unittest.TestCase):
    """The embedded effects of make_dummy.py are recovered (sampled grades vs expected grades)."""

    @classmethod
    def setUpClass(cls):
        main = sorted(str(p) for p in (DUMMY / 'main').iterdir())
        sec = sorted(str(p) for p in (DUMMY / 'secondary').iterdir())
        emain = sorted(str(p) for p in (DUMMY / 'expected' / 'main').iterdir())
        esec = sorted(str(p) for p in (DUMMY / 'expected' / 'secondary').iterdir())
        lab, conv = HERE.parent / 'labels_dummy.jsonl', HERE.parent / 'conv_labels_dummy.jsonl'
        cls.est = A.run_all(A.BASE / 'data', main, sec, lab, conv, n_boot=300)
        cls.exp = A.run_all(A.BASE / 'data', emain, esec, lab, conv, n_boot=10)
        cls.truth = json.loads((DUMMY / 'truth.json').read_text(encoding='utf-8'))

    def test_delta_loc_recovered(self):
        exp = {x['model']: x['delta_loc'] for x in self.exp['overall']['rows']}
        inside = sum(x['delta_loc_ci'][0] <= exp[x['model']] <= x['delta_loc_ci'][1] for x in self.est['overall']['rows'])
        self.assertGreaterEqual(inside, 12)  # 15 models, nominal 95%
        lo, hi = self.est['overall']['mean_delta_loc_ci']
        self.assertTrue(lo <= self.exp['overall']['mean_delta_loc'] <= hi)

    def test_conversation_effect_recovered(self):
        ch = {x['model']: x for x in self.est['by_conversation']['changed']['rows']}
        un = {x['model']: x for x in self.est['by_conversation']['unchanged']['rows']}
        self.assertGreater(ch['llmjp41_8b']['delta_loc'] - un['llmjp41_8b']['delta_loc'], 0.03)   # embedded +0.6 logit
        self.assertLess(ch['sol61']['delta_loc'] - un['sol61']['delta_loc'], -0.03)              # embedded -0.6 logit

    def test_change_type_components(self):
        est = self.est['delta_rubric']['mean_over_models']['shapley']
        exp = self.exp['delta_rubric']['mean_over_models']['shapley']
        self.assertGreater(est['text:E']['ci'][0], 0)   # code E: +0.7 logit in L
        self.assertLess(est['text:G']['ci'][1], 0)      # code G: -0.5 logit in L
        for f, v in est.items():
            self.assertTrue(v['ci'][0] - 0.002 <= exp[f]['value'] <= v['ci'][1] + 0.002, f)
        for x in self.est['delta_rubric']['rows']:
            self.assertLess(abs(x['shapley_sum_check']), 1e-9)

    def test_noise_baseline(self):
        got = self.est['item_disagreement']['classes']['identical']['__pooled__']['disagreement']
        self.assertAlmostEqual(got, self.truth['expected_disagreement']['identical'], delta=0.01)

    def test_missing_reported(self):
        sip = [(x['condition'], x['case_label'], x['sample']) for x in self.est['missing'] if x['model'] == 'sip_jmed_llm3_13b']
        self.assertIn(('T', '12_05', 2), sip)
        self.assertIn(('L', '12_05', 2), sip)
        gm = [x for x in self.est['missing'] if x['model'] == 'gemma4_31b']
        self.assertGreaterEqual(len(gm), 1)

    def test_grader_comparison(self):
        gc = self.est['grader_comparison']
        self.assertEqual(gc['samples'], [1])
        rows = {x['model']: x for x in gc['rows']}
        self.assertTrue(rows['qwen38_27b']['self_grading_main'])
        self.assertTrue(rows['gemini38flash']['self_grading_secondary'])
        self.assertGreater(gc['item_agreement']['__pooled__']['agreement'], 0.8)


if __name__ == '__main__':
    unittest.main()
