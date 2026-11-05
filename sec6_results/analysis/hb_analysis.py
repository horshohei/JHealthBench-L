"""Aggregation for the redo experiment (analysis_plan.md, T vs L). Reads grading run directories only; sends nothing.

Score definition = pipeline/hb/scoring.py (simple-evals healthbench_eval):
  case score  = sum(points of met items, negative items included) / sum(positive points)   (no per-case clipping)
  model score = mean over all (case, sample) case scores, then clipped ONCE to [0, 1]          (scoring.clipped_mean)
A case-sample with any missing grade has no score; it is counted and reported, never treated as "not met".
Differences (Delta_loc, Delta_rubric) are differences of the clipped model scores; decompositions (Shapley) use
the unclipped means, and the output says whether clipping changed any value.

Labels may be booleans (real runs) or probabilities in [0, 1] (expected-value dummy runs); every statistic that is
linear in the labels then gives its expectation.
"""
import hashlib
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'pipeline'))
from hb.scoring import clipped_mean  # noqa: E402  (same clipping as run.py score)

SEED = 20261013
N_BOOT = 2000
STRUCT = ('points', 'deleted', 'added')
NOISE = 'noise'
CONDS = ('T', 'L')

# Vendor of each answer model and grader, for marking self-grading (same vendor as the grader).
VENDOR = {'qwen38_27b': 'alibaba', 'qwen36_35b_a3b': 'alibaba', 'qwen38_flash_next': 'alibaba',
          'gemini38flash': 'google', 'gemma4_31b': 'google',
          'opus55': 'anthropic', 'sonnet55_medium': 'anthropic', 'sonnet55': 'anthropic', 'haiku55_answer': 'anthropic',
          'sol61': 'openai', 'luna6': 'openai', 'gptoss120b': 'openai',
          'deepseek_v4_flash_think': 'deepseek', 'llmjp41_33b': 'llm-jp', 'llmjp41_8b': 'llm-jp', 'llmjp41_32b_a3b': 'llm-jp',
          'sip_jmed_llm3_13b': 'sip', 'llmjp4_8b_instruct': 'llm-jp', 'llmjp4_8b_thinking': 'llm-jp',
          'qwen3_8b': 'alibaba', 'qwen3_32b': 'alibaba',
          # Swallow is continued pre-training of Qwen3 by Science Tokyo; marked as its own vendor, base noted in the paper
          'swallow_8b_sft': 'swallow', 'swallow_8b_rl': 'swallow', 'swallow_32b_sft': 'swallow', 'swallow_32b_rl': 'swallow',
          'weblab_medllm_gptoss120b_gguf': 'weblab'}
GRADER_VENDOR = {'qwen38_grader_none': 'alibaba', 'qwen38_grader_low': 'alibaba', 'qwen38_grader_medium': 'alibaba',
                 'gemini38flash_grader_low': 'google', 'gemini38flash_grader_default': 'google',
                 'sonnet55_grader_nothink': 'anthropic', 'sonnet55_grader_low': 'anthropic', 'opus55_grader_ref': 'anthropic',
                 'sol61_grader_ref': 'openai'}


