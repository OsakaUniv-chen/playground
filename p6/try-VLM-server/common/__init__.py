"""try-VLM-server 共有ライブラリ(mode_A / mode_B 双方が使う)。

- tunnel   : SSH 二段跳びの -L 端口転送
- protocol : 分帧協議(prompt + JPEG を送り、文字結果を受ける)
- prompt   : 場面説明 + 回答フォーマットの共通足場 + parse_label
- grading  : 4クラス採点(正解率 / クラス別 precision / 混同行列)

server 自体はモード非依存(prompt + 画像 -> 文字)なので、テキスト化 / オーバーレイ
の違いはすべて local 側(このパッケージを使う各 mode の client)で吸収する。
"""
