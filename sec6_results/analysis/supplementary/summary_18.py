"""Headline numbers of the 18-model set next to the 17-model set (out_main_v11_18models / analysis_18models)."""
import json
import statistics as st
from pathlib import Path

A = Path('<EXPERIMENT_ROOT>/analysis')
ld = lambda p: json.loads((A / p).read_text(encoding='utf-8'))
M = {'17': ld('out_main_v10_17models/analysis.json'), '18': ld('out_main_v11_18models/analysis.json')}
X = {'17': ld('out_axes_v2/analysis_17models.json'), '18': ld('out_axes_v2/analysis_18models.json')}
f = lambda v, c=None: f'{v:+.4f}' + (f' [{c[0]:+.4f}, {c[1]:+.4f}]' if c else '')
g = lambda v, c=None: f'{v:.4f}' + (f' [{c[0]:.4f}, {c[1]:.4f}]' if c else '')
for k in ('17', '18'):
    m, x = M[k], X[k]
    rows = m['overall']['rows']
    print(f'=== {k} models ({len(m["models"])} / {len(x["models"])})')
    mean_ci = lambda key: None
    print('mean S_T %.4f  S_L %.4f' % (st.mean(r['S_T'] for r in rows), st.mean(r['S_L'] for r in rows)),
          '| mean Delta_loc', f(m['overall']['mean_delta_loc'], m['overall']['mean_delta_loc_ci']),
          '| tau T vs L', g(m['ranking']['kendall_tau_b'], m['ranking']['kendall_tau_b_ci']))
    for name in ('unchanged', 'changed'):
        b = m['by_conversation'][name]
        print(f'  conversation {name} ({b["n_cases"]}): Delta_loc', f(b['mean_delta_loc'], b['mean_delta_loc_ci']))
    mo = x['delta_rubric']['mean_over_models']
    print('  Delta_rubric', f(mo['delta_rubric']['value'], mo['delta_rubric']['ci']), '| out_main:',
          f(m['delta_rubric']['mean_over_models']['delta_rubric']['value'], m['delta_rubric']['mean_over_models']['delta_rubric']['ci']))
    print('  Shapley:', {fct: f(v['value'], v['ci']) for fct, v in mo['shapley'].items()})
    sa = x['same_answer']['groups']['ct']
    print('  same answer:', {ct: {p: f(sa[ct][p]['delta_L_minus_T'], sa[ct][p]['ci']) for p in ('pos', 'neg') if p in sa[ct]}
                             for ct in ('E', 'J', '変更なし', '割れた', 'G')})
    jb = x['same_answer']['groups']['japan']['日本の環境']['neg']
    print('  same answer Japan binary neg:', f(jb['delta_L_minus_T'], jb['ci']))
    ax = x['axis_scores']
    print('  axis scores mean S_L / delta:', {a: (round(st.mean(ax[a][mm]['S_L'] for mm in x['models']), 3), round(st.mean(ax[a][mm]['delta'] for mm in x['models']), 3))
                                             for a in ax if a != '(タグなし)'})
    for gg in ('ct_L', 'ct_T'):
        mm = x['rates'][gg]['model_mean']
        print(f'  {gg} pos/neg/score:', {c: [round(mm[c][q], 3) if mm[c][q] is not None else None for q in ('pos_met', 'neg_met', 'score')] for c in mm},
              '| tau', x['rates'][gg]['tau_vs_overall'])
    jl = x['rates']['japan_L']['model_mean']
    print('  japan_L pos:', {c: round(jl[c]['pos_met'], 3) for c in jl}, '| tau', x['rates']['japan_L']['tau_vs_overall'])
    print('  axis tau L:', {a: t for a, t in x['rates']['axis_L']['tau_vs_overall'].items() if a != '(タグなし)'})
    print('  scenario:', {s: f(d['mean_delta_loc'], d['mean_delta_loc_ci']) for s, d in x['scenario']['step1'].items()})
    cn = x['critical_negative']['axis_L']
    tot = sorted(round(sum(v['met'] for v in cn[mm].values())) for mm in x['models'])
    print('  critical negative (L) per model: min', tot[0], 'max', tot[-1])
r = {q['model']: q for q in M['18']['overall']['rows']}['llmjp41_8b']
rk = {q['model']: (q['rank_T'], q['rank_L']) for q in M['18']['ranking']['ranks']}
print('llmjp41_8b: S_T %.4f S_L %.4f Delta %s rank T/L %s' % (r['S_T'], r['S_L'], f(r['delta_loc'], r['delta_loc_ci']), rk['llmjp41_8b']))
for gname in ('axis', 'ct', 'japan'):
    for row in X['18']['pairs'][gname]:
        if 'LLM-jp 4.1 8B' in row['pair']:
            print(gname, row['pair'], {c: f(d['diff'], d['ci']) for c, d in row['by_category'].items()})