# ------------------------------------------------------------------ small helpers
def read_jsonl(p):
    with open(p, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def case_score(items, met):
    """simple-evals calculate_score, linear in the labels (equal to scoring.calculate_score for booleans)."""
    total = sum(r['points'] for r in items if r['points'] > 0)
    if total == 0:
        return None
    return sum(r['points'] * float(m) for r, m in zip(items, met, strict=True)) / total


def clip(x):
    return None if x is None else min(1.0, max(0.0, x))


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def quantile(sorted_vals, q):
    """Linear interpolation (numpy 'linear')."""
    if not sorted_vals:
        return None
    pos = (len(sorted_vals) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def ci95(vals):
    v = sorted(x for x in vals if x is not None)
    return [quantile(v, 0.025), quantile(v, 0.975)] if v else None


def r(x, k=4):
    return None if x is None else round(x, k)


def kendall_tau_b(x, y):
    conc = disc = tx = ty = 0
    n = len(x)
    for i in range(n):
        for j in range(i + 1, n):
            dx = (x[i] > x[j]) - (x[i] < x[j])
            dy = (y[i] > y[j]) - (y[i] < y[j])
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                tx += 1
            elif dy == 0:
                ty += 1
            elif dx == dy:
                conc += 1
            else:
                disc += 1
    den = math.sqrt((conc + disc + tx) * (conc + disc + ty))
    return (conc - disc) / den if den else None


def ranks(scores):
    """{name: score} -> {name: rank}, 1 = best, ties get the average rank."""
    order = sorted(scores, key=lambda k: -scores[k])
    out, i = {}, 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for k in order[i:j + 1]:
            out[k] = (i + j) / 2 + 1
        i = j + 1
    return out


def draws_for(case_ids, n_boot, seed, name):
    """Case-level bootstrap draws, reproducible per (seed, name) (random.Random with a str seed is deterministic)."""
    rng = random.Random(f'{seed}:{name}')
    k = len(case_ids)
    return [[case_ids[rng.randrange(k)] for _ in range(k)] for _ in range(n_boot)]


def subsets(xs):
    for k in range(len(xs) + 1):
        yield from (frozenset(c) for c in combinations(xs, k))


# ------------------------------------------------------------------ Shapley
def shapley_bruteforce(players, v):
    """Exact Shapley values by enumerating subsets; v: frozenset -> float."""
    n = len(players)
    phi = {}
    for p in players:
        others = [q for q in players if q != p]
        tot = 0.0
        for k in range(n):
            w = math.factorial(k) * math.factorial(n - k - 1) / math.factorial(n)
            for S in combinations(others, k):
                S = frozenset(S)
                tot += w * (v(S | {p}) - v(S))
        phi[p] = tot
    return phi


def shapley_linear(b, G, struct=STRUCT):
    """Exact Shapley values when v(A u T) = b[T] + sum_{f in A} G[f][T] (A = label factors, T = structural factors).

    Label factors do not interact with each other (they only change numerators that share a denominator), so:
      phi_f = sum_T t!(m-t)!/(m+1)! G_f[T]
      phi_s = sum_{T in others} [ t!(m-1-t)!/m! (b[T+s]-b[T]) + sum_f (t+1)!(m-1-t)!/(m+1)! (G_f[T+s]-G_f[T]) ]
    (m structural factors; checked against shapley_bruteforce in the tests).
    """
    m = len(struct)
    F = math.factorial
    phi = {}
    for f, g in G.items():
        phi[f] = sum(F(len(T)) * F(m - len(T)) / F(m + 1) * g[T] for T in subsets(struct))
    for s in struct:
        others = [x for x in struct if x != s]
        val = 0.0
        for T in subsets(others):
            t, Ts = len(T), T | {s}
            val += F(t) * F(m - 1 - t) / F(m) * (b[Ts] - b[T])
            wl = F(t + 1) * F(m - 1 - t) / F(m + 1)
            for g in G.values():
                val += wl * (g[Ts] - g[T])
        phi[s] = val
    return phi


def value_linear(b, G, S, struct=STRUCT):
    T = frozenset(x for x in S if x in struct)
    return b[T] + sum(g[T] for f, g in G.items() if f in S)


def rubric_case_components(items_T, items_L, lab_T, lab_L, factor_of, struct=STRUCT):
    """Components of the T->L rubric change for ONE answer graded under both rubrics.

    items_T / items_L: [{'criterion_id', 'points'}] of the case; lab_T / lab_L: {criterion_id: label}.
    factor_of(criterion_id) -> label factor of a matched item ('noise' or 'text:<group>').
    Hybrid rubric for structural set T: matched items weighted with L points if 'points' in T else T points;
    T-only items kept unless 'deleted' in T; L-only items added if 'added' in T. A label factor f in A swaps the
    T labels of its matched items for their L labels. Returns (b, G) with v(A u T) = b[T] + sum_{f in A} G[f][T],
    or None when a denominator is zero. v(empty) = T score, v(all) = L score.
    """
    pT = {x['criterion_id']: x['points'] for x in items_T}
    pL = {x['criterion_id']: x['points'] for x in items_L}
    matched = [c for c in pT if c in pL]
    t_only = [c for c in pT if c not in pL]
    l_only = [c for c in pL if c not in pT]
    factors = sorted({factor_of(c) for c in matched})
    b, G = {}, {f: {} for f in factors}
    for T in subsets(struct):
        w = pL if 'points' in T else pT
        den = sum(w[c] for c in matched if w[c] > 0)
        num = sum(w[c] * float(lab_T[c]) for c in matched)
        if 'deleted' not in T:
            den += sum(pT[c] for c in t_only if pT[c] > 0)
            num += sum(pT[c] * float(lab_T[c]) for c in t_only)
        if 'added' in T:
            den += sum(pL[c] for c in l_only if pL[c] > 0)
            num += sum(pL[c] * float(lab_L[c]) for c in l_only)
        if den == 0:
            return None
        b[T] = num / den
        for f in factors:
            G[f][T] = sum(w[c] * (float(lab_L[c]) - float(lab_T[c])) for c in matched if factor_of(c) == f) / den
    return b, G


# ------------------------------------------------------------------ data and runs
class Data:
    """T/L datasets and the item/case maps (data/)."""

    def __init__(self, data_dir):
        d = Path(data_dir)
        self.ds = {c: {r['prompt_id']: r for r in read_jsonl(d / f'{c}.jsonl')} for c in CONDS}
        self.sha = {c: sha256_file(d / f'{c}.jsonl') for c in CONDS}
        self.case_map = {r['case_id']: r for r in read_jsonl(d / 'case_map.jsonl')}
        self.case_label = {cid: r['case_label'] for cid, r in self.case_map.items()}
        self.case_ids = sorted(self.case_map, key=lambda c: self.case_label[c])
        self.conv_changed = {cid: bool(r['turns_changed_T_vs_L']) for cid, r in self.case_map.items()}
        pts = {c: {(cid, x['criterion_id']): x['points'] for cid, case in self.ds[c].items() for x in case['rubrics']}
               for c in CONDS}
        self.items = {}
        for row in read_jsonl(d / 'criteria_map.jsonl'):
            key = (row['case_id'], row['criterion_id'])
            inT, inL = key in pts['T'], key in pts['L']
            kind = 'matched' if inT and inL else 'deleted' if inT else 'added' if inL else None
            if kind is None:
                raise ValueError(f'criteria_map row not in T or L: {key}')
            if kind == 'deleted' and not row['deleted_in_L'] or kind == 'added' and row['row_type'] == 'original':
                raise ValueError(f'criteria_map kind disagrees with the datasets: {key}')
            self.items[key] = {
                'kind': kind, 'case_id': row['case_id'], 'case_label': row['case_label'], 'criterion_id': row['criterion_id'],
                'points_T': pts['T'].get(key), 'points_L': pts['L'].get(key),
                'text_changed': bool(row['text_changed_T_vs_L']) if kind == 'matched' else None,
                'text_T': row.get('text_T'), 'text_L': row.get('text_L')}
        for c in CONDS:
            missing = [k for k in pts[c] if k not in self.items]
            if missing:
                raise ValueError(f'{c} items missing from criteria_map: {missing[:3]}')

    def item_class(self, case_id, crit):
        """Class of a matched item: 'identical' (same text and points), 'text_changed', or 'points_only'."""
        it = self.items[(case_id, crit)]
        if it['kind'] != 'matched':
            return it['kind']
        if it['text_changed']:
            return 'text_changed'
        return 'identical' if it['points_T'] == it['points_L'] else 'points_only'

    def theme(self, case_id):
        return [t.split(':', 1)[1] for t in self.case_map[case_id]['example_tags'] if t.startswith('theme:')]


class Grades:
    """All labels of one grader: lab[(model, cond, case_id, sample)] = {criterion_id: label or None}."""

    def __init__(self, run_dirs, data, check_sha=True):
        self.data = data
        self.grader = None
        self.lab, self.akey = {}, {}
        self.runs, self.skipped_repeats, self.skipped_other_conditions = [], 0, 0
        for d in map(Path, run_dirs):
            man = json.loads((d / 'manifest.json').read_text(encoding='utf-8'))
            if man.get('kind') != 'grade':
                raise ValueError(f'{d}: not a grade run')
            if self.grader not in (None, man['model_id']):
                raise ValueError(f'{d}: grader {man["model_id"]} differs from {self.grader}')
            self.grader = man['model_id']
            if check_sha:
                for c, h in man.get('dataset_sha256', {}).items():
                    if c in data.sha and data.sha[c] != h:
                        raise ValueError(f'{d}: dataset {c} hash differs from data/')
            res_path = d / 'results.jsonl'
            res = {x['task_id']: x.get('criteria_met') for x in read_jsonl(res_path)} if res_path.exists() else {}
            n_tasks = 0
            for t in read_jsonl(d / 'tasks.jsonl'):
                if t.get('repeat', 1) != 1:
                    self.skipped_repeats += 1
                    continue
                if t['condition'] not in CONDS:  # O tasks in combined O/T/L runs: analysed by o_analysis.py
                    self.skipped_other_conditions += 1
                    continue
                n_tasks += 1
                key = (t['answer_model'], t['condition'], t['case_id'], t['sample'])
                if key not in self.lab:
                    self.lab[key] = {x['criterion_id']: None for x in data.ds[t['condition']][t['case_id']]['rubrics']}
                    self.akey[key] = t['answer_key']
                elif self.akey[key] != t['answer_key']:
                    raise ValueError(f'{d}: two answers for {key}')
                crit = t['criterion_id']
                if crit not in self.lab[key]:
                    raise ValueError(f'{d}: {crit} is not in the {t["condition"]} rubric of {t["case_label"]}')
                met = res.get(t['task_id'])
                if met is not None:
                    if self.lab[key][crit] is not None:
                        raise ValueError(f'{d}: duplicate grade for {t["task_id"]}')
                    self.lab[key][crit] = met
            self.runs.append({'dir': str(d), 'tasks': n_tasks, 'results': len(res), 'dummy': bool(man.get('dummy'))})
        self.models = sorted({k[0] for k in self.lab})
        self.scores = {k: self._score(k) for k in self.lab}
        self._index()

    def _index(self):
        self._samples = defaultdict(list)
        for k in self.lab:
            self._samples[k[:3]].append(k[3])
        for v in self._samples.values():
            v.sort()

    def _score(self, key):
        items = self.data.ds[key[1]][key[2]]['rubrics']
        met = [self.lab[key][x['criterion_id']] for x in items]
        if any(m is None for m in met):
            return None
        return case_score(items, met)

    def samples(self, m, cond, case):
        return self._samples.get((m, cond, case), [])

    def shared(self, m, case):
        """True when the model's T and L answers for the case are the same answers (unchanged conversation)."""
        sT = self.samples(m, 'T', case)
        if not sT:
            return None
        return all(self.akey.get((m, 'L', case, s)) == self.akey[(m, 'T', case, s)] for s in sT)

    def restricted(self, keep):
        """A copy limited to the answer keys in `keep` (same grader)."""
        g = object.__new__(Grades)
        g.data, g.grader, g.runs, g.skipped_repeats = self.data, self.grader, self.runs, self.skipped_repeats
        g.skipped_other_conditions = self.skipped_other_conditions
        g.lab = {k: v for k, v in self.lab.items() if k in keep}
        g.akey = {k: self.akey[k] for k in g.lab}
        g.scores = {k: self.scores[k] for k in g.lab}
        g.models = sorted({k[0] for k in g.lab})
        g._index()
        return g


# ------------------------------------------------------------------ estimators
def _agg(G, models, cases, samples=None):
    """agg[m][cond][case] = (sum of defined case scores, n defined, n undefined)."""
    out = {m: {c: {} for c in CONDS} for m in models}
    for (m, c, case, s), v in G.scores.items():
        if m not in out or case not in cases or (samples and s not in samples):
            continue
        a = out[m][c].get(case, (0.0, 0, 0))
        out[m][c][case] = (a[0] + v, a[1] + 1, a[2]) if v is not None else (a[0], a[1], a[2] + 1)
    return out


def _model_means(agg, models, draw):
    out = {}
    for m in models:
        for c in CONDS:
            s = n = 0
            am = agg[m][c]
            for case in draw:
                a = am.get(case)
                if a:
                    s += a[0]
                    n += a[1]
            out[(m, c)] = s / n if n else None
    return out


def score_table(G, models, cases, draws, samples=None):
    """S_T, S_L (clipped), Delta_loc per model and the mean over models, with case-bootstrap CIs (paired T/L)."""
    cases = list(cases)
    agg = _agg(G, models, set(cases), samples)

    def stat(draw):
        mm = _model_means(agg, models, draw)
        st = {}
        for m in models:
            sT, sL = clip(mm[(m, 'T')]), clip(mm[(m, 'L')])
            st[m] = (sT, sL, None if sT is None or sL is None else sL - sT)
        st['__mean_delta__'] = mean([st[m][2] for m in models])
        return st, mm

    point, mm = stat(cases)
    reps = [stat(dw)[0] for dw in draws]
    rows = []
    for m in models:
        sT, sL, d = point[m]
        nT = sum(a[1] for a in agg[m]['T'].values()); uT = sum(a[2] for a in agg[m]['T'].values())
        nL = sum(a[1] for a in agg[m]['L'].values()); uL = sum(a[2] for a in agg[m]['L'].values())
        rawT, rawL = mm[(m, 'T')], mm[(m, 'L')]
        rows.append({'model': m, 'S_T': r(sT), 'S_T_ci': [r(x) for x in ci95([x[m][0] for x in reps]) or [None, None]],
                     'S_L': r(sL), 'S_L_ci': [r(x) for x in ci95([x[m][1] for x in reps]) or [None, None]],
                     'delta_loc': r(d), 'delta_loc_ci': [r(x) for x in ci95([x[m][2] for x in reps]) or [None, None]],
                     'mean_unclipped_T': r(rawT), 'mean_unclipped_L': r(rawL),
                     'clipping_changed': any(x is not None and (x < 0 or x > 1) for x in (rawT, rawL)),
                     'n_case_samples_T': nT, 'n_undefined_T': uT, 'n_case_samples_L': nL, 'n_undefined_L': uL})
    return {'rows': rows, 'mean_delta_loc': r(point['__mean_delta__']),
            'mean_delta_loc_ci': [r(x) for x in ci95([x['__mean_delta__'] for x in reps]) or [None, None]],
            'n_cases': len(cases)}, (point, reps)


def rank_table(models, point, reps):
    """Ranks under T and L, Kendall tau-b (with CI) and per-pair probability that T and L order the pair differently."""
    def scores_of(st, i):
        return {m: st[m][i] for m in models if st[m][0] is not None and st[m][1] is not None}
    sT, sL = scores_of(point, 0), scores_of(point, 1)
    ms = [m for m in models if m in sT and m in sL]
    rT, rL = ranks(sT), ranks(sL)
    tau = kendall_tau_b([sT[m] for m in ms], [sL[m] for m in ms])
    taus, swaps = [], defaultdict(int)
    for st in reps:
        a, b = scores_of(st, 0), scores_of(st, 1)
        mm = [m for m in ms if m in a and m in b]
        taus.append(kendall_tau_b([a[m] for m in mm], [b[m] for m in mm]))
        for i, x in enumerate(mm):
            for y in mm[i + 1:]:
                dT = (a[x] > a[y]) - (a[x] < a[y])
                dL = (b[x] > b[y]) - (b[x] < b[y])
                swaps[(x, y)] += dT != dL
    pairs = []
    for i, x in enumerate(ms):
        for y in ms[i + 1:]:
            pairs.append({'a': x, 'b': y, 'swapped_in_point_estimate': (sT[x] > sT[y]) != (sL[x] > sL[y]),
                          'swap_probability': r(swaps[(x, y)] / len(reps)) if reps else None})
    pairs.sort(key=lambda p: -(p['swap_probability'] or 0))
    return {'ranks': [{'model': m, 'rank_T': rT[m], 'rank_L': rL[m]} for m in sorted(ms, key=lambda m: rT[m])],
            'kendall_tau_b': r(tau), 'kendall_tau_b_ci': [r(x) for x in ci95(taus) or [None, None]], 'pairs': pairs}


def _factor_fn(data, case, item_groups):
    def factor_of(crit):
        cls = data.item_class(case, crit)
        if cls == 'identical':
            return NOISE
        if cls == 'points_only':
            return 'text:points_only'
        return 'text:' + item_groups.get((case, crit), 'all') if item_groups is not None else 'text:all'
    return factor_of


def rubric_decomposition(G, data, models, cases, draws, item_groups=None):
    """Delta_rubric on the unchanged-conversation cases (same answers graded with both rubrics) and its Shapley and
    fixed-order decomposition into: noise (identical items), text changes (by group), points, deleted, added."""
    comp = {m: {} for m in models}   # comp[m][case] = (n, sum_b{T}, sum_G{f}{T})
    excluded = defaultdict(int)
    factors = set()
    for m in models:
        for case in cases:
            itT = data.ds['T'][case]['rubrics']
            itL = data.ds['L'][case]['rubrics']
            fo = _factor_fn(data, case, item_groups)
            for s in G.samples(m, 'T', case):
                kT, kL = (m, 'T', case, s), (m, 'L', case, s)
                if kL not in G.lab or G.akey[kT] != G.akey[kL]:
                    continue
                if any(v is None for v in G.lab[kT].values()) or any(v is None for v in G.lab[kL].values()):
                    excluded[m] += 1
                    continue
                res = rubric_case_components(itT, itL, G.lab[kT], G.lab[kL], fo)
                if res is None:
                    excluded[m] += 1
                    continue
                b, Gf = res
                n, sb, sg = comp[m].get(case, (0, defaultdict(float), defaultdict(lambda: defaultdict(float))))
                for T, v in b.items():
                    sb[T] += v
                for f, g in Gf.items():
                    factors.add(f)
                    for T, v in g.items():
                        sg[f][T] += v
                comp[m][case] = (n + 1, sb, sg)
    factors = sorted(factors, key=lambda f: (f != NOISE, f))
    order = factors + list(STRUCT)
    T_all = frozenset(STRUCT)

    def model_values(m, draw):
        n, sb, sg = 0, defaultdict(float), {f: defaultdict(float) for f in factors}
        cm = comp[m]
        for case in draw:
            a = cm.get(case)
            if not a:
                continue
            n += a[0]
            for T, v in a[1].items():
                sb[T] += v
            for f, g in a[2].items():
                for T, v in g.items():
                    sg[f][T] += v
        if not n:
            return None
        b = {T: sb[T] / n for T in subsets(STRUCT)}
        Gf = {f: {T: sg[f][T] / n for T in subsets(STRUCT)} for f in factors}
        return b, Gf, n

    def stat(draw):
        out = {}
        for m in models:
            mv = model_values(m, draw)
            if mv is None:
                out[m] = None
                continue
            b, Gf, n = mv
            phi = shapley_linear(b, Gf)
            vT, vL = b[frozenset()], value_linear(b, Gf, set(factors) | T_all)
            seq, prev, cum = {}, vT, set()
            for f in order:
                cum.add(f)
                cur = value_linear(b, Gf, cum)
                seq[f] = cur - prev
                prev = cur
            out[m] = {'S_T': vT, 'S_L': vL, 'delta_unclipped': vL - vT,
                      'delta_clipped': clip(vL) - clip(vT), 'shapley': phi, 'fixed_order': seq, 'n': n}
        ok = [out[m] for m in models if out[m]]
        out['__mean__'] = {'delta_unclipped': mean([x['delta_unclipped'] for x in ok]),
                           'shapley': {f: mean([x['shapley'][f] for x in ok]) for f in order},
                           'fixed_order': {f: mean([x['fixed_order'][f] for x in ok]) for f in order}} if ok else None
        return out

    point = stat(cases)
    reps = [stat(dw) for dw in draws]

    def pack(get):
        return {'value': r(get(point)), 'ci': [r(x) for x in ci95([get(x) for x in reps]) or [None, None]]}

    rows = []
    for m in models:
        if point[m] is None:
            continue
        p = point[m]
        rows.append({'model': m, 'n_case_samples': p['n'], 'n_excluded': excluded[m],
                     'S_T_unclipped': r(p['S_T']), 'S_L_unclipped': r(p['S_L']),
                     'delta_rubric': pack(lambda x, m=m: x[m]['delta_unclipped'] if x[m] else None),
                     'delta_rubric_clipped': r(p['delta_clipped']),
                     'shapley': {f: pack(lambda x, m=m, f=f: x[m]['shapley'][f] if x[m] else None) for f in order},
                     'fixed_order': {f: r(p['fixed_order'][f]) for f in order},
                     'shapley_sum_check': r(sum(p['shapley'].values()) - p['delta_unclipped'], 12)})
    mean_part = None
    if point['__mean__']:
        mean_part = {'delta_rubric': pack(lambda x: x['__mean__']['delta_unclipped'] if x['__mean__'] else None),
                     'shapley': {f: pack(lambda x, f=f: x['__mean__']['shapley'][f] if x['__mean__'] else None) for f in order},
                     'fixed_order': {f: r(point['__mean__']['fixed_order'][f]) for f in order}}
    return {'factors': order, 'fixed_order': order, 'n_cases': len(cases), 'rows': rows, 'mean_over_models': mean_part,
            'note': 'Unclipped means. Shapley = average over all factor orders (exact); fixed order = the listed order. '
                    'noise = identical items (same text, points and conversation): T and L grades of the same request.'}


def item_disagreement(G, data, models, cases, draws, item_groups=None, item_codes=None):
    """Same answer graded under T and L (unchanged-conversation cases): per item class, the share of items whose
    grade differs and the mean (L - T) grade. 'identical' is the grading-noise baseline (same request twice)."""
    def classes(case, crit):
        cls = data.item_class(case, crit)
        out = [cls]
        if cls in ('text_changed', 'points_only'):
            if data.items[(case, crit)]['points_T'] != data.items[(case, crit)]['points_L']:
                out.append('points_changed')
            if item_groups is not None:
                out.append('group:' + item_groups.get((case, crit), 'unlabeled'))
            for code in (item_codes or {}).get((case, crit), []):
                out.append('code:' + code)
        return out
    per = defaultdict(lambda: defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0]))  # [n, sum|L-T|, sum(L-T), sum T, sum L]
    for (m, c, case, s), labT in G.lab.items():
        if c != 'T' or m not in models or case not in cases:
            continue
        kL = (m, 'L', case, s)
        if kL not in G.lab or G.akey[kL] != G.akey[(m, c, case, s)]:
            continue
        labL = G.lab[kL]
        for crit, t in labT.items():
            l_ = labL.get(crit)
            if t is None or l_ is None or crit not in labL:
                continue
            t, l_ = float(t), float(l_)
            for cl in classes(case, crit):
                a = per[(m, cl)][case]
                a[0] += 1; a[1] += t + l_ - 2 * t * l_   # = |L - T| for 0/1 labels
                a[2] += l_ - t; a[3] += t; a[4] += l_
    cls_names = sorted({k[1] for k in per})

    def summarise(keys, draw):
        n = d = s = sT = sL = 0.0
        for k in keys:
            pk = per.get(k)
            if not pk:
                continue
            for case in draw:
                a = pk.get(case)
                if a:
                    n += a[0]; d += a[1]; s += a[2]; sT += a[3]; sL += a[4]
        return (d / n, s / n, sT / n, sL / n, n) if n else None

    out = {}
    for cl in cls_names:
        rows = {}
        for scope in models + ['__pooled__']:
            keys = [(m, cl) for m in (models if scope == '__pooled__' else [scope])]
            p = summarise(keys, cases)
            if p is None:
                continue
            reps = [summarise(keys, dw) for dw in draws]
            rows[scope] = {'n_pairs': int(p[4]), 'disagreement': r(p[0]),
                           'disagreement_ci': [r(x) for x in ci95([x[0] if x else None for x in reps]) or [None, None]],
                           'mean_L_minus_T': r(p[1]),
                           'mean_L_minus_T_ci': [r(x) for x in ci95([x[1] if x else None for x in reps]) or [None, None]],
                           'met_T': r(p[2]), 'met_L': r(p[3])}
        out[cl] = rows
    return {'classes': out, 'note': 'Disagreement = share of (answer, item) pairs whose T and L grades differ; for '
            'probability labels the expected disagreement under independence. identical = same text and points '
            '(same request twice): the grading-noise baseline.'}


