#!/usr/bin/env python3
"""PC-D の 4 入力を OME からまとめて受ける（設計 §4.1）。

    <robot>stream    映像（Xacti）        場面理解
    <robot>soundmap  音響マップ           誰が喋っているかの手がかり
    <robot>mic       機体マイク           現場の音・発話の有無
    operatormic      操作者の音声         操作者が何を言ったか

4 本とも `common/ome_receiver.py` で受ける。OmeReceiver はインスタンスごとに
専用の GLib.MainContext を持つので、1 プロセスで並行して回せる。

**ここでは記録しない。** 記録は PC-B の bag だけ（設計 §5.1）。
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
sys.path.insert(0, os.path.join(_HERE, "..", "..", "common"))

from ome_receiver import OmeReceiver  # noqa: E402

try:
    import numpy as np
except ImportError:                    # numpy が無くても受信自体は動く
    np = None


def env(k, d=""):
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

    #  key       -> (環境変数, 既定の stream key, 種別)
    STREAMS = {
        "stream":   ("STREAM_KEY_MAIN", "boxiestream", "video"),
        "soundmap": ("STREAM_KEY_SOUNDMAP", "boxiesoundmap", "video"),
        "mic":      ("STREAM_KEY_MIC", "boxiemic", "audio"),
        "operator": ("STREAM_KEY_OPERATOR_MIC", "operatormic", "audio"),
    }

    def __init__(self, host=None, port=None, app=None, only=None, logger=None,
                 use_turn=None):
        # OME_HOST は SSH トンネル越しなら 127.0.0.1 になる（config.env 参照）
        self.host = host or env("OME_HOST") or env("PC_C_IP", "127.0.0.1")
        self.port = int(port or env("OME_WS_PORT", "3333"))
        self.app = app or env("OME_APP", "app")
        self.use_turn = (env("OME_USE_TURN", "0") == "1"
                         if use_turn is None else use_turn)
        self.log = logger or (lambda lv, m: print(f"[{lv}] {m}", flush=True))

        self._lock = threading.Lock()
        self._latest = {}
        self.rx = {}

        for key, (var, default, kind) in self.STREAMS.items():
            if only and key not in only:
                continue
            self.rx[key] = self._make(key, env(var, default), kind)

    def _make(self, key, stream, kind):
        def on_video(data, sample, _k=key):
            s = sample.get_caps().get_structure(0)
            w, h = s.get_value("width"), s.get_value("height")
            stride = len(data) // h if h else 0
            self._put(_k, Frame(data, w, h, stride, self._stamp(sample),
                                self._count(_k)))

        def on_audio(data, sample, _k=key):
            s = sample.get_caps().get_structure(0)
            self._put(_k, Audio(data, s.get_value("rate"), s.get_value("channels"),
                                self._stamp(sample), self._count(_k)))

        is_video = kind == "video"
        return OmeReceiver(
            self.host, self.port, self.app, stream,
            on_video=on_video if is_video else None,
            on_audio=None if is_video else on_audio,
            logger=lambda lv, m, _s=stream: self.log(lv, f"[{_s}] {m}"),
            # 推論に食わせるので RGB で受ける
            video_format="RGB" if is_video else None,
            use_turn=self.use_turn,
        )

    @staticmethod
    def _stamp(_sample):
        """到着時刻（PC-D のローカル時計）。

        記録の基準時計は PC-B なので、ここの時刻は「何秒前の画か」を
        知るためだけに使う。bag には残らない（設計 §5.1）。
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
    ap.add_argument("--host", default=None, help="既定は OME_HOST（無ければ PC_C_IP）")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--app", default=None)
    ap.add_argument("--turn", action="store_true", default=None,
                    help="OME 内蔵 TURN(TCP) を使う。既定は OME_USE_TURN")
    ap.add_argument("--only", nargs="*", default=None,
                    help="stream soundmap mic operator のうち受けるものだけ")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--snapshot-dir", default=None,
                    help="最後に受けた映像をここに PPM で保存する")
    a = ap.parse_args()

    inp = OmeInputs(a.host, a.port, a.app, only=a.only, use_turn=a.turn)
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

    if a.snapshot_dir:
        os.makedirs(a.snapshot_dir, exist_ok=True)
        for key in ("stream", "soundmap"):
            f = inp.latest_video(key)
            if f is None:
                continue
            path = os.path.join(a.snapshot_dir, f"{key}.ppm")
            arr = f.array()
            with open(path, "wb") as fp:
                fp.write(f"P6\n{f.width} {f.height}\n255\n".encode())
                fp.write(arr.tobytes() if arr is not None
                         else f.data[: f.width * f.height * 3])
            print(f"  {path} ({f.width}x{f.height})", flush=True)

    inp.stop()
    ok = all(s["video"] + s["audio"] > 0 for s in inp.stats().values())
    print("全入力そろった" if ok else "届いていない入力がある（送出側を確認）")
