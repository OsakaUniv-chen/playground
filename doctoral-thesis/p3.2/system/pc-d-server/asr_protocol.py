#!/usr/bin/env python3
"""audio_send.py（Python 3.8）と asr.py（Python 3.10）が共有する取り決め。

**この 2 本は別々の Python で動く**ので、値を各ファイルに書くと片方だけ
直したときに黙って食い違う ── 音は流れ続け、例外も出ず、**whisper が
別の速さの音として読んだ「それらしい文字」が出てくる**という形で外れる。
そうならないよう、ここが唯一の出所。

**config.env には置かない。** ここにあるのは配備ごとに変える設定ではなく、
変えられない仕様だから:

  - `RATE` / `CHANNELS` は **whisper の入力仕様**。16 kHz 単声道以外を
    渡すとモデルが正しく動かない。「現場で調整する値」ではない
  - `HEADER` / `MAGIC` は 2 プロセス間の電文の形。外から変える意味が無い

現場で動かす値（モデルの大きさ・言語・窓）は `config.env` の `ASR_*` にある。

**このファイルは Python 3.8 でも読めるように保つこと。** audio_send.py 側が
3.8 で動くので、新しい構文を入れるとそちらが import に失敗する。標準ライブラリ
以外に依存しないこと。
"""

import struct

# 電文: magic, len(source), unix_ns, len(pcm) のあとに source, pcm が続く
HEADER = struct.Struct("!4sBQI")
MAGIC = b"P32A"

# whisper の入力仕様。変更不可（変えるならモデルごと別物になる）
RATE = 16000
CHANNELS = 1

# gst に変換させる形。Python 側で resample しないで済むよう、
# 受信の appsink 直前でこの形に揃える（recv_ome.py の audio_caps）。
CAPS = f"audio/x-raw,format=S16LE,rate={RATE},channels={CHANNELS}"