def only_items(G, data, models):
    """Items present in only one version: T-only (deleted in L) and L-only (added in L), with the met rate per model."""
    out = []
    for key, it in sorted(data.items.items(), key=lambda kv: (kv[1]['case_label'], kv[0][1])):
        if it['kind'] not in ('deleted', 'added'):
            continue
        cond = 'T' if it['kind'] == 'deleted' else 'L'
        rates = {}
        for m in models:
            labs = [G.lab[(m, cond, it['case_id'], s)].get(it['criterion_id']) for s in G.samples(m, cond, it['case_id'])]
            vals = [float(v) for v in labs if v is not None]
            rates[m] = r(mean(vals)) if vals else None
        out.append({'version': 'T_only' if cond == 'T' else 'L_only', 'case_label': it['case_label'],
                    'criterion_id': it['criterion_id'], 'points': it['points_T'] if cond == 'T' else it['points_L'],
                    'text': it['text_T'] if cond == 'T' else it['text_L'], 'met_rate': rates,
                    'met_rate_pooled': r(mean([v for v in rates.values() if v is not None]))})
    return out


def strata(G, data, models, n_boot, seed, conv_codes=None):
    """Exploratory: S_T, S_L, Delta_loc by case group (point estimate and case-bootstrap CI within the group)."""
    groupings = {
        'workbook_group': lambda c: [data.case_map[c]['workbook_group']],
        'specialty': lambda c: [data.case_map[c]['specialty']],
        'scenario': lambda c: [data.case_map[c]['scenario']],
        'theme': lambda c: data.theme(c),
        'source_dataset': lambda c: [data.case_map[c]['source_dataset']],
        'original_language': lambda c: [data.case_map[c].get('original_language')],
        'conversation': lambda c: ['changed' if data.conv_changed[c] else 'unchanged'],
    }
    if conv_codes:
        groupings['conversation_change_code'] = lambda c: conv_codes.get(c, []) if data.conv_changed[c] else []
    out = {}
    for name, fn in groupings.items():
        groups = defaultdict(list)
        for c in data.case_ids:
            for g in fn(c):
                if g is not None:
                    groups[g].append(c)
        res = {}
        for g, cs in sorted(groups.items()):
            draws = draws_for(cs, n_boot if len(cs) > 1 else 0, seed, f'strata:{name}:{g}')  # no CI for a single case
            tab, _ = score_table(G, models, cs, draws)
            res[g] = {'n_cases': len(cs), 'mean_delta_loc': tab['mean_delta_loc'], 'mean_delta_loc_ci': tab['mean_delta_loc_ci'],
                      'by_model': {x['model']: {'S_T': x['S_T'], 'S_L': x['S_L'], 'delta_loc': x['delta_loc'],
                                                'delta_loc_ci': x['delta_loc_ci']} for x in tab['rows']}}
        out[name] = res
    out['axis'] = axis_scores(G, data, models, n_boot, seed)
    return out


