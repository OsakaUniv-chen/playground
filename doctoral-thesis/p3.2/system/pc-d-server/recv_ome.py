#!/usr/bin/env python3
"""PC-D の 4 入力を OME からまとめて受ける。

    <robot>stream    映像（Xacti）        場面理解
    <robot>soundmap  音響マップ           誰が喋っているかの手がかり
    <robot>mic       機体マイク           現場の音・発話の有無
    operatormic      操作者の音声         操作者が何を言ったか

4 本とも `common/ome_receiver.py` で受ける。OmeReceiver はインスタンスごとに
専用の GLib.MainContext を持つので、1 プロセスで並行して回せる。

**ここでは記録しない。** 記録は PC-B の bag だけ。
こちらが持つのは「いま最新の 1 枚」だけで、履歴は溜めない。推論が
間に合わなければ黙って古い枚を捨てる（`latest()` は常に最新を返す）。

使い方（推論側）:
    inp = OmeInputs()
    inp.start()
    frame = inp.latest_video("stream")      # Frame or None
    if frame is not None:
        rgb = frame.array()                 # (h, w, 3) uint8, numpy があれば
"""

import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "common"))

from ome_receiver import OmeReceiver  # noqa: E402

try:
    import numpy as np
except ImportError:                    # numpy が無くても受信自体は動く
    np = None


def env(k, d=None):
    """config.env が持つ項目には既定値を渡さない（二重定義にすると片方が古くなる）。"""
    if d is None:
        try:
            return os.environ[k]
        except KeyError:
            raise RuntimeError(
                f"{k} が未設定。env.sh を読まずに起動している"
                f"（common/config.env か pc-d-server/config.env が設定する）"
            ) from None
    return os.environ.get(k, d)


class Frame:
    """受け取った 1 枚。`data` は行パディング込みの生バイト列。"""

    __slots__ = ("data", "width", "height", "stride", "unix_ns", "n")

    def __init__(self, data, width, height, stride, unix_ns, n):
        self.data = data
        self.width = width
        self.height = height
        self.stride = stride
        self.unix_ns = unix_ns
        self.n = n

    def array(self):
        """(h, w, 3) uint8 の RGB 配列にする。numpy が無ければ None。

        gst は行を 4 byte 境界に揃えるので、幅によっては行末に詰め物が入る。
        stride から復元してから切り落とす。
        """
        if np is None:
            return None
        a = np.frombuffer(self.data, dtype=np.uint8)
        if self.stride * self.height != a.size:
            return None
        return a.reshape(self.height, self.stride)[:, : self.width * 3].reshape(
            self.height, self.width, 3
        )

    def age_sec(self):
        return time.time() - self.unix_ns / 1e9


class Audio:
    __slots__ = ("data", "rate", "channels", "unix_ns", "n")

    def __init__(self, data, rate, channels, unix_ns, n):
        self.data = data
        self.rate = rate
        self.channels = channels
        self.unix_ns = unix_ns
        self.n = n

    def array(self):
        """(sample, channel) の int16 配列。numpy が無ければ None。"""
        if np is None:
            return None
        a = np.frombuffer(self.data, dtype=np.int16)
        if self.channels > 1:
            a = a.reshape(-1, self.channels)
        return a

    def age_sec(self):
        return time.time() - self.unix_ns / 1e9


