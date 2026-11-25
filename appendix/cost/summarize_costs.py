"""Markdown summary of costs.json for the appendix (S29): counts, lab GPU hours, commercial USD, classifier calls."""
import json
import statistics as st
import sys
from pathlib import Path

D = Path(sys.argv[1])
c = json.loads((D / 'costs.json').read_text(encoding='utf-8'))
PAPER18 = ['sonnet55_medium', 'opus55', 'sol61', 'luna6', 'gemini38flash', 'gptoss120b', 'weblab_medllm_gptoss120b_gguf', 'qwen3_8b',
           'qwen3_32b', 'swallow_8b_sft', 'swallow_8b_rl', 'swallow_32b_sft', 'swallow_32b_rl', 'qwen38_27b', 'gemma4_31b',
           'llmjp4_8b_instruct', 'llmjp4_8b_thinking', 'llmjp41_8b']
COMMERCIAL = ['sonnet55_medium', 'opus55', 'sol61', 'luna6', 'gemini38flash']
act = c['activities']
ans = act['answers_used']
L = ['# 費用と計算量（付録 I / S29 用、18モデル版対応）', '',
     '実行記録（runs/*/manifest.json・attempts.jsonl・results.jsonl、r9・分析軸の raw.jsonl）だけから数えた。'
     '集計スクリプト：analysis/scripts_20261105/compute_costs.py、summarize_costs.py。', '']


def tl_unique(m):
    a = c['counts']['answers_by_model'].get(m, {})
    return a.get('answers', 0) - a.get('O', 0)


