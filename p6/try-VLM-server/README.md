# try-VLM-server

VLM を遠隔 3090PC で常駐させ、本機から word-wolf のシーンを送って「今の音源は誰か」を
文字で受け取る。**提案書 §2.4「音図を VLM にどう入力するか」の 2 方式を実測で比較する**
ための骨格。

- **mode_A = (A) テキスト化**：プレーン RGB + 音源のテキスト説明。どのテキスト形式が
  効くかを比較(座標 / 方位角 / 自然言語 / 3x3格子 / 上位Kピーク)。
- **mode_B = (B) 画像重畳**：音図を黄色で重畳した画像。重畳の α で読みが変わるかを比較。

判定はどちらも厳密 4 クラス(Left / Right / Teleoperator / Others)。§2.4 の既記載
「大規模 VLM なら方向 ~91.7%」の続き(mode_B/results の 160 枚参照結果)に、手挑 9 枚での
A/B・形式・α 比較を足す。

## 構造

```
try-VLM-server/
├── selection.csv     手挑 9 枚の tick(bag+tick_ts+gt+vad)。両 mode の生成元
├── common/           共有: tunnel(SSH隧道) / protocol(分帧) / prompt(共通足場+採点前段) / grading(4クラス採点)
├── server/           遠隔 3090 に常駐。vlm_server.py はモード非依存(prompt+画像→文字)。両 mode で共用
├── test/             網络延迟テスト(既完了): latency_test.py
├── mode_A/           テキスト化: textifier.py / client.py / eval.py / sample/ / results/
└── mode_B/           画像重畳:   overlay_info.py / client.py / eval.py / sample/ / results/
```

**server は 1 つで両モードを兼ねる**(prompt + JPEG → 文字。テキスト化 / 重畳の違いは
すべて local 側で吸収)。共通足場を A/B で共有し、違うのは「音源情報の一行」だけなので、
テキスト vs 画像重畳というモダリティ差だけを切り出して比較できる。

## 網络方案(mode 非依存)

3090PC は Riken 内網。網関 `Riken` を二段跳び ProxyJump、二段の密码が違う(網関 grp /
目標 chen)ので**入れ子 sshpass**で自動化。server は `127.0.0.1:50007` のみ監听し、local が
SSH `-L` で転送。密码は `RIKEN_GRP_PASS` / `PC3090_CHEN_PASS` で上書き可。詳細は
`common/tunnel.py`。

## 端到端の流れ(wolf venv)

```bash
# 0) 一度だけ: サンプル生成(bag へのアクセスが要る)
/home/chen/.virtualenvs/wolf/bin/python mode_A/sample/gen_samples.py
/home/chen/.virtualenvs/wolf/bin/python mode_B/sample/gen_samples.py

# 1) 遠隔 3090 で server 起動(echo で配線確認 / qwen で本番)
python server/vlm_server.py --backend echo
# python server/vlm_server.py --backend qwen --max-pixels 602112   # 32B-AWQ, ~20GB, 1-2分

# 2) 本機で評測(自動で隧道を張る)
/home/chen/.virtualenvs/wolf/bin/python mode_A/eval.py    # 9 枚 x 5 形式
/home/chen/.virtualenvs/wolf/bin/python mode_B/eval.py    # 9 枚 x 全 α
```

## 延迟テスト結論 (test/latency_test.py, 実 word-wolf 音図)

| モード | 上行 | 圧縮 | 解圧 | 網络 RTT(mean) |
|------|------|------|------|------|
| 原図 PNG | 474KB | 0 | 9ms | 166ms |
| **JPEG q70** | 38KB | ~1ms | ~1ms | **67ms** |
| JPEG q50 | 29KB | ~1ms | ~1ms | 49ms |
| 64×64 | 6KB | ~1ms | ~0ms | 29ms |

**JPEG q70 が既定**(保真と延迟の最適、単帧往復 ~67ms)。瓶頸は VLM 推論本体のみ。