class OmeInputs:
    """4 本の受信をまとめて持ち、それぞれの「最新」を保持する。"""

    #  key       -> (環境変数, 種別)
    #  stream key の実体は common/config.env が持つ（ここに既定値を書くと
    #  送出側の PC-B と食い違ったまま「繋がらない」だけになる）。
    STREAMS = {
        "stream":   ("STREAM_KEY_MAIN", "video"),
        "soundmap": ("STREAM_KEY_SOUNDMAP", "video"),
        "mic":      ("STREAM_KEY_MIC", "audio"),
        "operator": ("STREAM_KEY_OPERATOR_MIC", "audio"),
    }

    def __init__(self, host=None, port=None, app=None, only=None, logger=None,
                 audio_caps=None):
        """audio_caps:
        音声を appsink の直前で揃えたいときに
        `"audio/x-raw,format=S16LE,rate=16000,channels=1"` のように渡す。
        文字起こし（asr.py）はこの形しか受け付けないので、**変換は
        ここで gst にやらせる**（Python 側で resample すると音声 2 本ぶんの
        CPU がまるごと無駄になる）。None なら OME から来たまま。
        """
        # PC-C の tailscale アドレス（pc-d-server/config.env の OME_HOST）
        self.host = host or env("OME_HOST")
        self.port = int(port or env("OME_WS_PORT"))
        self.app = app or env("OME_APP")
        self.log = logger or (lambda lv, m: print(f"[{lv}] {m}", flush=True))
        self.audio_caps = audio_caps

        self._lock = threading.Lock()
        self._latest = {}
        self._audio_sinks = []
        self.rx = {}

        for key, (var, kind) in self.STREAMS.items():
            if only and key not in only:
                continue
            self.rx[key] = self._make(key, env(var), kind)

    def add_audio_sink(self, fn):
        """届いた音声バッファを**すべて** `fn(key, Audio)` に渡す。

        `latest_audio()` は「いま最新の 1 個」しか持たない ── 推論は
        間に合わなければ捨ててよい、という前提の作りで、映像はそれで正しい。
        **文字起こしはそれでは成り立たない**（落ちたぶんの発話が丸ごと
        消える）ので、連続で要る側はこちらで受ける。

        fn は受信スレッドから呼ばれる。重い処理をここでやると受信が詰まるので、
        溜めるだけにして別スレッドで処理すること（asr.py はそうしている）。
        """
        self._audio_sinks.append(fn)

    def _make(self, key, stream, kind):
        def on_video(data, sample, _k=key):
            s = sample.get_caps().get_structure(0)
            w, h = s.get_value("width"), s.get_value("height")
            stride = len(data) // h if h else 0
            self._put(_k, Frame(data, w, h, stride, self._stamp(sample),
                                self._count(_k)))

        def on_audio(data, sample, _k=key):
            s = sample.get_caps().get_structure(0)
            item = Audio(data, s.get_value("rate"), s.get_value("channels"),
                         self._stamp(sample), self._count(_k))
            self._put(_k, item)
            for fn in self._audio_sinks:
                try:
                    fn(_k, item)
                except Exception as e:      # 1 個が転んでも受信は続ける
                    self.log("warn", f"audio sink 例外: {e}")

        is_video = kind == "video"
        return OmeReceiver(
            self.host, self.port, self.app, stream,
            on_video=on_video if is_video else None,
            on_audio=None if is_video else on_audio,
            logger=lambda lv, m, _s=stream: self.log(lv, f"[{_s}] {m}"),
            # 推論に食わせるので RGB で受ける
            video_format="RGB" if is_video else None,
            audio_caps=None if is_video else self.audio_caps,
        )

    @staticmethod
    def _stamp(_sample):
        """到着時刻（PC-D のローカル時計）。

        記録の基準時計は PC-B なので、ここの時刻は「何秒前の画か」を
        知るためだけに使う。bag には残らない。
        """
        return time.clock_gettime_ns(time.CLOCK_REALTIME)

    def _count(self, key):
        prev = self._latest.get(key)
        return (prev.n + 1) if prev is not None else 1

    def _put(self, key, item):
        with self._lock:
            self._latest[key] = item

    # ---- 推論側が使う ----

    def latest(self, key):
        with self._lock:
            return self._latest.get(key)

    def latest_video(self, key):
        v = self.latest(key)
        return v if isinstance(v, Frame) else None

    def latest_audio(self, key):
        a = self.latest(key)
        return a if isinstance(a, Audio) else None

    def start(self):
        for key, rx in self.rx.items():
            self.log("info", f"{key}: {rx.url}")
            rx.start()

    def stop(self):
        for rx in self.rx.values():
            rx.stop()

    def stats(self):
        out = {}
        for key, rx in self.rx.items():
            item = self.latest(key)
            age = item.age_sec() if item is not None else None
            out[key] = {
                "video": rx.n_video,
                "audio": rx.n_audio,
                # 古い GStreamer（PC-D の 1.16）には connection-state が無く
                # rx.connected が立たない。実際に届いているかは
                # 「最近データが来たか」で見るほうが確実。
                "connected": rx.connected or (age is not None and age < 2.0),
                "age": age,
            }
        return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="PC-D の 4 入力を OME から受ける")
    ap.add_argument("--host", default=None, help="既定は config.env の OME_HOST")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--app", default=None)
    ap.add_argument("--only", nargs="*", default=None,
                    help="stream soundmap mic operator のうち受けるものだけ")
    ap.add_argument("--seconds", type=float, default=20.0)
    a = ap.parse_args()

    inp = OmeInputs(a.host, a.port, a.app, only=a.only)
    inp.start()

    t0 = time.time()
    while time.time() - t0 < a.seconds:
        time.sleep(2.0)
        parts = []
        for key, s in inp.stats().items():
            n = s["video"] + s["audio"]
            age = f"{s['age']:.1f}s" if s["age"] is not None else "-"
            mark = "OK " if s["connected"] and n else ".. "
            parts.append(f"{mark}{key}={n}({age})")
        print(f"  {time.time()-t0:5.1f}s  " + "  ".join(parts), flush=True)

    inp.stop()
    ok = all(s["video"] + s["audio"] > 0 for s in inp.stats().values())
    print("全入力そろった" if ok else "届いていない入力がある（送出側を確認）")
