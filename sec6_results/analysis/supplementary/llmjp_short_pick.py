"""Pick LLM-jp 4.1 33B T/L answers under 100 characters: case, last user turn, answer, thinking head, request body hash."""
import json
from pathlib import Path

R = Path('<EXPERIMENT_ROOT>/runs/answers_v4/ans_llmjp41_33b')
tasks = {t['task_id']: t for t in map(json.loads, open(R / 'tasks.jsonl', encoding='utf-8'))}
convs = {c['conv_key']: c['messages'] for c in map(json.loads, open(R / 'inputs' / 'conversations.jsonl', encoding='utf-8'))}
last = {}
for r in map(json.loads, open(R / 'attempts.jsonl', encoding='utf-8')):
    if r.get('ok'):
        last[r['task_id']] = r
rows = []
for tid, r in last.items():
    t = tasks[tid]
    if not any(u['condition'] in ('T', 'L') for u in t['uses']):
        continue
    msg = ((r.get('raw') or {}).get('choices') or [{}])[0].get('message') or {}
    text = (msg.get('content') or '').strip()
    if len(text) < 100:
        rows.append((len(text), tid, t, text, msg.get('reasoning') or ''))
rows.sort(key=lambda x: x[0])
print(len(rows), 'short T/L answers')
for n, tid, t, text, reas in rows:
    m = convs[t['conv_key']]
    print(f"--- {tid} | {','.join(u['condition'] + ':' + u['case_label'] for u in t['uses'])} | turns {len(m)} | answer {n} chars")
    print('  user:', m[-1]['content'][:160].replace('\n', ' '))
    print('  answer:', text.replace('\n', ' ')[:100])
    print('  thinking:', reas.replace('\n', ' ')[:220])
