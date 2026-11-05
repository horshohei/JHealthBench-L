#!/usr/bin/env bash
# 軸の付け直し（原典の5軸の定義を日本語に整えた版、kind AXJ）を1モデル分、2回回す（研究室の API に届く計算機で実行、再開可）。
# 条件表 v15 の設定のまま。
# Usage: [ENDPOINT=http://host:port] run_axj_one.sh <ROOT> <model_id> <short> <workers>
#   基準 1,844 単位（labels_axes_v2/units.jsonl。v1 と同じ単位）→ ROOT/analysis/labels_axes_v2/runs/<short>_AXJ_run{1,2}
set -uo pipefail
ROOT=$1 MODEL=$2 SHORT=$3 W=$4
HERE=$ROOT/axes_v1
T=$ROOT/conditions/condition_table_v15.json
U=$ROOT/analysis/labels_axes_v2/units.jsonl
O=$ROOT/analysis/labels_axes_v2/runs
PY=${PY:-python3}
mkdir -p "$O"
LOG=$O/run_$SHORT.log
for i in 1 2; do
  echo "== $(date -u +%FT%TZ) start $O/${SHORT}_AXJ_run$i" >> "$LOG"
  $PY "$HERE/classify.py" --table "$T" --model "$MODEL" --kind AXJ --units "$U" --out "$O/${SHORT}_AXJ_run$i" --workers "$W" \
    ${ENDPOINT:+--endpoint "$ENDPOINT"} >> "$LOG" 2>&1
  echo "== $(date -u +%FT%TZ) end $O/${SHORT}_AXJ_run$i rc=$?" >> "$LOG"
done
echo "== $(date -u +%FT%TZ) $SHORT axj finished" >> "$LOG"