def axis_scores(G, data, models, n_boot, seed):
    """Rubric-axis scores (scoring.tag_scores on axis:* tags): clipped mean over case-samples where defined."""
    per = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))  # per[(m, cond, axis)][case] = [sum, n]
    for (m, c, case, s), lab in G.lab.items():
        if m not in models or any(v is None for v in lab.values()):
            continue
        by = defaultdict(list)
        for x in data.ds[c][case]['rubrics']:
            for t in x.get('tags', []):
                if t.startswith('axis:'):
                    by[t[5:]].append(x)
        for ax, items in by.items():
            sc = case_score(items, [lab[x['criterion_id']] for x in items])
            if sc is not None:
                a = per[(m, c, ax)][case]
                a[0] += sc; a[1] += 1
    axes = sorted({k[2] for k in per})
    draws = draws_for(data.case_ids, n_boot, seed, 'axis')

    def val(key, draw):
        s = n = 0
        pk = per.get(key, {})
        for case in draw:
            a = pk.get(case)
            if a:
                s += a[0]; n += a[1]
        return clip(s / n) if n else None
    out = {}
    for ax in axes:
        res = {}
        for m in models:
            pT, pL = val((m, 'T', ax), data.case_ids), val((m, 'L', ax), data.case_ids)
            ds = [None if (a := val((m, 'T', ax), dw)) is None or (b := val((m, 'L', ax), dw)) is None else b - a for dw in draws]
            res[m] = {'S_T': r(pT), 'S_L': r(pL), 'delta': r(None if pT is None or pL is None else pL - pT),
                      'delta_ci': [r(x) for x in ci95(ds) or [None, None]]}
        out[ax] = res
    return out


