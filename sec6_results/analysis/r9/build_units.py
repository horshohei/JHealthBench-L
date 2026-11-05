"""Build the R9 units: every place where the localized version L differs from the translation-only version T.

Units: criteria whose text differs (771), deleted criteria (18), added criteria (10), changed conversation turns (53).
Point changes (14) are a flag on the criterion units (all 14 also change text). Each unit carries the existing
records (plan A): the physician's case-level strategy codes, the LLM draft's adaptation type and case summary, the
first physician's choice / note / rewrite, the two follow-up reviewers' quality flags and notes, and the meta-review.

uv run --no-project python r9/build_units.py   ->  r9/units.jsonl, r9/units_summary.json
uv run --no-project python r9/build_units.py --data data_v3 --meta-release v3 --out r9/v3   (2026-10-19, corrected L)
"""
import argparse
import collections
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
STEPS = BASE.parents[2] / 'steps'
_ap = argparse.ArgumentParser()
_ap.add_argument('--data', default='data')
_ap.add_argument('--meta-release', default='v2')
_ap.add_argument('--out', default='r9')
_ap.add_argument('--expected-text', type=int, default=771)
_a = _ap.parse_args()
DATA = BASE / _a.data
OUT = BASE / _a.out


def jl(p):
    with open(p, encoding='utf-8') as f:
        return [json.loads(x) for x in f if x.strip()]


cmap = jl(DATA / 'criteria_map.jsonl')
cases = {r['case_label']: r for r in jl(DATA / 'case_map.jsonl')}
ds = {c: {r['case_label']: r for r in jl(DATA / f'{c}.jsonl')} for c in ('T', 'L')}
gr = {c['case_label']: c for c in json.load(open(STEPS / '05_response_grading/releases/v1/grading_returns.json', encoding='utf-8'))['cases']}
meta = jl(STEPS / f'06_metareview/releases/{_a.meta_release}/criteria.jsonl')
meta_by = {}
for m in meta:
    meta_by[(m['ケースID'], m['基準ID'])] = m


def meta_key(r):
    cid = r['criterion_id']
    if cid.startswith('grader_'):  # grader_B_add_001 -> B:addition_1
        _, slot, _, n = cid.split('_')
        return (r['case_label'], f'{slot}:addition_{int(n)}')
    return (r['case_label'], r.get('source_criterion_id') or cid)


def stage1(case):
    revs = gr[case]['localization_reviews']
    return revs[0] if revs else {}


def grader_notes(case, crit):
    out = []
    for g in gr[case].get('grader_reviews', []):
        for e in g.get('evaluations', []):
            if e['criterion_id'] == crit:
                out.append({'slot': g['reviewer_slot'], 'quality': e.get('quality'), 'note': e.get('note'),
                            'corrected_text': e.get('corrected_text')})
    return out


def source_of_change(r, m):
    if r['row_type'] != 'original':
        return 'metareview' if r['row_type'].startswith('added_metareview') else 'physician'
    if r.get('deleted_in_L'):
        return 'physician' if r.get('stage1_disposition') == 'deleted' or (r.get('stage1_label') or '').endswith('削除') else 'metareview'
    if r.get('metareview_text_changed') or r.get('metareview_points_changed'):
        return 'metareview'
    s = r.get('stage1_selected_source')
    if s in ('human_correction', 'human_addition'):
        return 'physician'
    if s == 'localized_proposal':
        return 'llm'
    return 'other'


