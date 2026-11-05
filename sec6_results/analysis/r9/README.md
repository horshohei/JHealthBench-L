# R9 変更の種類のラベル付け（2026-10-16 開始）

手順は `../r9_coding_plan_v2.md`（案A＋案B＋案C）。

| ファイル | 内容 |
| --- | --- |
| `codebook.md` | コード（E 13・J 1・G 5・S 3・NONE）と大区分、例 |
| `build_units.py` → `units.jsonl`・`units_summary.json` | 単位の抽出（本文の変更771・削除18・追加10・会話53ターン、配点変更の印14）と既存の記録（案A） |
| `classify.py` | 1単位ずつ分類する client（標準ライブラリのみ、再開可）。送る本文は条件表 v5 の各モデルの `send`＋messages だけ |
| `pilot_ids.txt` | パイロットの22単位（既知の事例6＋層別に無作為、seed 20261016） |
| `runs/pilot_*`、`runs/{gptoss,deepseek}_run{1,2}/` | 生の応答（`raw.jsonl`）、ラベル（`labels.jsonl`）、失敗（`failures.jsonl`）、設定（`config.json`） |
| `aggregate.py` → `labels_merged.jsonl`・`agreement.json`・`summary.md` | 2モデルの一致度、2回の安定性、医師のケース単位コードとの整合、合意ラベル、暫定の件数 |
| `make_author_sheet.py` → `author_check_150.xlsx`・`.csv`・`_design.json` | 著者の確認用の150単位（大区分×変更の出所の層別無作為抽出、seed 20261016） |

分類役：gpt-oss-120b（LAB_GPU_SERVER、条件表 v5 の `gptoss120b`：temperature 1.0・top_p 1.0・reasoning_effort medium）と DeepSeek V4 Flash（研究室の API、`deepseek_v4_flash_think`：temperature 1.0・top_p 1.0・思考あり）。主採点役（Qwen3.8-27B）とは別系列。各モデル2回。形式不正の再試行は1単位5回まで。

パイロット後の変更：プロンプトに「reason は必ず日本語」を追加（DeepSeek の1件が中国語で理由を書いたため）。ほかは変更なし。

## 中断（2026-10-16 18:4x、研究室の電気設備メンテナンスのため）

| run | 完了（labels） | 全体 | 失敗 |
| --- | ---: | ---: | ---: |
| gptoss_run1 | 852 | 852 | 0 |
| gptoss_run2 | 696 | 852 | 0 |
| deepseek_run1 | 58 | 852 | 0 |
| deepseek_run2 | 0 | 852 | — |

LAB_GPU_SERVER の client・vLLM サーバー（tmux `hb-r9-*`）はすべて止め、GPU は空けた。結果は LAB_GPU_SERVER と手元（`r9/runs/`）の両方にある。`classify.py` は `labels.jsonl` にある単位を飛ばすので、同じコマンドで続きから再開できる。

再開（LAB_GPU_SERVER、`<REMOTE_ROOT>` で）：
```bash
U="<REMOTE_HOME>/.local/bin/uv run --no-project --offline --python /usr/bin/python3 python"
# gpt-oss のサーバー（GPU 1枚）を起動してから
$U r9/classify.py --table conditions/condition_table_v5.json --model gptoss120b --units r9/units.jsonl --out r9/runs/gptoss_run2 --workers 32
$U r9/classify.py --table conditions/condition_table_v5.json --model deepseek_v4_flash_think --units r9/units.jsonl --out r9/runs/deepseek_run1 --workers 12
$U r9/classify.py --table conditions/condition_table_v5.json --model deepseek_v4_flash_think --units r9/units.jsonl --out r9/runs/deepseek_run2 --workers 12
```

注意：メタレビューの取り込みにラベルの誤りが見つかり、L の本文が変わる可能性がある。L が変わったら `build_units.py` で単位を作り直し、本文が変わった単位だけ（または全件）を分類し直す。既存の run は単位の本文を記録していないので、作り直した `units.jsonl` と突き合わせて、L の本文が変わった単位の結果は使わない。

## 再開と完了（2026-10-19〜20、訂正後の L＝data_v3）

- 単位を `data_v3`・メタレビュー v3 で作り直した：`r9/v3/units.jsonl`（853単位。本文の変更772・削除18・追加10・会話53）。旧 `units.jsonl` は残した（`build_units.py` の既定で同一に再現できることを確認）。差分は `r9/v3/units_diff_vs_v2.json`：本文が変わった11単位＋新しく T と異なる1単位（12_08 criteria_001）＝12単位を分類し直した。
- 旧 run のラベルは、上の12単位を除いて `r9/v3/runs/` に引き継いだ（`carried_over_from_v2.json`）。残りを同じ設定（条件表 v5 の send、同じプロンプト）で回し、4 run とも 853／853、失敗0。
- `aggregate.py --dir r9/v3` → `r9/v3/agreement.json`・`summary.md`・`labels_merged.jsonl`。
- 150単位：`make_author_sheet_v2.py`（評価を動かした単位を優先、seed 20261019）→ `r9/author_check_150.xlsx`・`.csv`・`_design.json`。旧規則の `make_author_sheet.py` は実行していない。
