#!/usr/bin/env python3
"""把 vlm-server 要的四路数据从 OME 收齐。

**三条流，四路数据。** 机体麦克风复用在 `fisheye` 里（架构 §1.1），不是独立
的一条 —— 操作者要在一个浏览器页面里音画同步地收，所以推的时候就复用了。
到了这边，那条流会出两个 pad，视频和音频各走各的：

    OME ──WebRTC──▶ fisheye     ─┬─ 视频 ──▶ fisheye    场面理解（喂 VLM 的帧）
                                 └─ 音频 ──▶ onboard    现场说话 → 文字
    OME ──WebRTC──▶ soundmap    ─── 视频 ──▶ soundmap   谁在说话的线索（喂 VLM 的图）
    OME ──WebRTC──▶ operatormic ─── 音频 ──▶ operator   操作者说话 → 文字

**视频只留最新的一枚，音频一个 buffer 都不能丢。** 这两者的要求正好相反：
推理跟不上就该扔掉旧帧（拿三秒前的画面判断没有意义），而转写少一段就是整句
话消失，事后补不回来。所以视频走 `latest_video()`（覆盖式），音频走
`add_audio_sink()`（全量回调）。

**这边不做记录。** 记录只在 robot-pc 的 bag 里（架构 §6.1）。这个进程持有的
只有「此刻最新的一枚」，历史不留。唯一的例外是转写出来的文字（asr.py 写的
transcript.jsonl）—— 那个在这里生成，别处没有。

用法（连调，不加载模型）:
    ./run.sh recv                  # 20 秒，打印每条流收到多少
    ./run.sh recv -- --seconds 60
"""
from __future__ import annotations

import os

# **必须在 import gi 之前设。** GIO 调 libproxy 抛的 C++ 异常会把进程带走，
# 理由和 stream-server 那边一样（架构 §7「rclpy 和 libsoup 共存」）。
# 这个进程虽然不碰 rclpy，但 CUDA 那套库同样会加载 libunwind。
os.environ.setdefault("GIO_USE_PROXY_RESOLVER", "dummy")

import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from collections import deque  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ome_receiver import OmeReceiver  # noqa: E402

try:
    import numpy as np
except ImportError:          # 没有 numpy 也能收，只是 array() 用不了
    np = None


def log(level, msg):
    print(f"[{level}] {msg}", flush=True)


def env(key):
    try:
        return os.environ[key]
    except KeyError:
        raise SystemExit(
            f"[error] {key} 未设置 —— 没读到 config.env。用 ./run.sh 启动。"
        ) from None


