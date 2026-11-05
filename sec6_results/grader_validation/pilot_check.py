"""Check pilot or screening runs against the go/no-go criteria in redo_plan_v2.md and summarise them.

Reads run directories only (nothing is sent). For each run: request verification, completion,
truncation / refusal / parse failures, whether thinking actually happened, prompt-cache reads,
latency and token use, prompt length against the max_tokens reserve, and (Gold runs) agreement
with the physician Gold.

uv run --no-project python scripts/pilot_check.py --table conditions/condition_table_v2.json \
    --out runs/pilot_20261013/pilot_check.json runs/pilot_20261013/gold_* runs/pilot_20261013/ans_*
"""
import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'pipeline'))
from hb import conditions, metrics, runner, tasks  # noqa: E402

JA = re.compile(r'[぀-ヿ一-鿿]')


def reasoning_text(provider, raw):
    if provider in ('vllm_chat', 'openai_chat'):
        msg = ((raw.get('choices') or [{}])[0].get('message') or {})
        return msg.get('reasoning') or msg.get('reasoning_content') or ''
    if provider == 'anthropic_messages':
        return ''.join(c.get('thinking', '') for c in raw.get('content', []) if c.get('type') == 'thinking')
    return ''


def has_thinking_block(provider, raw):
    if provider == 'anthropic_messages':
        return any(c.get('type') in ('thinking', 'redacted_thinking') for c in raw.get('content', []))
    return bool(reasoning_text(provider, raw))


def ts(s):
    return datetime.fromisoformat(s)


def q(vals, p):
    vals = sorted(v for v in vals if v is not None)  # batch results carry no latency
    return vals[min(len(vals) - 1, int(p * len(vals)))] if vals else None