def variability(G, data, models):
    """Per model and version: mean over cases of the SD of the 3 sample scores, and worst-at-3 (clipped mean of the
    per-case minimum) next to the mean score on the same cases."""
    out = []
    for m in models:
        for c in CONDS:
            sds, worst, avg = [], [], []
            for case in data.case_ids:
                vals = [G.scores.get((m, c, case, s)) for s in G.samples(m, c, case)]
                if len(vals) < 2 or any(v is None for v in vals):
                    continue
                sds.append(statistics.stdev(vals))
                worst.append(min(vals))
                avg.append(sum(vals) / len(vals))
            out.append({'model': m, 'condition': c, 'n_cases_all_samples': len(worst), 'mean_sd': r(mean(sds)),
                        'worst_at_k': r(clipped_mean(worst)) if worst else None,
                        'mean_same_cases': r(clipped_mean(avg)) if avg else None})
    return out


def grader_comparison(Gm, Gs, data, models, n_boot, seed):
    """Main vs secondary grader on the answers both graded (sample 1): Delta_loc under each, their difference
    (case-bootstrap CI, paired), Delta_rubric (unchanged conversations) and item-level agreement / kappa."""
    common = sorted(k for k in Gs.lab if k in Gm.lab and Gm.akey[k] == Gs.akey[k])
    samples = sorted({k[3] for k in common})
    ms = sorted({k[0] for k in common if k[0] in models})
    keep = set(common)
    vm, vs = Gm.restricted(keep), Gs.restricted(keep)
    draws = draws_for(data.case_ids, n_boot, seed, 'grader_comparison')
    tm, (pm, rm) = score_table(vm, ms, data.case_ids, draws)
    ts, (ps, rs) = score_table(vs, ms, data.case_ids, draws)
    rows = []
    for m in ms:
        dm, ds_ = pm[m][2], ps[m][2]
        diff = [None if a[m][2] is None or b[m][2] is None else b[m][2] - a[m][2] for a, b in zip(rm, rs)]
        rows.append({'model': m, 'self_grading_main': VENDOR.get(m) == GRADER_VENDOR.get(Gm.grader),
                     'self_grading_secondary': VENDOR.get(m) == GRADER_VENDOR.get(Gs.grader),
                     'delta_loc_main': r(dm), 'delta_loc_secondary': r(ds_),
                     'secondary_minus_main': r(None if dm is None or ds_ is None else ds_ - dm),
                     'secondary_minus_main_ci': [r(x) for x in ci95(diff) or [None, None]],
                     'S_T_main': r(pm[m][0]), 'S_L_main': r(pm[m][1]), 'S_T_secondary': r(ps[m][0]), 'S_L_secondary': r(ps[m][1])})
    agree = {}
    for m in ms + ['__pooled__']:
        n = a = p1 = p2 = 0.0
        for k in common:
            if m != '__pooled__' and k[0] != m:
                continue
            for crit, x in Gm.lab[k].items():
                y = Gs.lab[k].get(crit)
                if x is None or y is None:
                    continue
                x, y = float(x), float(y)
                n += 1; a += x * y + (1 - x) * (1 - y); p1 += x; p2 += y
        if n:
            po, q1, q2 = a / n, p1 / n, p2 / n
            pe = q1 * q2 + (1 - q1) * (1 - q2)
            agree[m] = {'n_items': int(n), 'agreement': r(po), 'kappa': r((po - pe) / (1 - pe)) if pe < 1 else None,
                        'met_main': r(q1), 'met_secondary': r(q2)}
    same = [c for c in data.case_ids if not data.conv_changed[c]]
    dr = {}
    for name, v in (('main', vm), ('secondary', vs)):
        d = rubric_decomposition(v, data, ms, same, draws_for(same, n_boot, seed, 'grader_comparison_rubric'))  # same draws: paired
        dr[name] = {x['model']: x['delta_rubric'] for x in d['rows']}
    return {'grader_main': Gm.grader, 'grader_secondary': Gs.grader, 'samples': samples,
            'n_common_answers': len({Gm.akey[k] for k in common}), 'rows': rows,
            'mean_delta_loc_main': tm['mean_delta_loc'], 'mean_delta_loc_secondary': ts['mean_delta_loc'],
            'item_agreement': agree, 'delta_rubric': dr}