units = []
for r in cmap:
    case = r['case_label']
    kind = ('criterion_added' if r['row_type'] != 'original' else
            'criterion_deleted' if r.get('deleted_in_L') else
            'criterion_text' if r.get('text_changed_T_vs_L') else None)
    if kind is None:
        continue
    m = meta_by.get(meta_key(r), {})
    s1 = stage1(case)
    s1r = next((x for x in s1.get('rubrics', []) if x['criterion_id'] == r['criterion_id']), {})
    llm = gr[case].get('llm_localization') or {}
    llmr = next((x for x in llm.get('rubrics', []) if x['criterion_id'] == r['criterion_id']), {})
    units.append({
        'unit_id': f"{case}:crit:{r['criterion_id']}", 'unit_type': kind, 'case_label': case,
        'criterion_id': r['criterion_id'], 'polarity': r.get('polarity'),
        'points_T': r.get('points_O'), 'points_L': r.get('points_L'), 'points_changed': bool(r.get('points_changed')),
        'text_O': r.get('text_O'), 'text_T': r.get('text_T'), 'text_L': r.get('text_L'),
        'case_strategy_codes': r.get('case_strategy_codes') or [],
        'llm_adaptation_type': r.get('llm_adaptation_type'), 'llm_citation_ids': llmr.get('citation_ids'),
        'llm_case_rubric_summary': llm.get('rubric_summary'),
        'stage1_label': r.get('stage1_label'), 'stage1_selected_source': r.get('stage1_selected_source'),
        'stage1_note': s1r.get('note'), 'stage1_corrected_text': s1r.get('corrected_text'),
        'stage1_case_note': ((s1.get('case_review') or {}).get('note'), (s1.get('rubric_overall') or {}).get('note')),
        'grader_reviews': grader_notes(case, r['criterion_id']),
        'metareview': {k: m.get(k) for k in ('判定区分', '採否', '確認事項', '処理注記', '入力_reason', '本文変更', '配点変更')} if m else None,
        'change_source': source_of_change(r, m),
    })

for case, c in cases.items():
    for tid in c['turns_changed_T_vs_L']:
        tT, tL = ds['T'][case]['prompt'][tid], ds['L'][case]['prompt'][tid]
        s1 = stage1(case)
        s1c = next((x for x in s1.get('conversation', []) if int(x['turn_id']) == int(tid)), {})
        llm = gr[case].get('llm_localization') or {}
        llmc = next((x for x in llm.get('conversation', []) if int(x['turn_id']) == int(tid)), {})
        src = gr[case]['source']['conversation'][tid] if tid < len(gr[case]['source']['conversation']) else {}
        sel = s1c.get('label') or ''
        units.append({
            'unit_id': f'{case}:turn:{tid}', 'unit_type': 'conversation_turn', 'case_label': case, 'turn_id': tid,
            'role': tL['role'], 'text_O': src.get('original_text'), 'text_T': tT['content'], 'text_L': tL['content'],
            'case_strategy_codes': c.get('strategy_codes') or [],
            'llm_adaptation_type': llmc.get('adaptation_type'), 'llm_case_conversation_summary': llm.get('conversation_summary'),
            'stage1_label': sel, 'stage1_note': s1c.get('note'), 'stage1_corrected_text': s1c.get('corrected_text'),
            'grader_conversation_qc': [{'slot': g['reviewer_slot'], **(g.get('conversation_qc') or {})} for g in gr[case].get('grader_reviews', [])],
            'change_source': 'physician' if '人手' in sel else 'llm' if 'ローカライズ' in sel else 'metareview',
        })

OUT.mkdir(exist_ok=True)
with open(OUT / 'units.jsonl', 'w', encoding='utf-8') as f:
    for u in units:
        f.write(json.dumps(u, ensure_ascii=False) + '\n')
summary = {
    'n_units': len(units),
    'by_type': dict(collections.Counter(u['unit_type'] for u in units)),
    'points_changed_flags': sum(1 for u in units if u.get('points_changed')),
    'by_type_and_source': {f"{k[0]}|{k[1]}": v for k, v in collections.Counter((u['unit_type'], u['change_source']) for u in units).items()},
    'data': _a.data, 'meta_release': _a.meta_release,
    'expected': {'criterion_text': _a.expected_text, 'criterion_deleted': 18, 'criterion_added': 10, 'conversation_turn': 53, 'points_changed_flags': 14},
}
summary['matches_expected'] = all(summary['by_type'].get(k) == v for k, v in summary['expected'].items() if k != 'points_changed_flags') and summary['points_changed_flags'] == 14
(OUT / 'units_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=1))
