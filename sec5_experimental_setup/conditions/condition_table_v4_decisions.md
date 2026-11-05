# 条件表 v4 の確定事項（2026-10-14）

v4 の JSON は、作成済みの run が sha256 で固定しているため書き換えない。v4 を作った後にユーザーが確定したことを、ここに記録する。

| 項目 | 確定内容 | v4 JSON での記載 |
| --- | --- | --- |
| Qwen3.8-27B の回答 | `reasoning_effort: "medium"`（ユーザー確定 2026-10-14） | `qwen38_27b`（user_decision のまま） |
| Claude Sonnet 5.5 の回答 | `output_config.effort: "medium"` を使う（ユーザー確定 2026-10-14）。既定の high（`sonnet55`）は使わない | `sonnet55_medium` の `decided_by: agent_proposal_needs_approval` は、ユーザー確定と読み替える |

これで回答側の思考の強さは、選べるモデルではすべて medium に揃う（D17）。