def missing_report(G, data):
    out = []
    for k in sorted(G.lab, key=lambda k: (k[0], k[1], data.case_label[k[2]], k[3])):
        miss = [c for c, v in G.lab[k].items() if v is None]
        if miss:
            out.append({'model': k[0], 'condition': k[1], 'case_label': data.case_label[k[2]], 'sample': k[3],
                        'n_missing_items': len(miss), 'n_items': len(G.lab[k])})
    return out


# ------------------------------------------------------------------ labels files (R9 change types)
def load_item_codes(path, data):
    """JSONL rows {"case_id" or "case_label", "criterion_id", "codes": [...]} for text-changed items.
    Returns (codes per item, group per item = '+'.join(sorted codes), ignored rows)."""
    by_label = {v: k for k, v in data.case_label.items()}
    codes, ignored = {}, 0
    for row in read_jsonl(path):
        case = row.get('case_id') or by_label.get(row.get('case_label'))
        key = (case, row['criterion_id'])
        if key not in data.items or data.item_class(*key) not in ('text_changed', 'points_only'):
            ignored += 1
            continue
        codes[key] = sorted(set(row['codes']))
    groups = {k: '+'.join(v) if v else 'unlabeled' for k, v in codes.items()}
    return codes, groups, ignored


def load_conv_codes(path, data):
    by_label = {v: k for k, v in data.case_label.items()}
    out = {}
    for row in read_jsonl(path):
        case = row.get('case_id') or by_label.get(row.get('case_label'))
        out[case] = sorted(set(row['codes']))
    return out


