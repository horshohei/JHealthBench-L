"""Which stage deleted each T-only criterion: stage-1 review labels (Step5 grading_returns.json) vs R9 v3 change_source."""
import json
from collections import Counter

U = '<WORKSPACE>'
d = json.load(open(f'{U}/steps/05_response_grading/releases/v1/grading_returns.json', encoding='utf-8'))
stage1_del, disp = {}, Counter()
for c in d['cases']:
    if c['case_label'] == '09_09':
        continue
    for rv in c['localization_reviews']:
        for r in rv['rubrics']:
            if '削除' in str(r.get('label')):
                stage1_del[(c['case_label'], r['criterion_id'])] = (rv['reviewer_slot'], r['label'], r.get('note'))
    for a in c['adopted_localization']['rubrics']:
        disp[(a.get('disposition'), a.get('selected_source'))] += 1
        if (c['case_label'], a['criterion_id']) in stage1_del:
            stage1_del[(c['case_label'], a['criterion_id'])] += (('adopted', a.get('disposition'), a.get('selected_source'), a.get('selection_resolution')),)
r9 = {}
for line in open(f'{U}/Scratch/2026-10-13/experiment_redo/r9/v3/units.jsonl', encoding='utf-8'):
    u = json.loads(line)
    if u['unit_type'] == 'criterion_deleted':
        r9[(u['case_label'], u['criterion_id'])] = (u['change_source'], u.get('stage1_label'))
print('stage-1 deletions (09_09 excluded):', len(stage1_del))
for k, v in sorted(stage1_del.items()):
    print(' ', k, v, '| R9:', r9.get(k))
print('R9 physician-deleted not among stage-1 deletions:', [k for k, v in r9.items() if v[0] == 'physician' and k not in stage1_del])
print('stage-1 deletions that R9 attributes to metareview or does not list:', {k: r9.get(k) for k in stage1_del if (r9.get(k) or ('-',))[0] != 'physician'})
print('adopted dispositions:', dict(disp))
