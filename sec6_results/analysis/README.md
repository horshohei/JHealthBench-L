# 集計スクリプト（analysis_plan.md の推定量）

2026-10-16 作成。分析計画 §8 に従い、**実データの採点結果を見る前に、ダミーデータだけで作って確かめた**。実データへの適用は、ユーザーの確認後に行う。

## ファイル

| ファイル | 内容 |
| --- | --- |
| `hb_analysis.py` | 集計の本体（標準ライブラリだけ。得点の定義は `pipeline/hb/scoring.py` と同じ） |
| `run_analysis.py` | CLI。`analysis.json`（全結果）と `analysis.md`（要約の表）を書く |
| `make_dummy.py` | ダミーの採点 run を作る。実 run の `manifest.json`・`tasks.jsonl` だけを読み、`results.jsonl` は読まない |
| `labels_dummy.jsonl`・`conv_labels_dummy.jsonl` | 変更の種類（R9）の**ダミー**のラベル。R9 の本番ラベルができたら同じ形式で差し替える |
| `dummy_runs/` | ダミー run（`main/`・`secondary/`＝0/1 の判定、`expected/`＝判定の確率、`truth.json`＝埋め込んだ設定） |
| `out_dummy/`・`out_dummy_expected/` | ダミーでの出力（前者が標本、後者が期待値＝「正解」） |
| `tests/test_analysis.py` | unittest（17件） |

## 使い方

```bash
cd <EXPERIMENT_ROOT>
# ダミー（作り直すときは dummy_runs を消してから）
uv run --no-project python analysis/make_dummy.py
uv run --no-project python analysis/run_analysis.py --out analysis/out_dummy \
  --main analysis/dummy_runs/main/* --secondary analysis/dummy_runs/secondary/* \
  --labels analysis/labels_dummy.jsonl --conv-labels analysis/conv_labels_dummy.jsonl
uv run --no-project python -m unittest discover -s analysis/tests

# 実データ（ユーザー確認の後。R9 のラベルができるまでは --labels・--conv-labels を付けない）
uv run --no-project python analysis/run_analysis.py --out analysis/out_main \
  --main runs/grades_v6/qwen38_grader_none__* \
  --secondary runs/grades_v6_secondary/gemini38flash_grader_low__*
```

`--main` には主採点役の run（回答モデルごと）を全部渡す。所要時間はダミー（15モデル、bootstrap 2,000回）で約20秒。

## 定義

- **得点**：ケース得点＝満たした項目の配点の和（負の項目も含む）÷正の配点の和。ケースごとにはクリップしない。モデルの得点＝全ケース・全サンプルのケース得点の平均を、最後に1回だけ [0, 1] にクリップ（simple-evals と同じ、`scoring.clipped_mean`）。出力の `clipping_changed` が真なら、クリップで値が変わった。
- **欠損**：判定が1項目でも欠けたケース・サンプルは得点を出さず（「満たさない」扱いにしない）、`missing` に件数を載せる。
- **bootstrap**：ケース単位で 2,000 回（`--boot`）、seed 20261013。ケース内の3サンプルと全項目を一緒に動かす。T と L は同じ引き直しで対にする。区間は 2.5〜97.5 パーセンタイル。引き直しは集合ごとに名前付きの乱数で作るので、同じ入力なら同じ結果になる。
- **1. 全体（RQ1）**：S_T、S_L、Δ_loc＝S_L−S_T（クリップ後の差）、モデル平均。順位、Kendall τ-b（区間つき）、モデルの組ごとの「T と L で順序が入れ替わる確率」（bootstrap の割合）。
- **2. 会話の変更の有無**：会話が同一のケースと変更されたケースで、それぞれ Δ_loc。会話が同一かどうかは、run の中で T と L の回答が同じ回答かで判定し、`case_map.jsonl` と食い違えば出力に書く。
- **3. Δ_rubric と分解（RQ2）**：会話が同一のケースで、同じ回答を L と T の基準で採点した差。因子は次の5種類。
  - `noise`：本文も配点も同一の項目（同じ依頼の2回の採点）
  - `text:<群>`：本文が変わった項目の判定の入れ替え（`--labels` があれば群別、なければ `text:all`）
  - `points`：配点変更
  - `deleted`：T だけにある項目
  - `added`：L だけにある項目

  因子をすべて外した状態が T の得点、すべて入れた状態が L の得点になる混合の基準で計算する。
  - Shapley 値（全ての並べ方の平均、厳密計算）を主に出す。固定順（noise → text → points → deleted → added）も併記する。
  - Shapley の和は Δ_rubric に一致する（`shapley_sum_check`）。
  - 分解はクリップ前の平均で行う。
- **4. 項目の判定の変わりやすさ**：会話が同一のケースで、同じ回答の T と L の判定が異なる割合と、平均(L−T)を項目の区分ごとに出す。
  - `identical`（同一の項目）は、採点のゆらぎの基準線になる。
  - 区分は `text_changed`・`points_changed`、ラベルの群（`group:`）、ラベルのコード（`code:`、1項目が複数のコードに数えられる）。
- **5. 片方の版にだけある項目**：T だけにある18項目と L だけにある10項目について、本文・配点と、モデル別・全体の満たした割合。
- **6. 層別（探索的）**：次の群ごとに S_T・S_L・Δ_loc と区間を出す（ケースが1つの群は区間なし）。
  - workbook_group・specialty・scenario・theme・source_dataset・original_language
  - 会話の変更の有無、会話の変更のコード（`--conv-labels`）
  - axis（`axis:*` タグの得点）
- **7. ばらつき**：3サンプルのケース内 SD の平均と、worst-at-3（ケースごとの最小値の平均をクリップ）。
- **8. 主採点役と副採点役**：副採点役が採点したサンプル（サンプル1）で、Δ_loc と Δ_rubric を両方の採点役で出す。
  - 差（副−主）の区間は、対にした bootstrap で出す。
  - 項目単位の一致率とκも出す。
  - 自己採点（回答モデルと採点役が同じ開発元）には印を付ける。
- **9. 欠損**：得点を出さなかったケース・サンプルの一覧。

## ラベルファイルの形式（R9 の差し替え用）

- 項目（本文が変わった項目だけが使われる。ほかは無視して件数を記録する）：`{"case_id" または "case_label", "criterion_id", "codes": ["E", "J", ...]}`
  - Shapley の因子は、コードの組み合わせ（例 `E+J`）ごとにまとめる。
  - コード単位の集計（重複あり）は、4. の `code:` に出る。
- 会話（会話が変更されたケース）：`{"case_id" または "case_label", "codes": [...]}`

## ダミーで確かめたこと（`tests/test_analysis.py`）

- `case_score` が simple-evals の `calculate_score` と一致すること。平均だけをクリップすること。
- Shapley の高速計算（`shapley_linear`）が全列挙と一致し、和が全体の差に一致すること。手計算の例（削除だけ・判定の入れ替えだけ）で正しい因子に全てが割り当たること。
- 欠損したケース・サンプルを得点から外して数えること。同じ判定が2回ある run や、データのハッシュが違う run を拒むこと。
- 埋め込んだ差が回復できること：
  - Δ_loc は、期待値（`expected/` での値）が15モデル中14〜15で95%区間に入る。
  - 会話の変更の効果（llmjp41_8b +、sol61 −）が出る。
  - 変更の種類の効果（E は正、G は負、J はほぼ0）が出る。
  - 同一項目の不一致率が、理論値（約0.035）と一致する。
- 副採点役の比較で、自己採点の印が付くこと。