# ------------------------------------------------------------------ driver
def run_all(data_dir, main_dirs, secondary_dirs=None, labels=None, conv_labels=None, n_boot=N_BOOT, seed=SEED,
            check_sha=True):
    data = Data(data_dir)
    Gm = Grades(main_dirs, data, check_sha)
    models = Gm.models
    item_codes = item_groups = conv_codes = None
    ignored = 0
    if labels:
        item_codes, item_groups, ignored = load_item_codes(labels, data)
    if conv_labels:
        conv_codes = load_conv_codes(conv_labels, data)
    shared = {c: [Gm.shared(m, c) for m in models] for c in data.case_ids}
    same = [c for c in data.case_ids if all(x for x in shared[c] if x is not None) and any(x is not None for x in shared[c])]
    changed = [c for c in data.case_ids if c not in same]
    mismatch = [data.case_label[c] for c in data.case_ids if (c in same) == data.conv_changed[c]]
    out = {'grader_main': Gm.grader, 'models': models, 'n_boot': n_boot, 'seed': seed,
           'runs_main': Gm.runs, 'score_definition': 'simple-evals: case score = met points / positive points (not '
           'clipped per case); model score = mean over case-samples clipped once to [0, 1]; Delta = difference of '
           'clipped scores; decompositions use unclipped means',
           'cases': {'all': len(data.case_ids), 'unchanged_conversation': len(same), 'changed_conversation': len(changed),
                     'disagree_with_case_map': mismatch},
           'labels': {'item_labels_file': str(labels) if labels else None, 'item_rows_ignored': ignored,
                      'conv_labels_file': str(conv_labels) if conv_labels else None}}
    d_all = draws_for(data.case_ids, n_boot, seed, 'all')
    tab, (point, reps) = score_table(Gm, models, data.case_ids, d_all)
    out['overall'] = tab
    out['ranking'] = rank_table(models, point, reps)
    out['by_conversation'] = {
        'unchanged': score_table(Gm, models, same, draws_for(same, n_boot, seed, 'same'))[0],
        'changed': score_table(Gm, models, changed, draws_for(changed, n_boot, seed, 'changed'))[0]}
    d_same = draws_for(same, n_boot, seed, 'rubric')
    out['delta_rubric'] = rubric_decomposition(Gm, data, models, same, d_same, item_groups)
    out['item_disagreement'] = item_disagreement(Gm, data, models, set(same), d_same, item_groups, item_codes)
    out['only_items'] = only_items(Gm, data, models)
    out['strata'] = strata(Gm, data, models, n_boot, seed, conv_codes)
    out['variability'] = variability(Gm, data, models)
    out['missing'] = missing_report(Gm, data)
    if secondary_dirs:
        Gs = Grades(secondary_dirs, data, check_sha)
        out['runs_secondary'] = Gs.runs
        out['grader_comparison'] = grader_comparison(Gm, Gs, data, models, n_boot, seed)
        out['missing_secondary'] = missing_report(Gs, data)
    return out


# ------------------------------------------------------------------ markdown
def _f(x, k=3):
    return '—' if x is None else f'{x:.{k}f}'


def _ci(c, k=3):
    return '—' if not c or c[0] is None else f'[{c[0]:.{k}f}, {c[1]:.{k}f}]'


