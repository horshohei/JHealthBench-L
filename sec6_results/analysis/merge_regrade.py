"""Merge a re-grading of corrected criteria into a full grading run (D19, 2026-10-16).

For each answer model: take the full run (e.g. runs/grades_v6/qwen38_grader_none__<m>), replace the tasks and
results whose task_id appears in the fix run (e.g. runs/grades_v7_fix/qwen38_grader_none__<m>), and write a merged
run directory that the analysis reads like any other run. Task ids do not contain the criterion text, so the same
id names the same (answer, condition, case, criterion, repeat) in both runs. The merged manifest records both
sources and takes the dataset hashes of the fix run for the conditions it re-graded.

uv run --no-project python analysis/merge_regrade.py --base runs/grades_v6 --fix runs/grades_v7_fix \
    --out runs/grades_v7_merged [--suffix _s1]
"""
import argparse
import json
import shutil
from pathlib import Path


def read_jsonl(p):
    with open(p, encoding='utf-8') as f:
        return [json.loads(x) for x in f if x.strip()]


def write_jsonl(p, rows):
    with open(p, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


def merge(base, fix, out):
    btasks, ftasks = read_jsonl(base / 'tasks.jsonl'), read_jsonl(fix / 'tasks.jsonl')
    fids = {t['task_id'] for t in ftasks}
    bids = {t['task_id'] for t in btasks}
    if not fids <= bids:
        raise ValueError(f'{fix}: {len(fids - bids)} task ids not in the base run')
    fres = {r['task_id']: r for r in read_jsonl(fix / 'results.jsonl')} if (fix / 'results.jsonl').exists() else {}
    missing = sorted(fids - set(fres))
    if missing:
        raise ValueError(f'{fix}: {len(missing)} re-graded tasks have no result yet (e.g. {missing[0]})')
    out.mkdir(parents=True, exist_ok=False)
    fmap = {t['task_id']: t for t in ftasks}
    write_jsonl(out / 'tasks.jsonl', [fmap.get(t['task_id'], t) for t in btasks])
    write_jsonl(out / 'results.jsonl', [fres[r['task_id']] if r['task_id'] in fids else r for r in read_jsonl(base / 'results.jsonl')])
    shutil.copytree(base / 'inputs', out / 'inputs')
    bm = json.loads((base / 'manifest.json').read_text(encoding='utf-8'))
    fm = json.loads((fix / 'manifest.json').read_text(encoding='utf-8'))
    m = dict(bm)
    m['dataset_sha256'] = {**bm.get('dataset_sha256', {}), **fm.get('dataset_sha256', {})}
    m['merged'] = {'base': str(base.resolve()), 'fix': str(fix.resolve()), 'replaced_tasks': len(fids),
                   'base_table_sha256': bm.get('table_sha256'), 'fix_table_sha256': fm.get('table_sha256'),
                   'fix_criteria': fm.get('criteria')}
    (out / 'manifest.json').write_text(json.dumps(m, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return len(fids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--fix', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    base, fix, out = Path(a.base), Path(a.fix), Path(a.out)
    done = {}
    for f in sorted(fix.iterdir()):
        if not (f / 'tasks.jsonl').exists():
            continue
        b = base / f.name
        if not b.exists():
            raise SystemExit(f'no base run for {f.name}')
        done[f.name] = merge(b, f, out / f.name)
    print(json.dumps(done, ensure_ascii=False))


if __name__ == '__main__':
    main()
