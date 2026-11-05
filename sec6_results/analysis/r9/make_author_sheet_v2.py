"""Draw the 150 units for the authors' check, centred on the units that actually moved the evaluation (rule of
2026-10-19, approved by the user), and write the check sheet.

Priority population P (each unit counted once):
  (a) criteria whose text changed, in cases with the same conversation, where the same answers' grades moved
      together: analysis/out_main_v7/item_flips/items.jsonl, kind=text_changed, flips.n>=40, >=10 one way and
      <=2 the other way (71 items);
  (b) criteria changed by a person: analysis/labels_provenance_v7/items.jsonl, codes contain Human:stage1 or
      Human:metareview (116 items);
  (c) deleted (18) and added (10) criteria.
Strata = category from the two models' run1 (consensus major E / J / G / NONE, 'disagree'; S for deleted/added).
If |P| > 150, 150 are drawn from P by stratum: every non-empty stratum gets min(size, 4), the rest proportional to
size (largest remainder). If |P| <= 150, all of P is taken and the remaining slots are drawn from the other units
with the same stratified rule. Seed fixed before drawing: 20261019. The sheet does not show grades or flips.

uv run python Scratch/2026-10-13/experiment_redo/r9/make_author_sheet_v2.py
-> r9/author_check_150.xlsx / .csv / _design.json  (refuses to overwrite)
"""
import collections
import csv
import json
import random
from pathlib import Path

R9 = Path(__file__).resolve().parent
BASE = R9.parent
V3 = R9 / 'v3'
SEED, TOTAL, FLOOR = 20261019, 150, 4
OUT = {ext: R9 / f'author_check_150.{ext}' for ext in ('xlsx', 'csv')}
DESIGN = R9 / 'author_check_150_design.json'
for p in [*OUT.values(), DESIGN]:
    if p.exists():
        raise SystemExit(f'{p} exists; not overwriting')


def jl(p):
    return [json.loads(x) for x in open(p, encoding='utf-8') if x.strip()]


units = {u['unit_id']: u for u in jl(V3 / 'units.jsonl')}
merged = {r['unit_id']: r for r in jl(V3 / 'labels_merged.jsonl')}


def uid(r):
    return f"{r['case_label']}:crit:{r['criterion_id']}"


def sysflip(r):
    f = r.get('flips')
    return bool(f) and f['n'] >= 40 and max(f['no_to_yes'], f['yes_to_no']) >= 10 and min(f['no_to_yes'], f['yes_to_no']) <= 2


set_a = {uid(r) for r in jl(BASE / 'analysis/out_main_v7/item_flips/items.jsonl') if r['kind'] == 'text_changed' and sysflip(r)}
set_b = {uid(r) for r in jl(BASE / 'analysis/labels_provenance_v7/items.jsonl')
         if any(c in ('Human:stage1', 'Human:metareview') for c in r.get('codes', []))}
set_c = {k for k, u in units.items() if u['unit_type'] in ('criterion_deleted', 'criterion_added')}
missing = sorted((set_a | set_b) - set(units))
if missing:
    raise SystemExit(f'units not found in r9/v3/units.jsonl: {missing[:5]}')
prio = set_a | set_b | set_c


def category(k):
    u, r = units[k], merged.get(k)
    if u['unit_type'] in ('criterion_deleted', 'criterion_added'):
        return 'S'
    return (r or {}).get('consensus_major') or 'unlabelled'


def allocate(pool, n):
    strata = collections.defaultdict(list)
    for k in pool:
        strata[category(k)].append(k)
    keys = sorted(strata)
    alloc = {s: min(len(strata[s]), FLOOR) for s in keys}
    if sum(alloc.values()) > n:
        raise SystemExit('floor exceeds the sample size')
    cap = {s: len(strata[s]) - alloc[s] for s in keys}
    pool_left = sum(cap.values())
    rest = n - sum(alloc.values())
    quota = {s: rest * cap[s] / pool_left if pool_left else 0 for s in keys}
    for s in keys:
        alloc[s] += int(quota[s])
    left = n - sum(alloc.values())
    for s in sorted(keys, key=lambda s: -(quota[s] - int(quota[s]))):
        if left <= 0:
            break
        if alloc[s] < len(strata[s]):
            alloc[s] += 1
            left -= 1
    return strata, alloc


rng = random.Random(SEED)
picked, design_strata = [], {}
if len(prio) > TOTAL:
    strata, alloc = allocate(prio, TOTAL)
    for s in sorted(strata):
        picked += rng.sample(sorted(strata[s]), alloc[s])
        design_strata[f'priority|{s}'] = {'size': len(strata[s]), 'drawn': alloc[s]}
else:
    picked = sorted(prio)
    for s, n in collections.Counter(category(k) for k in prio).items():
        design_strata[f'priority|{s}'] = {'size': n, 'drawn': n}
    others = sorted(set(units) - prio)
    if TOTAL - len(picked) > 0:
        strata, alloc = allocate(others, TOTAL - len(picked))
        for s in sorted(strata):
            picked += rng.sample(sorted(strata[s]), alloc[s])
            design_strata[f'other|{s}'] = {'size': len(strata[s]), 'drawn': alloc[s]}
