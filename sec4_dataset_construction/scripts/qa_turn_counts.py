"""Reconstruct the conversation-turn counts of Table 1 (79 cases, 09_09 excluded) from Step5 grading_returns.json and data_v3.

(1) LLM draft identical to the translation vs edited/adapted, under several definitions.
(2) Turns changed at the metareview: L conversation vs the stage-1 adopted conversation, turn by turn.
"""
import json
from collections import Counter

U = '<WORKSPACE>'
EXP = f'{U}/Scratch/2026-10-13/experiment_redo'
d = json.load(open(f'{U}/steps/05_response_grading/releases/v1/grading_returns.json', encoding='utf-8'))
cases = [c for c in d['cases'] if c['case_label'] != '09_09']
L = {json.loads(x)['prompt_id']: json.loads(x) for x in open(f'{EXP}/data_v3/L.jsonl', encoding='utf-8')}
T = {json.loads(x)['prompt_id']: json.loads(x) for x in open(f'{EXP}/data_v3/T.jsonl', encoding='utf-8')}
s = lambda x: (x or '').strip()
cnt = Counter()
rows = []
for c in cases:
    src = {t['turn_id']: t for t in c['source']['conversation']}
    llm = {t['turn_id']: t for t in c['llm_localization']['conversation']}
    adp = {t['turn_id']: t for t in c['adopted_localization']['conversation']}
    tconv = T[c['case_id']]['prompt']
    for i, (tid, t) in enumerate(src.items()):
        same = s(llm[tid]['content']) == s(t['translated_text'])
        same_T = s(llm[tid]['content']) == s(tconv[i]['content']) if i < len(tconv) else None
        sel = adp[tid]['selected_source']
        cnt['turns'] += 1
        cnt[f'draft==translation:{same}'] += 1
        cnt[f'draft==T(data_v3):{same_T}'] += 1
        cnt[f"adaptation_type:{llm[tid].get('adaptation_type')}"] += 1
        cnt[f'selected:{sel}'] += 1
        # exclusive breakdown (rewritten first, then identical draft, then which side was chosen)
        ex = 'rewritten' if sel == 'human_correction' else 'identical' if same else 'translation_only' if sel == 'translation' else 'llm_only'
        cnt[f'exclusive:{ex}'] += 1
        if sel == 'human_correction':
            cnt[f'rewritten_with_identical_draft:{same}'] += 1
print(json.dumps(dict(sorted(cnt.items())), ensure_ascii=False, indent=1))

# (2) metareview: L vs adopted conversation
changed = []
for c in cases:
    adp = c['adopted_localization']['conversation']
    lc = L[c['case_id']]['prompt']
    if len(adp) != len(lc):
        changed.append((c['case_label'], 'turn count differs', len(adp), len(lc)))
        continue
    for i, (a_, l_) in enumerate(zip(adp, lc)):
        if a_['content'] != l_['content']:
            changed.append((c['case_label'], i, a_['role'], 'strip-equal' if s(a_['content']) == s(l_['content']) else 'text differs'))
print('metareview turn changes (L vs adopted):', len(changed))
for x in changed:
    print(' ', x)
