#!/usr/bin/env python3
"""OME から受けた音声 2 本を、文字起こし側（asr.py）へ流す。

    OME ── WebRTC ──> ここ（Python 3.8 + GStreamer）── TCP ──> asr.py（Python 3.10）

**なぜ 1 プロセスにしないのか。** 受信は `gi`（GStreamer）が要り、focal の
`python3-gi` はシステムの Python 3.8 用しか無い。一方 faster-whisper は 3.8 に
入らない（実測でビルドが失敗する）。**GStreamer が要る側と GPU が要る側を
分けて**、間を 16 kHz 単声道 PCM で繋ぐ。将来 OS の新しい機械に移して両方が
同じ Python で動くようになったら、この 1 本は落とせる。

**16 kHz 単声道への変換は gst にやらせる**（`audio_caps`）。whisper の入力が
その形なので、ここで揃えておけば Python 側の resample が丸ごと不要になる。

映像も同じ経路で渡せるが、いまは繋いでいない ── VLM が未実装で、必要な
フレームレートが決まっていないため（30 fps の生 RGB をそのまま流すと
100 MB/s になる。VLM の周期に合わせて間引いてから渡すことになる）。
"""

import os
import signal
import socket
import struct
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from recv_ome import OmeInputs, env  # noqa: E402

HEADER = struct.Struct("!4sBQI")     # magic, len(source), unix_ns, len(pcm)
MAGIC = b"P32A"
# asr.py と同じ値であること。片方だけ変えると無音になる（音は流れるが
# レートが合わず、whisper が別の速さの音として読む）。
ASR_CAPS = "audio/x-raw,format=S16LE,rate=16000,channels=1"


def log(level, text):
    print(f"[{level}] [audio_send]: {text}", flush=True)


class AsrLink:
    """asr.py への片方向の送出。**繋がらなくても落ちない。**

    asr.py はモデルの読み込みに時間がかかるので、こちらが先に上がるのが
    普通。届かないぶんは捨てる ── 溜めて後から流しても、文字起こしとしては
    もう古い（頭を向ける判断に使うので、遅れた発話は害になる）。
    """

    def __init__(self, port, retry_sec=3.0):
        self.port = port
        self.retry_sec = retry_sec
        self.sock = None
        self._last_try = 0.0
        self.n_sent = 0
        self.n_dropped = 0

    def _connect(self):
        if self.sock is not None:
            return True
        now = time.monotonic()
        if now - self._last_try < self.retry_sec:
            return False
        self._last_try = now
        try:
            s = socket.create_connection(("127.0.0.1", self.port), timeout=3.0)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock = s
            log("INFO", f"asr へ接続: 127.0.0.1:{self.port}")
            return True
        except OSError as e:
            log("WARN", f"asr へ繋がらない（{self.retry_sec:.0f}s 後に再試行）: {e}")
            return False

    def send(self, source, pcm, unix_ns):
        if not self._connect():
            self.n_dropped += 1
            return
        src = source.encode()
        try:
            self.sock.sendall(HEADER.pack(MAGIC, len(src), unix_ns, len(pcm))
                              + src + pcm)
            self.n_sent += 1
        except OSError as e:
            log("WARN", f"送信に失敗した。繋ぎ直す: {e}")
            self.close()
            self.n_dropped += 1

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


def main():
    port = int(env("ASR_PORT"))
    link = AsrLink(port)

    # 音声 2 本だけ。映像はいま使わないので繋がない（無駄に復号しない）。
    inp = OmeInputs(only=["mic", "operator"], audio_caps=ASR_CAPS,
                    logger=lambda lv, m: log(lv.upper(), m))

    # **実際に届いた形を 1 回だけ確かめる。** asr.py は 16 kHz 単声道の
    # 決め打ちで、レートが違っても例外は出ない ── whisper が別の速さの音
    # として読み、区切りの秒数も全部ずれる。**それらしい文字が出てくるので
    # 気付けない**（3 倍速の音を無理に読んだ結果が返る）。
    ok_by_key = {}

    def on_audio(key, a):
        ok = ok_by_key.get(key)
        if ok is None:                     # その音源の最初の 1 個だけ調べる
            ok = (a.rate == 16000 and a.channels == 1)
            ok_by_key[key] = ok
            if ok:
                log("INFO", f"{key}: {a.rate} Hz {a.channels}ch を確認")
            else:
                log("ERROR", f"{key}: {a.rate} Hz {a.channels}ch で届いている。"
                             f"16000 Hz 1ch のはず ── audio_caps が効いていない。"
                             f"**この音源は送らない**（流すと壊れた文字起こしに"
                             f"なるだけで、エラーにはならない）")
        if not ok:
            return
        link.send(key, a.data, a.unix_ns)

    inp.add_audio_sink(on_audio)

    def _bye(signum, _frame):
        log("INFO", f"signal {signum} を受けた。終了する")
        inp.stop()
        link.close()
        os._exit(0)

    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)

    inp.start()
    log("INFO", f"音声 2 本を asr へ中継する（{ASR_CAPS}）")

    while True:
        time.sleep(20.0)
        st = inp.stats()
        parts = []
        for key in ("mic", "operator"):
            s = st.get(key)
            if s is None:
                continue
            age = f"{s['age']:.1f}s" if s["age"] is not None else "-"
            parts.append(f"{key}={s['audio']}({age})")
        log("INFO", f"受信 {' '.join(parts)} / asr へ {link.n_sent} 送信・"
                    f"{link.n_dropped} 破棄")


if __name__ == "__main__":
    main()