rng.shuffle(picked)

KIND = {'criterion_text': '基準の本文の変更', 'criterion_deleted': '基準の削除', 'criterion_added': '基準の追加', 'conversation_turn': '会話のターン'}
SRC = {'llm': 'LLM案', 'physician': '医師の書き直し', 'metareview': 'メタレビュー'}
header = ['No', '単位ID', '単位の種類', '変更の出所', '配点（T→L）', '原文', 'T（翻訳だけの版）', 'L（ローカライズ版）',
          '医師のケース単位の修正方針', 'モデルA（gpt-oss-120b）のコード', 'モデルA 大区分', 'モデルA 理由',
          'モデルB（DeepSeek V4 Flash）のコード', 'モデルB 大区分', 'モデルB 理由',
          '【判定】大区分（E/J/G/NONE。削除・追加は理由の大区分）', '【判定】コード（任意）', '【判定】A は正しいか（○/×）', '【判定】B は正しいか（○/×）', 'コメント']
rows = []
for i, k in enumerate(picked, 1):
    u, r = units[k], merged.get(k) or {}
    a, b = r.get('A1') or {}, r.get('B1') or {}
    pts = f"{u.get('points_T')}→{u.get('points_L')}" if u['unit_type'].startswith('criterion') else ''
    rows.append([i, k, KIND[u['unit_type']], SRC.get(u['change_source'], u['change_source']), pts,
                 u.get('text_O') or '', u.get('text_T') or '', u.get('text_L') or '', ', '.join(u.get('case_strategy_codes') or []),
                 ', '.join(a.get('codes', [])), a.get('primary_major', ''), a.get('reason', ''),
                 ', '.join(b.get('codes', [])), b.get('primary_major', ''), b.get('reason', ''), '', '', '', '', ''])

with open(OUT['csv'], 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(header)
    w.writerows(rows)
import openpyxl  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
wb = openpyxl.Workbook()
ws = wb.active
ws.title = '確認150'
ws.append(header)
for row in rows:
    ws.append(row)
for j, wdt in enumerate([5, 24, 12, 12, 9, 40, 40, 40, 22, 22, 8, 36, 22, 8, 36, 16, 18, 10, 10, 30], 1):
    ws.column_dimensions[openpyxl.utils.get_column_letter(j)].width = wdt
for c in ws[1]:
    c.font = Font(bold=True)
    c.alignment = Alignment(wrap_text=True, vertical='top')
fill = PatternFill('solid', fgColor='FFF2CC')
for row in ws.iter_rows(min_row=2):
    for c in row:
        c.alignment = Alignment(wrap_text=True, vertical='top')
    for c in row[15:20]:
        c.fill = fill
ws.freeze_panes = 'C2'
guide = wb.create_sheet('説明')
for line in ['各行は、翻訳だけの版（T）からローカライズ版（L）への1つの変更です。黄色の列に記入してください。',
             '大区分：E＝日本の環境に合わせた変更、J＝日本語の表現だけ（満たす条件は同じ）、G＝日本に固有でない基準の改善（明確化・厳しさの変更・誤りの修正・分割・医学的内容）、NONE＝実質的な変更なし。',
             '削除・追加の行は、削除・追加した理由の大区分を記入してください。コードの定義は r9/codebook.md。',
             'モデルA・Bの判定が正しいかも○/×で記入してください。',
             f'抽出：評価を実際に動かした単位（同じ回答の判定がそろって変わった本文変更の項目）、人が変えた項目、削除・追加を優先し、2モデルの合意の大区分で層化（seed {SEED}）。']:
    guide.append([line])
guide.column_dimensions['A'].width = 120
wb.save(OUT['xlsx'])

design = {
    'rule': 'priority population = (a) systematic-flip text changes (out_main_v7 item_flips: kind=text_changed, flips.n>=40, >=10 one way, <=2 other) + (b) human-changed (labels_provenance_v7 codes Human:stage1/Human:metareview) + (c) deleted/added; stratified by consensus major (S for deleted/added), floor 4 per stratum, rest proportional (largest remainder); if |P|<=150 take all and fill from the other units with the same rule',
    'seed': SEED, 'total': TOTAL, 'floor_per_stratum': FLOOR,
    'population': {'a_systematic_flips': len(set_a), 'b_human_changed': len(set_b), 'c_deleted_added': len(set_c),
                   'a_and_b': len(set_a & set_b), 'priority_union': len(prio)},
    'strata': design_strata,
    'drawn_by_population': {'a': sum(1 for k in picked if k in set_a), 'b': sum(1 for k in picked if k in set_b),
                            'c': sum(1 for k in picked if k in set_c), 'outside_priority': sum(1 for k in picked if k not in prio)},
    'drawn_by_category': dict(collections.Counter(category(k) for k in picked)),
    'unit_ids': picked,
}
DESIGN.write_text(json.dumps(design, ensure_ascii=False, indent=1), encoding='utf-8')
print(json.dumps({k: v for k, v in design.items() if k != 'unit_ids'}, ensure_ascii=False, indent=1))
