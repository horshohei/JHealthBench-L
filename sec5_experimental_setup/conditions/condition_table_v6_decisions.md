# 条件表 v6 の確定事項（2026-10-16）

v6 の JSON は、作成済みの採点 run が sha256 で固定しているため書き換えない。v6 の後にユーザーが確定したことを記録する。

| 項目 | 確定内容 | 根拠 |
| --- | --- | --- |
| 主採点役（D1） | `qwen38_grader_none`（Qwen3.8-27B 思考なし、ローカル） | 医師 Gold 997項目で9設定中最良（balanced F1 0.851、κ 0.702、ケース得点 MAE 14.4pt）。3回採点で 0.852／0.848／0.845、形式不正0。`runs/screen_20261013_v3/screen_summary_all_20261016.json`、`runs/grades_v6/gold_qwen38_grader_none_x3/metrics.json` |
| 副採点役 | `gemini38flash_grader_low`（Gemini 3.8 Flash、thinking low、Batch） | 選定基準（最良との差の95%区間の下限 > −0.03）を満たす商用のうち最も安い（balanced F1 0.846、差 −0.005［−0.026, 0.016］） |
| 副採点役の範囲 | 回答のサンプル1だけ、T・L 両方の基準で採点（1モデル 2,052判定）。Qwen 系の回答（qwen38_27b、qwen36_35b_a3b、qwen38_flash_next）は自己採点の確認として必須。ほかのモデルは採点役による結論の変わりやすさの確認 | analysis_plan.md §5・§6 |

ユーザー確定：2026-10-16（「OK」）。
