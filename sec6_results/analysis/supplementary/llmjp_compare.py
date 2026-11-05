"""LLM-jp-4 vs LLM-jp 4.1: answer length, thinking tokens, short/refusal answers (T/L answers only)."""
import json
import re
import statistics as st
from collections import Counter
from pathlib import Path

R = Path('<EXPERIMENT_ROOT>/runs')
RUNS = {'llmjp4_8b_thinking': 'answers_v9_new/ans_llmjp4_8b_thinking', 'llmjp4_8b_instruct': 'answers_v12_new/ans_llmjp4_8b_instruct',
        'llmjp41_8b': 'answers_v4/ans_llmjp41_8b', 'llmjp41_33b': 'answers_v4/ans_llmjp41_33b', 'llmjp41_32b_a3b': 'answers_v4/ans_llmjp41_32b_a3b',
        'llmjp41_33b_low': 'answers_v15_new/ans_llmjp41_33b_low', 'llmjp41_32b_a3b_low': 'answers_v15_new/ans_llmjp41_32b_a3b_low',
        'qwen3_8b': 'answers_v12_new/ans_qwen3_8b_split', 'gemma4_31b': 'answers_v4/ans_gemma4_31b'}
REFUSE = re.compile(r'お応えできません|お答えできません|回答できません|申し訳ありませんが')
out = {}
for m, d in RUNS.items():
    p = R / d
    if not (p / 'results.jsonl').exists():
        print(m, 'missing', d); continue
    tl = {t['task_id'] for t in map(json.loads, open(p / 'tasks.jsonl', encoding='utf-8'))
          if any(u['condition'] in ('T', 'L') for u in t.get('uses', [{'condition': t.get('condition')}]))}
    rs = [r for r in map(json.loads, open(p / 'results.jsonl', encoding='utf-8')) if r['task_id'] in tl]
    L = [len(r.get('text') or '') for r in rs]
    outp = [(r.get('usage') or {}).get('output') or 0 for r in rs]
    reas = [(r.get('usage') or {}).get('reasoning') for r in rs]
    short = [r for r in rs if len((r.get('text') or '').strip()) < 100]
    kinds = Counter('はい/いいえだけ' if re.fullmatch(r'\s*(はい|いいえ)[。、!！]?\s*', r.get('text') or '') else
                    '断り' if REFUSE.search(r.get('text') or '') else
                    '質問返し' if (r.get('text') or '').strip().endswith(('？', '?')) else 'その他' for r in short)
    out[m] = {'n': len(rs), 'median_chars': st.median(L), 'p10_chars': sorted(L)[len(L) // 10], 'median_output_tok': st.median(outp),
              'median_reasoning_tok': st.median([x for x in reas if x is not None]) if any(x is not None for x in reas) else None,
              'reasoning_zero_share': round(sum(1 for x in reas if x == 0) / len(rs), 3), 'short_lt100': len(short), 'short_kinds': dict(kinds),
              'finish': dict(Counter(r.get('finish') for r in rs))}
    print(m, json.dumps(out[m], ensure_ascii=False))