# 1. counts
grp = {'本文の18モデル': PAPER18, '外した8モデル': [m for m in c['counts']['answers_by_model'] if m not in PAPER18]}
L += ['## 1. 回答と判定の数（分析に使ったもの）', '',
      '| モデル集合 | モデル数 | 回答 T・L（会話の重複を除く） | 回答 O | 主採点役 T | 主採点役 L | 主採点役 O |', '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
tot = [0] * 6
for name, ms in grp.items():
    row = [len(ms), sum(tl_unique(m) for m in ms), sum(c['counts']['answers_by_model'].get(m, {}).get('O', 0) for m in ms),
           *[sum(c['counts']['main_judgments_by_model'].get(m, {}).get(k, 0) for m in ms) for k in ('T', 'L', 'O')]]
    tot = [t + r for t, r in zip(tot, row[1:])]
    L.append(f'| {name} | ' + ' | '.join(f'{x:,}' for x in row) + ' |')
L.append('| 計 | 26 | ' + ' | '.join(f'{x:,}' for x in tot) + ' |')
s = c['counts']['secondary_judgments']
L += ['', f"- 1モデルあたり：回答 T・L 342（114会話×3）、O 237（79×3）。判定 T 3,090、L 3,066（計 6,156）、O 3,090。",
      '- Qwen3-32B は O を採点していない（回答は O も生成した）。O の回答はほかの25モデルとも 237 ずつ。',
      f"- 副採点役（Gemini 3.8 Flash low、Batch）：T {s.get('T', 0):,}、L {s.get('L', 0):,}（計 {sum(s.values()):,}）＝最初の15モデルの回答サンプル1（15×2,052）。"]

# 2. lab GPU time
lab = [(m, b) for m, b in ans.items() if b['gpu_s'] > 0]
L += ['', '## 2. ラボの計算時間', '', '回答の生成（LAB_GPU_SERVER、サーバーが要求を処理していた時間。サーバーの起動 1〜3 分は含まない）：', '',
      '| モデル | 本文 | 回答 | GPU 時間（h） |', '| --- | --- | ---: | ---: |']
for m, b in sorted(lab, key=lambda x: -x[1]['gpu_s']):
    L.append(f"| {m} | {'○' if m in PAPER18 else '付録'} | {b['tasks']} | {b['gpu_s'] / 3600:.2f} |")
L.append(f"| 計 | | | {sum(b['gpu_s'] for _, b in lab) / 3600:.1f}（本文 {sum(b['gpu_s'] for m, b in lab if m in PAPER18) / 3600:.1f}） |")
api = [(m, b) for m, b in ans.items() if b['lab_api_s'] > 0]
L += ['', '研究室の共用 API（GPU 時間の計に入れない）：' + '、'.join(f"{m} {b['lab_api_s'] / 3600:.1f} h（{b['tasks']} 回答）" for m, b in api)]
used, other, gold = (act.get(k, {}).get('qwen38_grader_none', {}) for k in ('main_grader_used', 'main_grader_other', 'main_grader_gold_meta'))
rates = [r[2] for r in c['grader_rate'] if '/gold' not in r[0] and 'meta' not in r[0]]
total_h = (used.get('gpu_s', 0) + other.get('gpu_s', 0) + gold.get('gpu_s', 0)) / 3600
L += ['', '主採点役（Qwen3.8-27B 思考なし、GPU 1 枚のサーバー）：', '',
      f"- 実行したすべての採点 run の処理時間：{total_h:.1f} GPU 時間（呼び出し {used.get('attempts', 0) + other.get('attempts', 0) + gold.get('attempts', 0):,} 回）。"
      f"内訳：分析に使った run {used.get('gpu_s', 0) / 3600:.1f} h、統合前の run・直した項目の採点し直し・置き換えた run {other.get('gpu_s', 0) / 3600:.1f} h、"
      f"Gold とメタ評価 {gold.get('gpu_s', 0) / 3600:.1f} h。",
      '- grades_v7_merged（最初の15モデルの T・L）は統合した写しで呼び出しの記録を持たないので、その計算は grades_v6・grades_v6_pass2・grades_v7_fix の側で数えている。',
      f"- 1時間あたりの判定数（1つの run の処理時間で割った値、{len(rates)} run）：中央値 {st.median(rates):,.0f}、範囲 {min(rates):,}〜{max(rates):,}。"
      '回答の長いモデル（gpt-oss-120b、Weblab-MedLLM）は約4,000、短い商用モデルの O は約8,000〜10,000。',
      '- 「1時間に約7,800判定」は experiment_redo/experiment_plan_20261019.md（10/19）の「実測の速さ」の記述。どの run から出したかは書かれていない。'
      '上の run ごとの値（中央値）とおおむね合う。']

# 3. commercial
def ctab(rows, title):
    out = [f'| {title} | Batch | 呼び出し | 入力トークン | うちキャッシュ | 出力トークン | 思考トークン | 費用（USD） |', '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    tot = 0.0
    for m, b in rows:
        out.append(f"| {m} | {'○' if b.get('batch_runs') else ''} | {b['attempts']:,} | {b.get('tok_input', 0):,} | {b.get('tok_cached', 0):,} | "
                   f"{b.get('tok_output', 0):,} | {b.get('tok_reasoning', 0):,} | {b['usd_x1000'] / 1000:.2f} |")
        tot += b['usd_x1000'] / 1000
    out.append(f'| 計 | | | | | | | {tot:.2f} |')
    return out, tot


L += ['', '## 3. 商用 API の費用', '',
      '条件表の各モデルの単価（price_usd_per_mtok、出典 URL は条件表）で、呼び出しごとに記録したトークン数から計算。Batch は 50%（条件表 D15）。'
      'Anthropic の入力はキャッシュ以外の分、Gemini の思考は出力の単価、OpenAI の出力は思考を含む。', '', '### 回答の生成（T・L と O、分析に使った run）', '']
t1, s1 = ctab([(m, ans[m]) for m in COMMERCIAL if m in ans], '回答モデル')
L += t1
t2, s2 = ctab(list(act.get('secondary_used', {}).items()), '副採点役')
L += ['', '### 副採点役', ''] + t2
scr = [(m, b) for m, b in act.get('grader_screening', {}).items() if b['usd_x1000'] > 0]
t3, s3 = ctab(scr, '採点役の候補（Gold）')
L += ['', '### 採点役の候補の比較（医師 Gold 997項目＋予備の57項目）', ''] + t3
L += ['', f'商用 API の合計：{s1 + s2 + s3:.2f} USD（回答 {s1:.2f}、副採点役 {s2:.2f}、候補の比較 {s3:.2f}）。',
      '- 数えていないもの：呼び出しの記録（attempts）を持たない初期の試行 run（answers_other・grader_screening の一部、合わせて数十件）。費用は小さい。',
      '- ラボのモデル（gpt-oss-120b、Qwen3.8 の低い思考など）の候補比較は GPU 上なので費用 0（時間は Gold の行に含まない。下の JSON の grader_screening）。']

# 4. classifier calls
cl = c['classifiers']
summ = lambda k: (sum(v['calls'] for v in cl[k].values()), sum(v['labels'] for v in cl[k].values()))
L += ['', '## 4. 分類の呼び出し数（失敗した呼び出しの再試行を含む）', '',
      '| 作業 | モデル・回 | 呼び出し | ラベル |', '| --- | --- | ---: | ---: |']
for k, title in (('r9_v3', '変更の理由 R9 v3（本文で使う）'), ('r9_earlier', '変更の理由 R9 v1・v2 とパイロット（探索）'),
                 ('axes_v2_axj', '軸の付け直し（日本語の定義、本文・付録）'), ('axes_v1_full', '分析軸 v1 の3体系（探索。Qwen は途中で止めた）'),
                 ('axes_v1_pilot', '分析軸 v1 のパイロット'), ('cases_v1', '事例の軸（シナリオ・テーマ）')):
    for run, v in cl[k].items():
        L.append(f"| {title} | {Path(run).name}（{v['model_id']}） | {v['calls']:,} | {v['labels']:,} |")
    n, lab_ = summ(k)
    L.append(f'| {title}：計 | | {n:,} | {lab_:,} |')
L += ['', '- R9 は gpt-oss-120b（LAB_GPU_SERVER）と DeepSeek V4 Flash（研究室の API）で、記録に処理時間がないので時間は出せない。',
      '- 分析軸は研究室の API（Qwen3.8-Flash-Next、DeepSeek）。処理していた時間は analysis/costs_20261105/costs.json の classifiers に run ごとにある'
      '（軸の付け直し：Qwen 計 17.7 h、DeepSeek 計 3.0 h）。']
(D / 'costs.md').write_text('\n'.join(L) + '\n', encoding='utf-8')
print('\n'.join(L))
