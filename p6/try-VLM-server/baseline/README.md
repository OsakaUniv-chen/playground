# baseline — 音図なし(視覚のみ)

**純 RGB 画像 + プロンプトのみ**を VLM に渡し、4 クラス(Left / Right / Teleoperator /
Others)のどれが今の話者かを当てさせる。**音図情報は一切与えない**。

mode_A / mode_B の対照(ablation)。提案書のコア主張「**視覚のみでは対象選択を誤る →
音図で補う**」を検証する下限点。ここが低く、mode_A/mode_B で上がれば「音図の追加が効く」
ことの直接証拠になる。

- 入力画像は **mode_A のプレーン RGB を流用**(独自 `sample/` を持たない。
  `../mode_A/sample/` を参照)。
- プロンプトは `../common/prompt.py` の共通足場 + `BASELINE_INFO`(「音図なし、視覚だけで
  判断せよ」)。**3 条件で足場は同一、違いは音源情報ブロックだけ**。

## 使い方(wolf venv)

```bash
# 3090 で server 起動後(共有 server)
python client.py --sample sample_06 --show-prompt   # 単発
python eval.py                                       # 9 枚一括 -> results/
```

判定は厳密 4 クラス + クラス別 precision/recall + 混同行列(`../common/grading.py`)。
Teleoperator は非可視・Others は音源手がかりが無いため、baseline では原理的に不利
(それが「音図が要る」根拠になる)。