def check_run(d, table):
    d = Path(d)
    man = json.loads((d / 'manifest.json').read_text(encoding='utf-8'))
    m = conditions.model(table, man['model_id'])
    prov = m['provider']
    tk = {t['task_id']: t for t in runner.read_jsonl(d / 'tasks.jsonl')}
    res = {r['task_id']: r for r in runner.read_jsonl(d / 'results.jsonl')}
    fail = runner.read_jsonl(d / 'failures.jsonl')
    att = runner.read_jsonl(d / 'attempts.jsonl')
    ok_att = [a for a in att if a.get('ok')]
    reserve = table['run_policy'].get('prompt_reserve_tokens', 16384)
    out = {'run': d.name, 'model_id': man['model_id'], 'kind': man['kind'], 'tasks': len(tk), 'done': len(res),
           'failed_tasks': len({f['task_id'] for f in fail}), 'attempts': len(att),
           'transport_errors': sum(not a.get('ok') for a in att),
           'truncated_attempts': sum(bool(a.get('truncated')) for a in ok_att),
           'refusal_attempts': sum(bool(a.get('refusal')) for a in ok_att),
           'parse_failed_attempts': sum(a.get('parsed_label', 0) is None for a in ok_att if 'parsed_label' in a),
           'verify_problems': runner.verify_requests(d, table)[:10] if (d / 'requests.jsonl').exists() else ['no requests.jsonl']}
    rate = lambda n, den: round(n / den, 4) if den else None  # noqa: E731
    out['parse_failure_rate'] = rate(out['parse_failed_attempts'], len(ok_att))
    out['truncation_rate'] = rate(out['truncated_attempts'], len(ok_att))
    think = [has_thinking_block(prov, a['raw']) for a in ok_att if 'raw' in a]
    rlen = [len(reasoning_text(prov, a['raw'])) for a in ok_att if 'raw' in a]
    out['thinking_present_share'] = rate(sum(think), len(think))
    out['reasoning_chars_median'] = q(rlen, 0.5)
    out['reasoning_chars_p90'] = q(rlen, 0.9)
    u = [a.get('usage') or {} for a in ok_att]
    for k in ('input', 'output', 'reasoning', 'cached'):
        vals = [x[k] for x in u if isinstance(x.get(k), int)]
        out[f'{k}_tokens_median'] = q(vals, 0.5)
        out[f'{k}_tokens_max'] = max(vals) if vals else None
    out['max_input_vs_reserve'] = f"{out['input_tokens_max']} / {reserve}" if out['input_tokens_max'] is not None else None
    out['input_over_reserve'] = bool(out['input_tokens_max'] and out['input_tokens_max'] > reserve)
    lat = [a['latency_s'] for a in ok_att if a.get('latency_s') is not None]
    out['latency_s_median'] = q(lat, 0.5)
    out['latency_s_p90'] = q(lat, 0.9)
    times = [ts(a['at']) for a in att]
    if len(times) > 1:
        wall = (max(times) - min(times)).total_seconds() + (lat[-1] if lat else 0)
        out['wall_s_approx'] = round(wall, 1)
        out['tasks_per_hour'] = round(len(res) / wall * 3600, 1) if wall else None
    if man['kind'] in ('gold', 'grade'):
        # cache reads: first attempt of every non-first item of each cache group
        first_seen, reads, eligible = set(), 0, 0
        for a in sorted(ok_att, key=lambda a: a['at']):
            g = tk[a['task_id']].get('cache_group')
            if g in first_seen:
                eligible += 1
                reads += bool((a.get('usage') or {}).get('cached'))
            first_seen.add(g)
        out['cache_read_share_non_first'] = rate(reads, eligible) if any('cached' in (a.get('usage') or {}) and (a.get('usage') or {}).get('cached') is not None for a in ok_att) else 'not reported by server'
    if man['kind'] == 'gold':
        gold_rows, _ = tasks.load_dataset(table, 'GOLD')
        cases = {t['case_id'] for t in tk.values()}
        gold = {(r['case_id'], c['criterion_id']): c['criteria_met'] for r in gold_rows if r['case_id'] in cases for c in r['rubrics']}
        items = {r['case_id']: [{'criterion_id': c['criterion_id'], 'points': c['points']} for c in r['rubrics']]
                 for r in gold_rows if r['case_id'] in cases}
        pred = {(tk[t]['case_id'], tk[t]['criterion_id']): r['criteria_met'] for t, r in res.items()}
        rep = metrics.gold_report(pred, gold, items, n_boot=500)
        out['gold'] = {k: rep[k] for k in ('n_items', 'n_missing', 'agreement', 'balanced_f1', 'kappa', 'case_score_mae') if k in rep}
    if man['kind'] == 'answer':
        texts = [r.get('text') or '' for r in res.values()]
        out['answer_chars_median'] = q([len(t) for t in texts], 0.5)
        out['answer_ja_char_share_median'] = q([len(JA.findall(t)) / max(1, len(t)) for t in texts], 0.5)
        out['answer_empty'] = sum(not t.strip() for t in texts)
        out['answer_think_tag_leak'] = sum(('<think>' in t or '</think>' in t) for t in texts)
        fin = defaultdict(int)
        for r in res.values():
            fin[str(r.get('finish'))] += 1
        out['finish'] = dict(fin)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--table', default=str(BASE / 'conditions' / 'condition_table_v2.json'))
    ap.add_argument('--out', required=True)
    ap.add_argument('runs', nargs='+')
    a = ap.parse_args()
    table = conditions.load_table(a.table)
    rows = [check_run(r, table) for r in a.runs if (Path(r) / 'manifest.json').exists() and (Path(r) / 'attempts.jsonl').exists()]
    Path(a.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    keys = ['run', 'done', 'tasks', 'failed_tasks', 'parse_failure_rate', 'truncation_rate', 'refusal_attempts',
            'thinking_present_share', 'reasoning_chars_median', 'output_tokens_median', 'input_tokens_max',
            'cache_read_share_non_first', 'latency_s_median', 'tasks_per_hour']
    for r in rows:
        line = {k: r.get(k) for k in keys}
        if 'gold' in r:
            line['gold'] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r['gold'].items()}
        line['verify'] = 'OK' if not r['verify_problems'] else r['verify_problems'][:2]
        print(json.dumps(line, ensure_ascii=False))


if __name__ == '__main__':
    main()
