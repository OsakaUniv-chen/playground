# mode_B — 画像重畳(提案書 2.4 の (B))

**音図を黄色で重畳した画像**を VLM に渡し、4 クラス(Left / Right / Teleoperator /
Others)のどれが今の音源かを当てさせる。主眼は **重畳の α(濃さ)で VLM の読みが
変わるか**。可変は `--sample` と `--alpha` のみ。

重畳は実験映像と同じ黄色(明るいほど大)。`sample/gen_samples.py` が同じ 9 枚を
α 格子 {0.3, 0.45, 0.6, 0.75, 0.9} で描き分ける(α=0.6 が現行基準、相機 β=0.8 固定)。

プロンプトは `../common/prompt.py` の共通足場 + `overlay_info.py`(黄色オーバーレイの
読み方)。**mode_A と同じ足場・違うのは音源情報だけ** = テキスト vs 画像重畳を公平比較。

> 注: 旧 `eval_labeled.py` は prompt を jet(赤=最大)と書いていたが実画像は黄色で
> 不整合だった。mode_B は黄色として正しく説明する。

## 使い方(wolf venv)

```bash
# 0) サンプル再生成(α 別オーバーレイ -> sample/manifest.csv)。bag アクセスが要る。
/home/chen/.virtualenvs/wolf/bin/python sample/gen_samples.py

# 1) 3090 で server 起動(共有 server)
#    python ../server/vlm_server.py --backend qwen --max-pixels 602112

# 2) 単発確認
/home/chen/.virtualenvs/wolf/bin/python client.py --sample sample_06 --alpha 0.6 --show-prompt

# 3) 一括評測(9 枚 x 全 α -> results/)
/home/chen/.virtualenvs/wolf/bin/python eval.py
```

## 判定 / 参照

厳密 4 クラス + クラス別 precision/recall + 混同行列(`../common/grading.py`)。
`results/eval_32b_awq_2026-07-21.txt` は旧 160 枚(jet 重畳)での参照結果
(4 クラス 68.8% / 方向 3 クラス ~91.7%、Others 予測 0 件)。