class Frame:
    """收到的一枚画面。`data` 是带行填充的原始字节。"""

    __slots__ = ("data", "width", "height", "stride", "unix_ns", "n")

    def __init__(self, data, width, height, stride, unix_ns, n):
        self.data = data
        self.width = width
        self.height = height
        self.stride = stride
        self.unix_ns = unix_ns
        self.n = n

    def array(self):
        """还原成 (h, w, 3) uint8。没有 numpy 就返回 None。

        **gst 把每行对齐到 4 字节**，所以宽度不是 4 的倍数时行尾有填充。
        按 stride 还原再切掉，直接 reshape 会整幅斜掉。
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
    """收到的一块音频。`data` 是交织的 S16LE。"""

    __slots__ = ("data", "rate", "channels", "unix_ns", "n")

    def __init__(self, data, rate, channels, unix_ns, n):
        self.data = data
        self.rate = rate
        self.channels = channels
        self.unix_ns = unix_ns
        self.n = n

    def array(self):
        if np is None:
            return None
        a = np.frombuffer(self.data, dtype=np.int16)
        return a.reshape(-1, self.channels) if self.channels > 1 else a

    def age_sec(self):
        return time.time() - self.unix_ns / 1e9


class Inputs:
    """三条流收进来，按四路数据交出去。

        inp = Inputs(audio_caps=asr.CAPS)
        inp.add_audio_sink(lambda src, a: ...)   # src = onboard | operator
        inp.start()
        f = inp.latest_video("fisheye")          # Frame or None
    """

    # 数据路 -> (stream key 的环境变量, 那条流上取哪种 pad)
    #
    # **fisheye 出现两次是有意的**：同一条流的两个 track 分给两路数据。
    # stream key 的实体在 config.env 里 —— 这里写死默认值的话，改了推流侧
    # 就只是「连上了但没有数据」，看不出原因。
    CHANNELS = {
        "fisheye":  ("STREAM_KEY_FISHEYE", "video"),
        "onboard":  ("STREAM_KEY_FISHEYE", "audio"),
        "soundmap": ("STREAM_KEY_SOUNDMAP", "video"),
        "operator": ("STREAM_KEY_OPERATORMIC", "audio"),
    }

    def __init__(self, host=None, port=None, app=None, only=None,
                 logger=None, audio_caps=None, video_format=None):
        """audio_caps:
            想让音频以固定的形状交上来时传
            `"audio/x-raw,format=S16LE,rate=16000,channels=1"` 这样的字符串。
            **转换交给 gst 做** —— whisper 只吃 16 kHz 单声道，在 Python 里
            重采样两路音频纯属白烧 CPU。
        only:
            只收其中几路（名字用上面 CHANNELS 的 key）。**按数据路给，不是
            按流给** —— 只要 `onboard` 的话，fisheye 那条流照样要连，只是
            不接视频的那个 pad，省掉一路 H.264 解码。
        """
        self.host = host or env("OME_HOST")
        self.port = int(port or env("OME_WS_PORT"))
        self.app = app or env("OME_APP")
        self.video_format = video_format or env("VIDEO_FORMAT")
        self.audio_caps = audio_caps
        self.log = logger or log

        want = set(only) if only else set(self.CHANNELS)
        bad = want - set(self.CHANNELS)
        if bad:
            raise SystemExit(f"[error] 没有这几路: {sorted(bad)}（有的是 "
                             f"{sorted(self.CHANNELS)}）")
        self.want = want

        # **视频留一段，不是只留最新一枚。** VLM 要多帧输入（架构 §5.1），
        # 所以每个视频路各挂一个按时长封顶的环形缓冲。
        #
        # **只按时间抽稀，不缩尺寸。** 1080×1080×3 = 3.5 MB/帧，按
        # BUFFER_FPS=5、BUFFER_SEC=15 算是 75 帧 ≈ 262 MB —— 这台机器 62 GB，
        # 随便留。存原始的好处是要多大尺寸是造 prompt 时才决定的，
        # 这里先缩了就回不去了。
        self.buffer_sec = float(env("BUFFER_SEC"))
        self.buffer_fps = float(env("BUFFER_FPS"))

        self._lock = threading.Lock()
        self._latest = {}
        self._buf = {}              # 数据路 -> deque[Frame]，只有视频路有
        self._audio_sinks = []
        self._n = {}

        # 一条流一个 OmeReceiver。**fisheye 那条只建一个** —— 视频和音频是
        # 同一个 WebRTC session 的两个 track，建两个等于连两次、拉两份。
        self.rx = {}
        self._chan_of = {}          # (stream key, video/audio) -> 数据路名字
        for chan in sorted(want):
            var, kind = self.CHANNELS[chan]
            stream = env(var)
            self._chan_of[(stream, kind)] = chan
            if stream not in self.rx:
                self.rx[stream] = None      # 占位，下面统一建
        for stream in self.rx:
            self.rx[stream] = self._make(stream)

    # ---- 建收流 ----

    def _make(self, stream):
        v_chan = self._chan_of.get((stream, "video"))
        a_chan = self._chan_of.get((stream, "audio"))

        def on_video(data, sample):
            st = sample.get_caps().get_structure(0)
            w, h = st.get_value("width"), st.get_value("height")
            self._put(v_chan, Frame(data, w, h, len(data) // h if h else 0,
                                    self._stamp(), self._count(v_chan)))

        def on_audio(data, sample):
            st = sample.get_caps().get_structure(0)
            item = Audio(data, st.get_value("rate"), st.get_value("channels"),
                         self._stamp(), self._count(a_chan))
            self._put(a_chan, item)
            # **全量交出去。** latest 只留一个，转写要的是连续的音频。
            for fn in self._audio_sinks:
                try:
                    fn(a_chan, item)
                except Exception as e:      # 一个 sink 翻了不能连累收流
                    self.log("warn", f"[{a_chan}] audio sink 异常: {e}")

        return OmeReceiver(
            self.host, self.port, self.app, stream,
            on_video=on_video if v_chan else None,
            on_audio=on_audio if a_chan else None,
            logger=lambda lv, m, _s=stream: self.log(lv, f"[{_s}] {m}"),
            video_format=self.video_format if v_chan else None,
            audio_caps=self.audio_caps if a_chan else None,
        )

    def add_audio_sink(self, fn):
        """每一块音频都以 `fn(source, Audio)` 交出去，source = onboard|operator。

        **fn 在 gst 的流线程里被调用。** 在里面做重活会把收流堵住 ——
        只往队列里塞，另开线程处理（asr.py 就是这么做的）。
        """
        self._audio_sinks.append(fn)

    # ---- 时刻与计数 ----

    @staticmethod
    def _stamp():
        """到达时刻（本机时钟）。

        **不是采集时刻。** 过了 OME 那一层原本的时间戳就没了（架构 §6.1），
        所以这个数只能用来看「这一枚有多旧」。要精确的采集时刻去 bag 里取 ——
        那是 robot-pc 的活，这边一个字节都不记录。
        """
        return time.clock_gettime_ns(time.CLOCK_REALTIME)

    def _count(self, chan):
        # **同一条 fisheye 流的视频和音频是两个 gst 线程**，各自数各自的路。
        # 用同一把锁，免得两路同时进来的时候读改写撞上。
        with self._lock:
            n = self._n.get(chan, 0) + 1
            self._n[chan] = n
        return n

    def _put(self, chan, item):
        with self._lock:
            self._latest[chan] = item
            if not isinstance(item, Frame) or self.buffer_sec <= 0:
                return
            b = self._buf.get(chan)
            if b is None:
                b = self._buf[chan] = deque()
            # 按 BUFFER_FPS 抽稀。**收流照收，只是不都往缓冲里塞** ——
            # 30 fps 全存的话 15 秒就是 1.6 GB，而 VLM 一次也就用几帧。
            if self.buffer_fps > 0 and b and \
                    (item.unix_ns - b[-1].unix_ns) / 1e9 < 1.0 / self.buffer_fps:
                return
            b.append(item)
            cutoff = item.unix_ns - self.buffer_sec * 1e9
            while b and b[0].unix_ns < cutoff:
                b.popleft()

    # ---- 推理侧用的 ----

    def latest(self, chan):
        with self._lock:
            return self._latest.get(chan)

    def latest_video(self, chan):
        v = self.latest(chan)
        return v if isinstance(v, Frame) else None

    def latest_audio(self, chan):
        a = self.latest(chan)
        return a if isinstance(a, Audio) else None

    def frames(self, n=1, span=0.0, chan="fisheye", pair_with="soundmap"):
        """取 `n` 帧、跨最近 `span` 秒，每帧配上时刻最近的 `pair_with` 帧。

        **配对是这边的活。** 两条流各自到达、各自抖动，谁跟谁是同一时刻要按
        时间戳对 —— 判断「谁在说话」正依赖这个对得上。每一对都报出 `dt`
        （两帧的时刻差），对不齐时看得见。

        **★ 这里的时刻是「到达本机的时刻」，过了广域网**（见 `_stamp`）。
        鱼眼 30 fps、声音图 15 fps，两边的抖动是独立的，所以 `dt` 有可能和
        声音图一帧的间隔（67 ms）同量级。真发现画面和斑点对不上，先看这个数。

        返回按时间从旧到新：
            [{"t": 采集侧到达时刻, "video": Frame, "pair": Frame|None,
              "dt": 秒|None}, ...]
        缓冲里不够 n 帧就有多少给多少（**不补，不重复**）。
        """
        with self._lock:
            vb = list(self._buf.get(chan, ()))
            pb = list(self._buf.get(pair_with, ())) if pair_with else []
        if not vb:
            return []

        newest = vb[-1].unix_ns
        if span > 0:
            lo = newest - span * 1e9
            vb = [f for f in vb if f.unix_ns >= lo]
        if n <= 1 or len(vb) <= 1:
            picked = vb[-1:]
        elif len(vb) <= n:
            picked = vb
        else:
            # **按时间等间隔挑，不是按下标。** 缓冲里的帧间隔本来就不均匀
            # （网络抖动、抽稀的取整），按下标挑会挑出时间上偏斜的一组。
            t0, t1 = vb[0].unix_ns, vb[-1].unix_ns
            picked, used = [], set()
            for k in range(n):
                want = t0 + (t1 - t0) * k / (n - 1)
                j = min(range(len(vb)), key=lambda i: abs(vb[i].unix_ns - want))
                if j not in used:
                    used.add(j)
                    picked.append(vb[j])

        out = []
        for f in picked:
            best, dt = None, None
            if pb:
                best = min(pb, key=lambda g: abs(g.unix_ns - f.unix_ns))
                dt = (best.unix_ns - f.unix_ns) / 1e9
            out.append({"t": f.unix_ns / 1e9, "video": f, "pair": best, "dt": dt})
        return out

    def buffered(self, chan):
        """缓冲里现在有几帧、跨多长时间（状态面板用）。"""
        with self._lock:
            b = self._buf.get(chan)
            if not b:
                return 0, 0.0
            return len(b), (b[-1].unix_ns - b[0].unix_ns) / 1e9

    def start(self):
        for stream, rx in self.rx.items():
            chans = [c for (s, _), c in self._chan_of.items() if s == stream]
            self.log("info", f"{'＋'.join(sorted(chans))}: {rx.url}")
            rx.start()

    def stop(self):
        for rx in self.rx.values():
            rx.stop()

    def stats(self):
        """每一路：收到多少、最新的那枚多旧、活着没有、以及它长什么样。

        `desc` 是**实际收到的**形状（不是配置里写的）—— 状态面板显示它，
        是为了让「推上来的东西和以为的不一样」当场看得出来（音频速率不对
        的话 whisper 不会报错，只会读出速度不对的、看着挺像的文字）。
        """
        out = {}
        for chan in sorted(self.want):
            item = self.latest(chan)
            age = item.age_sec() if item is not None else None
            if isinstance(item, Frame):
                desc = f"{item.width}x{item.height}"
            elif isinstance(item, Audio):
                desc = f"{item.rate}Hz {item.channels}ch"
            else:
                desc = ""
            out[chan] = {
                "n": self._n.get(chan, 0),
                # **不看 rx.connected 就够了** —— signalling 连上但媒体
                # 不来是最常见的故障（少 gstreamer1.0-nice 就长这样），
                # 所以判据是「最近真的有数据到」。
                "alive": age is not None and age < 2.0,
                "age": age,
                "desc": desc,
                "kind": "video" if self.CHANNELS[chan][1] == "video" else "audio",
            }
        return out


def main():
    import argparse
    import signal

    ap = argparse.ArgumentParser(description="从 OME 收齐 vlm-server 的四路数据")
    ap.add_argument("--seconds", type=float, default=20.0, help="收多久（0 = 一直收）")
    ap.add_argument("--only", nargs="*", default=None,
                    help="只收其中几路: fisheye onboard soundmap operator")
    a = ap.parse_args()

    inp = Inputs(only=a.only)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    inp.start()
    t0 = time.monotonic()
    while not stop.is_set():
        if a.seconds > 0 and time.monotonic() - t0 >= a.seconds:
            break
        stop.wait(2.0)
        parts = []
        for chan, s in inp.stats().items():
            age = f"{s['age']:.1f}s" if s["age"] is not None else "-"
            parts.append(f"{'●' if s['alive'] else '○'}{chan}={s['n']}({age})")
        print(f"  {time.monotonic() - t0:5.1f}s  " + "  ".join(parts), flush=True)

    inp.stop()
    st = inp.stats()
    missing = [c for c, s in st.items() if s["n"] == 0]
    if missing:
        log("error", f"这几路一个数据都没收到: {' '.join(missing)} —— "
                     f"推流侧起了没有？`gst-inspect-1.0 nicesrc` 查过没有？")
        return 1
    log("info", "四路都收到了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
