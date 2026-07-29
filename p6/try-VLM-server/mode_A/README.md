# mode_A — テキスト化(提案書 2.4 の (A))

**プレーン RGB 画像 + 音源のテキスト説明**を VLM に渡し、4 クラス
(Left / Right / Teleoperator / Others)のどれが今の音源かを当てさせる。
音源のテキストは、**gt_label を定義したのと同一の前段**(`label_current_sm` の
領域絶対強度 + 代表点、labeling.py)から機械生成する(`textifier.py`)。**gt_label という
"答えの単語"は使わない**——textifier は「どの方向が最強か」と代表点/強度だけ用い、
座標→人のグラウンディング(右座席の人? 下中央=遠隔者? 誰でもない?)を VLM に委ねる
(＝ honest なテスト)。**絶対強度**(0=静か〜1=最大)を必ず併記するので、静か/曖昧な
tick は Others と判断でき、別途「静音フラグ」を足さずに済む。

> 旧・大域ピーク方式は静かな tick(例 sample_06 は最大 0.16)で座標が不安定だった。
> 同一前段に変えて忠実・頑健にした(全 9 枚で argmax = gt を確認)。

主眼は **どのテキスト形式が最も効くか**。可変は `--sample` と `--format` のみ。

## 5 つの形式(`textifier.FORMATS`)

いずれも先頭に「最強の音は絶対強度 S(0=静か〜1=最大)」を付け、位置の与え方だけ変える:

| format | 位置の与え方 |
|---|---|
| `coord`   | 最強検出の画素座標 (x, y) |
| `azimuth` | 中心からの方位角 …° / …時方向 |
| `nl`      | 「右側」等の自然言語方位 |
| `grid`    | 3x3 格子の「中央下」セル |
| `profile` | 全方向の検出を 座標 + 絶対強度で列挙(領域名は伏せ、VLM に最強選択させる) |

共通足場(場面説明・遠隔者=下中央の役割・回答フォーマット)は `../common/prompt.py`。
mode_B と足場を共有し、違うのは音源情報の一行だけ = テキスト vs 画像重畳を公平に比較。

## 使い方(wolf venv)

```bash
# 0) サンプル再生成(プレーン RGB + ピーク -> sample/manifest.csv)。bag へのアクセスが要る。
/home/chen/.virtualenvs/wolf/bin/python sample/gen_samples.py

# 1) 3090 で server 起動(共有 server。echo で配線確認 / qwen で本番)
#    python ../server/vlm_server.py --backend qwen --max-pixels 602112

# 2) 単発確認
/home/chen/.virtualenvs/wolf/bin/python client.py --sample sample_06 --format coord --show-prompt

# 3) 一括評測(9 枚 x 5 形式 -> results/)
/home/chen/.virtualenvs/wolf/bin/python eval.py
```

## 判定

厳密 4 クラス。全体正解率 + クラス別 precision/recall + 混同行列(`../common/grading.py`)。
手挑 9 枚(Right×3 / Left×3 / Teleoperator×2 / Others×1)なのでクラス件数と併記して読む。
