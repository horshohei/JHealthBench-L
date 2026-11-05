"""Counts behind Table 2 (changed criteria T -> L): item classes from data_v3 and the provenance codes of text-changed items."""
import json
import sys
from collections import Counter
from pathlib import Path

EXP = Path('<EXPERIMENT_ROOT>')
sys.path.insert(0, str(EXP / 'analysis'))
import hb_analysis as H  # noqa: E402

data = H.Data(EXP / 'data_v3')
cls = Counter(data.item_class(*k) for k in data.items)
print('item classes:', dict(cls))
tc = [k for k in data.items if data.item_class(*k) == 'text_changed']
print('text changed with points changed:', sum(data.items[k]['points_T'] != data.items[k]['points_L'] for k in tc))
print('points changed (any matched):', sum(data.items[k]['kind'] == 'matched' and data.items[k]['points_T'] != data.items[k]['points_L'] for k in data.items))
prov = {(r['case_id'], r['criterion_id']): r['codes'] for r in map(json.loads, open(EXP / 'analysis/labels_provenance_v7/items.jsonl', encoding='utf-8'))}
print('provenance of text-changed items:', dict(Counter('+'.join(sorted(prov.get(k, ['(none)']))) for k in tc).most_common()))
print('provenance of added items:', dict(Counter('+'.join(sorted(prov.get(k, ['(none)']))) for k in data.items if data.item_class(*k) == 'added')))
print('provenance of deleted items:', dict(Counter('+'.join(sorted(prov.get(k, ['(none)']))) for k in data.items if data.item_class(*k) == 'deleted')))
r9 = {}
for r in map(json.loads, open(EXP / 'r9/v3/labels_merged.jsonl', encoding='utf-8')):
    if r['unit_type'] == 'criterion_text':
        cl, _, cid = r['unit_id'].split(':', 2)
        r9[(cl, cid)] = r['change_source']
lab = {k: (data.case_label[k[0]], k[1]) for k in tc}
print('R9 criterion_text units:', len(r9), '; text-changed items without an R9 unit:', [lab[k] for k in tc if lab[k] not in r9],
      '; R9 units not text-changed:', [k for k in r9 if k not in set(lab.values())])
print('R9 change_source of text-changed items:', dict(Counter(r9.get(lab[k]) for k in tc)))
