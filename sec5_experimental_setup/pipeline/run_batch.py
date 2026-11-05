"""Batch-API CLI for runs built with run.py (build-answers / build-gold / build-grades).

  uv run python pipeline/run_batch.py --table conditions/condition_table_v3.json prepare --run RUN [--max-requests N]
  uv run python pipeline/run_batch.py --table ... submit  --run RUN      # creates the provider batches (needs the API key)
  uv run python pipeline/run_batch.py --table ... poll    --run RUN      # refreshes their status
  uv run python pipeline/run_batch.py --table ... collect --run RUN      # imports ended batches; failed items stay pending
  uv run python pipeline/run_batch.py --table ... auto    --run RUN --poll 300   # prepare/submit/poll/collect until done
  uv run python pipeline/run_batch.py --table ... verify  --run RUN      # batch input lines and stored requests vs the table
  uv run python pipeline/run_batch.py --table ... state   --run RUN

Do not run run.py execute and the batch path on the same run at the same time.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from hb import batch, conditions, runner  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--table', default=str(HERE.parent / 'conditions' / 'condition_table_v3.json'))
    sub = p.add_subparsers(dest='cmd', required=True)
    for name in ('prepare', 'submit', 'poll', 'collect', 'auto', 'verify', 'state'):
        s = sub.add_parser(name)
        s.add_argument('--run', required=True)
        if name in ('prepare', 'auto'):
            s.add_argument('--max-requests', type=int)
        if name == 'auto':
            s.add_argument('--poll', type=int, default=300)
            s.add_argument('--max-rounds', type=int)
    a = p.parse_args(argv)
    table = conditions.load_table(a.table)
    if a.cmd == 'verify':
        problems = runner.verify_requests(a.run, table) + batch.verify_batch_inputs(a.run, table)
        print('OK' if not problems else json.dumps(problems[:50], ensure_ascii=False, indent=1))
        return 1 if problems else 0
    br = batch.BatchRun(a.run, table)
    if a.cmd == 'prepare':
        print(json.dumps(br.prepare(a.max_requests)))
    elif a.cmd == 'submit':
        print(json.dumps(br.submit()))
    elif a.cmd == 'poll':
        print(json.dumps(br.poll()))
    elif a.cmd == 'collect':
        print(json.dumps(br.collect()))
    elif a.cmd == 'auto':
        print(json.dumps(br.run_until_done(a.poll, a.max_rounds, max_requests=a.max_requests)))
    elif a.cmd == 'state':
        st = br.state()
        print(json.dumps({'pending': len(br.pending(st)),
                          'chunks': [{k: c.get(k) for k in ('name', 'status', 'provider_status', 'n', 'provider_batch_id', 'collected')}
                                     for c in st['chunks']]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
