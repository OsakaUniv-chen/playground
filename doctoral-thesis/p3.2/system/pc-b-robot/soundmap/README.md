# 1-bit 音響マップ生成器

`bridge/soundmap_bridge.py` がここから `OneBitSoundMapAPI` を読む。

| ファイル | 内容 |
|---|---|
| `onebit_soundmap.py` | 生成器本体。依存は numpy / scipy / cv2 だけ |
| `1bit-soundmap-note.md` | アルゴリズムと主要パラメータの覚え書き |

## なぜここに置いてあるか

**意図的な複製。** 元は `soundmap-generator/generator-1bit/`（研究用の別ディレクトリ）
にあり、以前は `SOUNDMAP_GENERATOR_DIR` で外から参照していた。この系は現地で
別の機械（PC-B）に配って動かすものなので、**手元の作業ディレクトリの構成に
依存していると、コピーした先で動かない。** PC-B のフォルダだけ持って行けば
そのまま動くように、ここに取り込んである。

複製元の更新（2026-07-17 時点の版を取り込み）を追う場合は
`soundmap-generator/generator-1bit/onebit_soundmap.py` と突き合わせる。

## パラメータ

既定の `fs=44100` / `channels=16` は UMA16v2 と一致するので、そのまま使える。
生成周期と積分窓は `common/config.env` の `SOUNDMAP_HZ` / `SOUNDMAP_WINDOW_MS`
で指定する（窓は周期より長く取り、毎回ずらして使う）。

CPU のみで動く。N100 の実測で 25.5 ms/map・1 コア・最大 27 Hz。