def to_markdown(res):
    L = [f"# 集計結果（主採点役 {res['grader_main']}）", '',
         f"- 得点の定義：{res['score_definition']}",
         f"- bootstrap：ケース単位 {res['n_boot']} 回、seed {res['seed']}。区間は 2.5〜97.5 パーセンタイル。",
         f"- ケース：全 {res['cases']['all']}、会話が同一 {res['cases']['unchanged_conversation']}、会話が変更 "
         f"{res['cases']['changed_conversation']}（case_map との不一致：{res['cases']['disagree_with_case_map'] or 'なし'}）",
         f"- 採点 run：{', '.join(Path(x['dir']).name + (' (dummy)' if x['dummy'] else '') for x in res['runs_main'])}", '',
         '## 1. 全体（RQ1）', '', '| モデル | S_T | S_L | Δ_loc [95%CI] | 欠損 T/L | クリップ |', '| --- | ---: | ---: | --- | ---: | --- |']
    for x in res['overall']['rows']:
        L.append(f"| {x['model']} | {_f(x['S_T'])} | {_f(x['S_L'])} | {_f(x['delta_loc'])} {_ci(x['delta_loc_ci'])} | "
                 f"{x['n_undefined_T']}/{x['n_undefined_L']} | {'変化あり' if x['clipping_changed'] else '—'} |")
    L.append(f"| **モデル平均** | | | {_f(res['overall']['mean_delta_loc'])} {_ci(res['overall']['mean_delta_loc_ci'])} | | |")
    rk = res['ranking']
    L += ['', f"Kendall τ-b（T と L の順位）：{_f(rk['kendall_tau_b'])} {_ci(rk['kendall_tau_b_ci'])}", '',
          '| モデル | 順位 T | 順位 L |', '| --- | ---: | ---: |']
    L += [f"| {x['model']} | {x['rank_T']} | {x['rank_L']} |" for x in rk['ranks']]
    L += ['', '入れ替わり確率の上位（bootstrap で T と L の順序が異なる割合）：', '', '| 組 | 確率 | 点推定で入れ替わり |', '| --- | ---: | --- |']
    L += [f"| {p['a']} / {p['b']} | {_f(p['swap_probability'], 2)} | {'○' if p['swapped_in_point_estimate'] else ''} |"
          for p in rk['pairs'][:10]]
    L += ['', '## 2. 会話の変更の有無で分けた Δ_loc', '', '| モデル | 会話同一 Δ_loc | 会話変更 Δ_loc |', '| --- | --- | --- |']
    u, c = res['by_conversation']['unchanged'], res['by_conversation']['changed']
    cm = {x['model']: x for x in c['rows']}
    for x in u['rows']:
        y = cm.get(x['model'], {})
        L.append(f"| {x['model']} | {_f(x['delta_loc'])} {_ci(x['delta_loc_ci'])} | {_f(y.get('delta_loc'))} {_ci(y.get('delta_loc_ci'))} |")
    L.append(f"| **モデル平均** | {_f(u['mean_delta_loc'])} {_ci(u['mean_delta_loc_ci'])} | {_f(c['mean_delta_loc'])} {_ci(c['mean_delta_loc_ci'])} |")
    dr = res['delta_rubric']
    L += ['', '## 3. 基準の違いだけによる差 Δ_rubric と分解（RQ2、会話が同一のケース）', '', dr['note'], '']
    if dr['mean_over_models']:
        mo = dr['mean_over_models']
        L += [f"モデル平均 Δ_rubric = {_f(mo['delta_rubric']['value'])} {_ci(mo['delta_rubric']['ci'])}", '',
              '| 因子 | Shapley [95%CI] | 固定順 |', '| --- | --- | ---: |']
        L += [f"| {f} | {_f(mo['shapley'][f]['value'])} {_ci(mo['shapley'][f]['ci'])} | {_f(mo['fixed_order'][f])} |" for f in dr['factors']]
    L += ['', '| モデル | Δ_rubric [95%CI] | ' + ' | '.join(dr['factors']) + ' |', '| --- | --- | ' + ' | '.join('---:' for _ in dr['factors']) + ' |']
    for x in dr['rows']:
        L.append(f"| {x['model']} | {_f(x['delta_rubric']['value'])} {_ci(x['delta_rubric']['ci'])} | " +
                 ' | '.join(_f(x['shapley'][f]['value']) for f in dr['factors']) + ' |')
    L += ['', '## 4. 項目の判定が T と L で変わる割合（同じ回答）', '', res['item_disagreement']['note'], '',
          '| 項目の区分 | 組数 | 不一致率 [95%CI] | 平均(L−T) [95%CI] |', '| --- | ---: | --- | --- |']
    for cl, rows in res['item_disagreement']['classes'].items():
        p = rows.get('__pooled__')
        if p:
            L.append(f"| {cl} | {p['n_pairs']} | {_f(p['disagreement'])} {_ci(p['disagreement_ci'])} | "
                     f"{_f(p['mean_L_minus_T'])} {_ci(p['mean_L_minus_T_ci'])} |")
    L += ['', '## 5. 片方の版にだけある項目', '', '| 版 | ケース | 項目 | 配点 | 満たした割合（全モデル平均） |', '| --- | --- | --- | ---: | ---: |']
    L += [f"| {x['version']} | {x['case_label']} | {x['criterion_id']} | {x['points']} | {_f(x['met_rate_pooled'])} |" for x in res['only_items']]
    L += ['', '## 6. 層別（探索的、モデル平均の Δ_loc）', '']
    for name, groups in res['strata'].items():
        if name == 'axis':
            continue
        L += [f'### {name}', '', '| 群 | ケース数 | Δ_loc [95%CI] |', '| --- | ---: | --- |']
        L += [f"| {g} | {v['n_cases']} | {_f(v['mean_delta_loc'])} {_ci(v['mean_delta_loc_ci'])} |" for g, v in groups.items()]
        L.append('')
    L += ['### axis（モデル平均の Δ）', '', '| axis | Δ |', '| --- | ---: |']
    for ax, rows in res['strata']['axis'].items():
        L.append(f"| {ax} | {_f(mean([v['delta'] for v in rows.values()]))} |")
    L += ['', '## 7. ばらつき（3サンプル）', '', '| モデル | 版 | ケース | SD 平均 | worst-at-3 | 同じケースの平均 |', '| --- | --- | ---: | ---: | ---: | ---: |']
    L += [f"| {x['model']} | {x['condition']} | {x['n_cases_all_samples']} | {_f(x['mean_sd'])} | {_f(x['worst_at_k'])} | {_f(x['mean_same_cases'])} |"
          for x in res['variability']]
    if 'grader_comparison' in res:
        gc = res['grader_comparison']
        L += ['', f"## 8. 主採点役 {gc['grader_main']} と副採点役 {gc['grader_secondary']}（サンプル {gc['samples']}）", '',
              f"モデル平均 Δ_loc：主 {_f(gc['mean_delta_loc_main'])}、副 {_f(gc['mean_delta_loc_secondary'])}", '',
              '| モデル | 自己採点 | Δ_loc 主 | Δ_loc 副 | 副−主 [95%CI] | Δ_rubric 主 | Δ_rubric 副 | 項目一致率 | κ |',
              '| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |']
        for x in gc['rows']:
            a = gc['item_agreement'].get(x['model'], {})
            sg = '主' if x['self_grading_main'] else '副' if x['self_grading_secondary'] else ''
            L.append(f"| {x['model']} | {sg} | {_f(x['delta_loc_main'])} | {_f(x['delta_loc_secondary'])} | "
                     f"{_f(x['secondary_minus_main'])} {_ci(x['secondary_minus_main_ci'])} | "
                     f"{_f((gc['delta_rubric']['main'].get(x['model']) or {}).get('value'))} | "
                     f"{_f((gc['delta_rubric']['secondary'].get(x['model']) or {}).get('value'))} | "
                     f"{_f(a.get('agreement'))} | {_f(a.get('kappa'))} |")
        p = gc['item_agreement'].get('__pooled__', {})
        L.append(f"| **全体** | | | | | | | {_f(p.get('agreement'))} | {_f(p.get('kappa'))} |")
    L += ['', '## 9. 採点が欠けたケース・サンプル（得点なし）', '']
    miss = res['missing'] + res.get('missing_secondary', [])
    if miss:
        L += ['| モデル | 版 | ケース | サンプル | 欠けた項目 |', '| --- | --- | --- | ---: | ---: |']
        L += [f"| {x['model']} | {x['condition']} | {x['case_label']} | {x['sample']} | {x['n_missing_items']}/{x['n_items']} |" for x in miss]
    else:
        L.append('なし')
    return '\n'.join(L) + '\n'
