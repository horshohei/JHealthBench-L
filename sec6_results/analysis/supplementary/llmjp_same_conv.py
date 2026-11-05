"""For the probed conversations: what the other LLM-jp models answered (stored answers, same conversation key)."""
import json
from pathlib import Path

R = Path('<EXPERIMENT_ROOT>/runs')
IDS = ['d26d48b4a3fc9ff7', '9805d95235008a7f', '0820b439377c338c', '16a7560fa8d0497b', 'f37fc9fb9d93252b', '2b76ce89f7dd5228', 'ae58dd0d86ae7e35']
conv = {t['task_id'].split(':')[2]: t['conv_key'] for t in map(json.loads, open(R / 'answers_v4/ans_llmjp41_33b/tasks.jsonl', encoding='utf-8'))}
for name, d in (('llmjp4_8b_thinking', 'answers_v9_new/ans_llmjp4_8b_thinking'), ('llmjp41_8b', 'answers_v4/ans_llmjp41_8b'),
                ('llmjp41_32b_a3b', 'answers_v4/ans_llmjp41_32b_a3b'), ('gptoss120b', 'answers_v4/ans_gptoss120b')):
    tk = {t['task_id']: t['conv_key'] for t in map(json.loads, open(R / d / 'tasks.jsonl', encoding='utf-8'))}
    res = {r['task_id']: r.get('text') or '' for r in map(json.loads, open(R / d / 'results.jsonl', encoding='utf-8'))}
    print('==', name)
    for h in IDS:
        texts = [res[t] for t, c in tk.items() if c == conv.get(h) and t in res]
        print(f'  {h}: lengths {[len(x) for x in texts]} | {texts[0][:60].replace(chr(10), " ") if texts else ""}')
