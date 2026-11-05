# 費用と計算量（付録 S29 用、2026-11-05 集計）

実行記録（runs/*/manifest.json・attempts.jsonl・results.jsonl、r9・分析軸の raw.jsonl）だけから数えた。集計スクリプト：analysis/scripts_20261105/compute_costs.py、summarize_costs.py。

## 1. 回答と判定の数（分析に使ったもの）

| モデル集合 | モデル数 | 回答 T・L（会話の重複を除く） | 回答 O | 主採点役 T | 主採点役 L | 主採点役 O |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 本文の17モデル | 17 | 5,814 | 4,029 | 52,530 | 52,122 | 49,440 |
| 外した9モデル | 9 | 3,078 | 2,133 | 27,810 | 27,594 | 27,810 |
| 計 | 26 | 8,892 | 6,162 | 80,340 | 79,716 | 77,250 |

- 1モデルあたり：回答 T・L 342（114会話×3）、O 237（79×3）。判定 T 3,090、L 3,066（計 6,156）、O 3,090。
- Qwen3-32B は O を採点していない（回答は O も生成した）。O の回答はほかの25モデルとも 237 ずつ。
- 副採点役（Gemini 3.8 Flash low、Batch）：T 15,450、L 15,330（計 30,780）＝最初の15モデルの回答サンプル1（15×2,052）。

## 2. ラボの計算時間

回答の生成（LAB_GPU_SERVER、サーバーが要求を処理していた時間。サーバーの起動 1〜3 分は含まない）：

| モデル | 本文 | 回答 | GPU 時間（h） |
| --- | --- | ---: | ---: |
| weblab_medllm_gptoss120b_gguf | ○ | 579 | 1.83 |
| swallow_32b_rl | ○ | 579 | 0.90 |
| swallow_8b_rl | ○ | 579 | 0.47 |
| swallow_32b_sft | ○ | 579 | 0.46 |
| sip_jmed_llm3_13b | 付録 | 579 | 0.39 |
| llmjp4_8b_thinking | ○ | 579 | 0.32 |
| qwen38_27b | ○ | 579 | 0.28 |
| gptoss120b | ○ | 579 | 0.28 |
| qwen3_32b | ○ | 579 | 0.26 |
| swallow_8b_sft | ○ | 579 | 0.26 |
| llmjp41_33b | 付録 | 579 | 0.24 |
| qwen36_35b_a3b | 付録 | 579 | 0.24 |
| llmjp41_33b_low | 付録 | 579 | 0.16 |
| gemma4_31b | ○ | 579 | 0.16 |
| llmjp41_32b_a3b | 付録 | 579 | 0.15 |
| qwen3_8b | ○ | 579 | 0.15 |
| llmjp4_8b_instruct | ○ | 579 | 0.15 |
| llmjp41_32b_a3b_low | 付録 | 579 | 0.12 |
| llmjp41_8b | 付録 | 579 | 0.06 |
| 計 | | | 6.9（本文 5.5） |

研究室の共用 API（GPU 時間の計に入れない）：deepseek_v4_flash_think 5.2 h（579 回答）、qwen38_flash_next 5.9 h（579 回答）

主採点役（Qwen3.8-27B 思考なし、GPU 1 枚のサーバー）：

- 実行したすべての採点 run の処理時間：42.9 GPU 時間（呼び出し 280,106 回）。内訳：分析に使った run 24.3 h、統合前の run・直した項目の採点し直し・置き換えた run 17.4 h、Gold とメタ評価 1.2 h。
- grades_v7_merged（最初の15モデルの T・L）は統合した写しで呼び出しの記録を持たないので、その計算は grades_v6・grades_v6_pass2・grades_v7_fix の側で数えている。
- 1時間あたりの判定数（1つの run の処理時間で割った値、56 run）：中央値 7,300、範囲 3,459〜10,195。回答の長いモデル（gpt-oss-120b、Weblab-MedLLM）は約4,000、短い商用モデルの O は約8,000〜10,000。
- 「1時間に約7,800判定」は experiment_redo/experiment_plan_20261019.md（10/19）の「実測の速さ」の記述。どの run から出したかは書かれていない。上の run ごとの値（中央値）とおおむね合う。

## 3. 商用 API の費用

条件表の各モデルの単価（price_usd_per_mtok、出典 URL は条件表）で、呼び出しごとに記録したトークン数から計算。Batch は 50%（条件表 D15）。Anthropic の入力はキャッシュ以外の分、Gemini の思考は出力の単価、OpenAI の出力は思考を含む。

### 回答の生成（T・L と O、分析に使った run）

| 回答モデル | Batch | 呼び出し | 入力トークン | うちキャッシュ | 出力トークン | 思考トークン | 費用（USD） |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| sonnet55_medium | ○ | 579 | 164,295 | 0 | 631,061 | 82,421 | 3.32 |
| opus55 | ○ | 579 | 164,295 | 0 | 880,007 | 247,213 | 9.13 |
| sol61 | ○ | 799 | 126,261 | 9,838 | 352,544 | 56,927 | 1.88 |
| luna6 | ○ | 579 | 126,261 | 9,838 | 484,473 | 262,126 | 0.13 |
| gemini38flash | ○ | 579 | 96,402 | 0 | 464,325 | 515,814 | 1.87 |
| 計 | | | | | | | 16.33 |

### 副採点役

| 副採点役 | Batch | 呼び出し | 入力トークン | うちキャッシュ | 出力トークン | 思考トークン | 費用（USD） |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| gemini38flash_grader_low | ○ | 30,780 | 56,070,523 | 1,436 | 2,667,710 | 1,445,153 | 28.74 |
| 計 | | | | | | | 28.74 |

### 採点役の候補の比較（医師 Gold 997項目＋予備の57項目）

| 採点役の候補（Gold） | Batch | 呼び出し | 入力トークン | うちキャッシュ | 出力トークン | 思考トークン | 費用（USD） |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| gemini38flash_grader_default | ○ | 1,054 | 1,557,096 | 0 | 83,947 | 365,002 | 1.51 |
| gemini38flash_grader_low | ○ | 1,055 | 1,557,096 | 0 | 90,311 | 70,622 | 0.93 |
| opus55_grader_ref | ○ | 1,054 | 194,301 | 1,419,046 | 334,498 | 112,956 | 6.38 |
| sol61_grader_ref | ○ | 1,054 | 2,015,571 | 1,700,145 | 154,638 | 22,595 | 1.24 |
| sonnet55_grader_low | ○ | 1,054 | 194,301 | 1,720,002 | 244,370 | 0 | 2.44 |
| sonnet55_grader_nothink | ○ | 1,054 | 194,301 | 1,833,370 | 278,792 | 0 | 2.48 |
| 計 | | | | | | | 14.98 |

商用 API の合計：60.04 USD（回答 16.33、副採点役 28.74、候補の比較 14.98）。
- 数えていないもの：呼び出しの記録（attempts）を持たない初期の試行 run（answers_other・grader_screening の一部、合わせて数十件）。費用は小さい。
- ラボのモデル（gpt-oss-120b、Qwen3.8 の低い思考など）の候補比較は GPU 上なので費用 0（時間は Gold の行に含まない。下の JSON の grader_screening）。

## 4. 分類の呼び出し数（失敗した呼び出しの再試行を含む）

| 作業 | モデル・回 | 呼び出し | ラベル |
| --- | --- | ---: | ---: |
| 変更の理由 R9 v3（本文で使う） | deepseek_run1（deepseek_v4_flash_think） | 864 | 853 |
| 変更の理由 R9 v3（本文で使う） | deepseek_run2（deepseek_v4_flash_think） | 865 | 853 |
| 変更の理由 R9 v3（本文で使う） | gptoss_run1（gptoss120b） | 855 | 853 |
| 変更の理由 R9 v3（本文で使う） | gptoss_run2（gptoss120b） | 856 | 853 |
| 変更の理由 R9 v3（本文で使う）：計 | | 3,440 | 3,412 |
| 変更の理由 R9 v1・v2 とパイロット（探索） | deepseek_run1（deepseek_v4_flash_think） | 58 | 58 |
| 変更の理由 R9 v1・v2 とパイロット（探索） | gptoss_run1（gptoss120b） | 854 | 852 |
| 変更の理由 R9 v1・v2 とパイロット（探索） | gptoss_run2（gptoss120b） | 698 | 696 |
| 変更の理由 R9 v1・v2 とパイロット（探索） | pilot_deepseek（deepseek_v4_flash_think） | 22 | 22 |
| 変更の理由 R9 v1・v2 とパイロット（探索） | pilot_gptoss（gptoss120b） | 22 | 22 |
| 変更の理由 R9 v1・v2 とパイロット（探索）：計 | | 1,654 | 1,650 |
| 軸の付け直し（日本語の定義、本文・付録） | deepseek_AXJ_run1（deepseek_v4_flash_think） | 1,875 | 1,844 |
| 軸の付け直し（日本語の定義、本文・付録） | deepseek_AXJ_run2（deepseek_v4_flash_think） | 1,871 | 1,844 |
| 軸の付け直し（日本語の定義、本文・付録） | qwen_AXJ_run1（qwen38_flash_next） | 1,847 | 1,844 |
| 軸の付け直し（日本語の定義、本文・付録） | qwen_AXJ_run2（qwen38_flash_next） | 1,848 | 1,844 |
| 軸の付け直し（日本語の定義、本文・付録）：計 | | 7,441 | 7,376 |
| 分析軸 v1 の3体系（探索。Qwen は途中で止めた） | deepseek_A_run1（deepseek_v4_flash_think） | 1,857 | 1,844 |
| 分析軸 v1 の3体系（探索。Qwen は途中で止めた） | deepseek_A_run2（deepseek_v4_flash_think） | 1,852 | 1,844 |
| 分析軸 v1 の3体系（探索。Qwen は途中で止めた） | qwen_A_run1（qwen38_flash_next） | 1,414 | 1,412 |
| 分析軸 v1 の3体系（探索。Qwen は途中で止めた）：計 | | 5,123 | 5,100 |
| 分析軸 v1 のパイロット | deepseek_A1_solo（deepseek_v4_flash_think） | 20 | 20 |
| 分析軸 v1 のパイロット | deepseek_A2_solo（deepseek_v4_flash_think） | 20 | 20 |
| 分析軸 v1 のパイロット | deepseek_A3_solo（deepseek_v4_flash_think） | 20 | 20 |
| 分析軸 v1 のパイロット | deepseek_A_run1（deepseek_v4_flash_think） | 20 | 20 |
| 分析軸 v1 のパイロット | deepseek_A_run2（deepseek_v4_flash_think） | 20 | 20 |
| 分析軸 v1 のパイロット | deepseek_Actx（deepseek_v4_flash_think） | 9 | 9 |
| 分析軸 v1 のパイロット | deepseek_B1（deepseek_v4_flash_think） | 5 | 5 |
| 分析軸 v1 のパイロット | deepseek_B2（deepseek_v4_flash_think） | 5 | 5 |
| 分析軸 v1 のパイロット | qwen_A1_solo（qwen38_flash_next） | 20 | 20 |
| 分析軸 v1 のパイロット | qwen_A2_solo（qwen38_flash_next） | 20 | 20 |
| 分析軸 v1 のパイロット | qwen_A3_solo（qwen38_flash_next） | 20 | 20 |
| 分析軸 v1 のパイロット | qwen_A_run1（qwen38_flash_next） | 20 | 20 |
| 分析軸 v1 のパイロット | qwen_A_run2（qwen38_flash_next） | 20 | 20 |
| 分析軸 v1 のパイロット | qwen_Actx（qwen38_flash_next） | 9 | 9 |
| 分析軸 v1 のパイロット | qwen_B1（qwen38_flash_next） | 5 | 5 |
| 分析軸 v1 のパイロット | qwen_B2（qwen38_flash_next） | 5 | 5 |
| 分析軸 v1 のパイロット：計 | | 238 | 238 |
| 事例の軸（シナリオ・テーマ） | deepseek_B1_run1（deepseek_v4_flash_think） | 80 | 79 |
| 事例の軸（シナリオ・テーマ） | deepseek_B1_run2（deepseek_v4_flash_think） | 83 | 79 |
| 事例の軸（シナリオ・テーマ） | deepseek_B2_run1（deepseek_v4_flash_think） | 79 | 79 |
| 事例の軸（シナリオ・テーマ） | deepseek_B2_run2（deepseek_v4_flash_think） | 80 | 79 |
| 事例の軸（シナリオ・テーマ） | qwen_B1_run1（qwen38_flash_next） | 79 | 79 |
| 事例の軸（シナリオ・テーマ） | qwen_B1_run2（qwen38_flash_next） | 79 | 79 |
| 事例の軸（シナリオ・テーマ） | qwen_B2_run1（qwen38_flash_next） | 81 | 79 |
| 事例の軸（シナリオ・テーマ） | qwen_B2_run2（qwen38_flash_next） | 80 | 79 |
| 事例の軸（シナリオ・テーマ）：計 | | 641 | 632 |

- R9 は gpt-oss-120b（LAB_GPU_SERVER）と DeepSeek V4 Flash（研究室の API）で、記録に処理時間がないので時間は出せない。
- 分析軸は研究室の API（Qwen3.8-Flash-Next、DeepSeek）。処理していた時間は analysis/costs_20261105/costs.json の classifiers に run ごとにある（軸の付け直し：Qwen 計 17.7 h、DeepSeek 計 3.0 h）。
