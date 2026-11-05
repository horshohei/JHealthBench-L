"""Aggregate the R9 classifications: agreement between the two models, stability of each model over 2 runs,
consistency with the physicians' case-level codes, consensus labels per unit, and provisional counts.

uv run --no-project python r9/aggregate.py   (reads r9/runs/{gptoss,deepseek}_run{1,2}/labels.jsonl)
-> r9/labels_merged.jsonl, r9/agreement.json, r9/summary.md
uv run --no-project python r9/aggregate.py --dir r9/v3   (2026-10-19: units and runs of the corrected L)
"""
import argparse
import collections
import json
from pathlib import Path

_ap = argparse.ArgumentParser()
_ap.add_argument('--dir', default=str(Path(__file__).resolve().parent))
R9 = Path(_ap.parse_args().dir).resolve()
RUNS = {'A1': 'gptoss_run1', 'A2': 'gptoss_run2', 'B1': 'deepseek_run1', 'B2': 'deepseek_run2'}
MODEL = {'A': 'gpt-oss-120b', 'B': 'DeepSeek V4 Flash'}
E_CODES = ['ADJ_DRUG_DEVICE', 'ADJ_TEST_UNIT', 'ADJ_GUIDELINE', 'ADJ_CARE_PATHWAY', 'ADJ_EMERGENCY', 'ADJ_INSURANCE',
           'ADJ_REGULATION', 'ADJ_DOCUMENT', 'ADJ_PUBLIC_HEALTH', 'ADJ_EPIDEMIOLOGY', 'ADJ_CULTURE', 'ADJ_SAFETY', 'ADJ_OTHER']
G_CODES = ['G_CLARIFY', 'G_STRICTNESS', 'G_CORRECTION', 'G_RESTRUCTURE', 'G_CONTENT']
ALL = E_CODES + ['ADJ_LANGUAGE'] + G_CODES + ['NONE']


def jl(p):
    return [json.loads(x) for x in open(p, encoding='utf-8') if x.strip()] if Path(p).exists() else []


def kappa(pairs):
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    n = len(pairs)
    if not n:
        return None, 0
    po = sum(a == b for a, b in pairs) / n
    ca, cb = collections.Counter(a for a, _ in pairs), collections.Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / n / n
    return (round((po - pe) / (1 - pe), 3) if pe < 1 else None), n


def agree(pairs):
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    return round(sum(a == b for a, b in pairs) / len(pairs), 3) if pairs else None


units = {u['unit_id']: u for u in jl(R9 / 'units.jsonl')}
labels = {k: {x['unit_id']: x for x in jl(R9 / 'runs' / v / 'labels.jsonl')} for k, v in RUNS.items()}
avail = {k: len(v) for k, v in labels.items()}


def major(u, lab):
    if lab is None:
        return None
    return lab['primary_major']


def has_e(lab):
    return None if lab is None else any(c in E_CODES for c in lab['codes'])


merged = []
for uid, u in units.items():
    row = {'unit_id': uid, 'unit_type': u['unit_type'], 'case_label': u['case_label'], 'change_source': u['change_source'],
           'points_changed': u.get('points_changed', False)}
    for k in RUNS:
        lab = labels[k].get(uid)
        row[k] = None if lab is None else {'codes': lab['codes'], 'primary_major': lab['primary_major'],
                                           'japan_specific': lab['japan_specific'], 'reason': lab['reason']}
    a, b = row['A1'], row['B1']
    pa, pb = (a or {}).get('primary_major'), (b or {}).get('primary_major')
    row['consensus_major'] = pa if (pa is not None and pa == pb) else ('disagree' if pa and pb else None)
    votes = collections.Counter((row[k] or {}).get('primary_major') for k in RUNS if row[k])
    top = votes.most_common(2)
    row['majority4_major'] = top[0][0] if top and (len(top) == 1 or top[0][1] > top[1][1]) else ('tie' if top else None)
    row['structure'] = ('S' if u['unit_type'] in ('criterion_deleted', 'criterion_added') or u.get('points_changed') else None)
    # final provisional category for analysis: S units keep S as the structural factor; reason = consensus major
    row['analysis_major'] = ('S' if u['unit_type'] in ('criterion_deleted', 'criterion_added') else row['consensus_major'])
    merged.append(row)

with open(R9 / 'labels_merged.jsonl', 'w', encoding='utf-8') as f:
    for r in merged:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')

text_like = [r for r in merged if r['unit_type'] in ('criterion_text', 'conversation_turn')]
res = {'runs_available': avail, 'models': MODEL}
res['between_models_run1'] = {
    'primary_major_kappa_text_and_turns': kappa([((r['A1'] or {}).get('primary_major'), (r['B1'] or {}).get('primary_major')) for r in text_like]),
    'primary_major_agreement_text_and_turns': agree([((r['A1'] or {}).get('primary_major'), (r['B1'] or {}).get('primary_major')) for r in text_like]),
    'reason_major_kappa_deleted_added': kappa([((r['A1'] or {}).get('primary_major'), (r['B1'] or {}).get('primary_major')) for r in merged if r['unit_type'] in ('criterion_deleted', 'criterion_added')]),
    'any_E_code_kappa_all_units': kappa([(has_e(r['A1']), has_e(r['B1'])) for r in merged]),
    'per_code_presence_kappa': {c: kappa([(c in (r['A1'] or {}).get('codes', []) if r['A1'] else None,
                                           c in (r['B1'] or {}).get('codes', []) if r['B1'] else None) for r in merged]) for c in ALL},
}
res['stability_run1_vs_run2'] = {
    m: {'primary_major_kappa': kappa([((r[m + '1'] or {}).get('primary_major'), (r[m + '2'] or {}).get('primary_major')) for r in merged]),
        'primary_major_agreement': agree([((r[m + '1'] or {}).get('primary_major'), (r[m + '2'] or {}).get('primary_major')) for r in merged]),
        'any_E_kappa': kappa([(has_e(r[m + '1']), has_e(r[m + '2'])) for r in merged])}
    for m in ('A', 'B')}

# consistency with the physicians' case-level codes (case unit): physician-selected ADJ codes vs codes found by
# the models (union over the case's units, consensus = code given by both models in run1)
case_codes = collections.defaultdict(lambda: {'phys': set(), 'both': set()})
for r in merged:
    u = units[r['unit_id']]
    case_codes[r['case_label']]['phys'] |= set(c for c in (u.get('case_strategy_codes') or []) if c.startswith('ADJ_'))
    if r['A1'] and r['B1']:
        case_codes[r['case_label']]['both'] |= set(r['A1']['codes']) & set(r['B1']['codes'])
pc = []
for case, d in case_codes.items():
    for c in E_CODES + ['ADJ_LANGUAGE']:
        pc.append((c in d['phys'], c in d['both']))
tp = sum(1 for p, b in pc if p and b)
res['physician_case_codes'] = {
    'case_code_pairs': len(pc), 'physician_selected': sum(1 for p, _ in pc if p), 'models_both_found': sum(1 for _, b in pc if b),
    'both': tp, 'recall_of_physician_codes': round(tp / max(1, sum(1 for p, _ in pc if p)), 3),
    'precision_vs_physician_codes': round(tp / max(1, sum(1 for _, b in pc if b)), 3),
    'kappa': kappa(pc),
    'note': 'case-level: a code counts as found if both models gave it to at least one unit of the case (run1)'}

# provisional counts
cnt = collections.Counter((r['unit_type'], r['consensus_major']) for r in merged)
res['provisional_counts'] = {f'{k[0]}|{k[1]}': v for k, v in sorted(cnt.items(), key=lambda x: (x[0][0], str(x[0][1])))}
src = collections.Counter((r['change_source'], r['consensus_major']) for r in merged if r['unit_type'] == 'criterion_text')
res['criterion_text_by_source'] = {f'{k[0]}|{k[1]}': v for k, v in sorted(src.items(), key=lambda x: (x[0][0], str(x[0][1])))}
code_cnt = collections.Counter()
for r in merged:
    if r['A1'] and r['B1']:
        for c in set(r['A1']['codes']) & set(r['B1']['codes']):
            code_cnt[c] += 1
res['consensus_code_counts'] = dict(code_cnt.most_common())
(R9 / 'agreement.json').write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')

L = ['# R9 集計（暫定）', '', f"実行済み run の単位数：{avail}", '',
     '## 2モデルの一致（run1どうし）', '',
     f"- 主な大区分（本文の変更・会話の単位）：κ={res['between_models_run1']['primary_major_kappa_text_and_turns'][0]}（n={res['between_models_run1']['primary_major_kappa_text_and_turns'][1]}）、一致率 {res['between_models_run1']['primary_major_agreement_text_and_turns']}",
     f"- 削除・追加の理由の大区分：κ={res['between_models_run1']['reason_major_kappa_deleted_added'][0]}（n={res['between_models_run1']['reason_major_kappa_deleted_added'][1]}）",
     f"- E のコードがあるか（全単位）：κ={res['between_models_run1']['any_E_code_kappa_all_units'][0]}", '',
     '', '### コード別（付けたかどうか）の κ', '', '| コード | κ | n |', '| --- | ---: | ---: |']
L += [f"| {c} | {k[0]} | {k[1]} |" for c, k in res['between_models_run1']['per_code_presence_kappa'].items()]
L += ['', '## 各モデルの2回の安定性', '']
for m in ('A', 'B'):
    s = res['stability_run1_vs_run2'][m]
    L.append(f"- {MODEL[m]}：主な大区分 κ={s['primary_major_kappa'][0]}（一致率 {s['primary_major_agreement']}）、E の有無 κ={s['any_E_kappa'][0]}")
p = res['physician_case_codes']
L += ['', '## 医師のケース単位コードとの整合', '',
      f"- 医師が選んだコード {p['physician_selected']} 件のうち、2モデルがそのケースのどこかの単位に付けたもの {p['both']} 件（{p['recall_of_physician_codes']}）。2モデルが付けたコード {p['models_both_found']} 件のうち医師も選んでいたもの（{p['precision_vs_physician_codes']}）。κ={p['kappa'][0]}",
      '', '## 大区分ごとの件数（2モデルが一致したもの。暫定）', '', '| 単位 | 大区分 | 件数 |', '| --- | --- | ---: |']
for k, v in res['provisional_counts'].items():
    t, mj = k.split('|')
    L.append(f'| {t} | {mj} | {v} |')
L += ['', '## 本文の変更の単位：変更の出所 × 大区分', '', '| 出所 | 大区分 | 件数 |', '| --- | --- | ---: |']
for k, v in res['criterion_text_by_source'].items():
    s, mj = k.split('|')
    L.append(f'| {s} | {mj} | {v} |')
L += ['', '## コード別（2モデルとも付けた単位の数）', '', '| コード | 件数 |', '| --- | ---: |']
L += [f'| {c} | {n} |' for c, n in res['consensus_code_counts'].items()]
(R9 / 'summary.md').write_text('\n'.join(L) + '\n', encoding='utf-8')
print('\n'.join(L))
