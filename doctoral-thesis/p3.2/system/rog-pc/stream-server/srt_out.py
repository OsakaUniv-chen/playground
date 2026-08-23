#!/usr/bin/env python3
"""把 BGR 帧推回 OME —— stream-server 侧「合成流」的共用出口。

    numpy BGR ──appsrc──▶ H.264 ──mpegtsmux──▶ SRT ──▶ OME(localhost)
                                                        streamid=<vhost>/<app>/<key>

`person_detect.py`（检测框 ＋ 录制标志）和 `soundmap_overlay.py`（鱼眼 ＋ 声音图）
都用它。**进 OME 一律 SRT**（system-architecture.md §1.1），所以这里不提供
RTMP/MPEGTS 的旁路 —— 旧实现要改 OME 的 `Server.xml` StreamMap 才能收 MPEG-TS，
SRT 的 `streamid` 不用改配置。

**★ 发布进程死得不干净的话，OME 会留下一个同名的僵尸流。** 之后再推同名的流，
OME 一律回

    Reject to add stream : there is already an incoming stream (<key>) with the same name

而 srtsink 被拒之后会立刻重连（约 20 ms 一次）猛敲 OME。所以这里**自己收总线、
自己退避重建**（RETRY_SEC 秒），并且在 srtsink 支持的版本上把它自己的
`auto-reconnect` 关掉，让重连只有一处。真遇到僵尸流只能重启 OME，日志里会把
这句话写出来，省得现场对着「连上了但没有画面」猜。

**★ `auto-reconnect` 是 GStreamer 1.22 才有的属性**，两台机器差别很大，都实测过：

| | 开发机 1.20.3 | **rog-server 1.24.2（生产）** |
|---|---|---|
| `auto-reconnect` | 没有 | 有，这里设成 false |
| 撞上僵尸流 | **直接把进程 abort 掉**（崩在 srtsink 内部，Python 侧拦不住） | 总线上报一次 error，5 s 后重建**一次就成功**，进程毫发无伤 |

1.24 上 OME 会在几秒内自己把僵尸流放掉（SRT 超时），所以退避重建正好能跨过去 ——
实测 SIGKILL 掉一个发布者再推同名流，只重建 1 次、推 297 帧 0 丢弃、退出码 0。

四个必须写死的地方，写错了表现都是「连了但看不到画面」：

**① x264enc 前必须显式 I420。** 上游是 BGR 时 x264enc 会自己挑 High 4:4:4，
浏览器（OvenPlayer）解不了 —— 连接建立、SDP 交换成功，然后一直黑屏。
**② vaapih264enc 必须写 profile。** 不写默认出 High，WebRTC 的 SDP 是
42e01f（constrained-baseline），同样解不了。
**③ `h264parse config-interval=-1`。** 每个 IDR 都重发 SPS/PPS，否则中途打开
播放器的人要等到下一个关键帧之后才可能出画（甚至一直不出）。
**④ `mpegtsmux alignment=7`**（7 × 188 = 1316 B）。和 robot-pc 那四条一致。

用法:
    out = SrtVideoOut("human_detect", 1080, 1080, 5, 2000, cfg)
    out.start(); out.push(bgr); out.stop()
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("GIO_USE_PROXY_RESOLVER", "dummy")   # 理由见 person_detect.py

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

# 出错之后隔多久重建一次。和 robot-pc 的 RESTART_SEC 取同一个数。
RETRY_SEC = 5.0


class SrtVideoOut:
    """一条「合成流」的出口。push() 不阻塞调用方。

    **推理/合成的循环绝不能被编码器堵住。** 所以 appsrc 是 block=false，后面
    紧跟一个 leaky=downstream 的 queue：编码器跟不上就丢新帧，而不是把上游
    卡住。这是给人看的监视流，掉几帧无所谓；而上游卡住会直接影响录制触发。
    """

    def __init__(self, key, width, height, fps, bitrate_kbps,
                 host, srt_port, vhost, app, latency_ms,
                 use_hw=True, logger=None):
        self.key = key
        self.w, self.h, self.fps = width, height, fps
        self.bitrate = bitrate_kbps
        self.uri = (f"srt://{host}:{srt_port}?mode=caller"
                    f"&latency={latency_ms}&streamid={vhost}/{app}/{key}")
        self.use_hw = use_hw
        self.log = logger or (lambda lv, m: print(f"[{lv}] {m}", flush=True))
        self.pipe = None
        self.src = None
        self.bus = None
        self._retry_at = 0.0
        self.n_push = 0
        self.n_drop = 0
        self.n_restart = 0

    # ---- 管线 ----

    def _encoder(self):
        if self.use_hw and Gst.ElementFactory.find("vaapih264enc") is not None:
            # ② 见文件头
            return ("videoconvert ! video/x-raw,format=NV12 "
                    f"! vaapih264enc rate-control=cbr bitrate={self.bitrate} "
                    f"keyframe-period={self.fps} "
                    "! video/x-h264,profile=constrained-baseline ")
        # ① 见文件头
        return ("videoconvert ! video/x-raw,format=I420 "
                f"! x264enc tune=zerolatency speed-preset=veryfast "
                f"bitrate={self.bitrate} key-int-max={self.fps} "
                "! video/x-h264,profile=baseline ")

    def _desc(self):
        return (
            "appsrc name=src is-live=true do-timestamp=true format=time "
            "block=false max-bytes=20000000 "
            f"caps=video/x-raw,format=BGR,width={self.w},height={self.h},"
            f"framerate={self.fps}/1 "
            # 编码器跟不上就丢新帧，不要回压上游
            "! queue max-size-buffers=3 leaky=downstream "
            f"! {self._encoder()}"
            "! h264parse config-interval=-1 "          # ③
            "! mpegtsmux alignment=7 "                 # ④
            f"! srtsink uri=\"{self.uri}\" sync=false{self._srt_extra()}"
        )

    @staticmethod
    def _srt_extra():
        """srtsink 自己的重连要关掉 —— 重连只应该有一处（见文件头）。

        1.22 以前没有这个属性，探一下再写，不然 parse_launch 直接失败。
        """
        el = Gst.ElementFactory.make("srtsink")
        if el is not None and el.find_property("auto-reconnect") is not None:
            return " auto-reconnect=false"
        return ""

    def start(self):
        Gst.init(None)                                  # 幂等
        self._build(first=True)

    def _build(self, first=False):
        desc = self._desc()
        self.pipe = Gst.parse_launch(desc)
        self.src = self.pipe.get_by_name("src")
        # **不要用 bus.add_signal_watch()。** 它把 GSource 挂在默认的
        # MainContext 上，而这两个进程的主循环都不是 GLib 的 —— 回调永远不会
        # 被派发，消息只会越堆越多，出错也没人知道。同步 pop 就没这问题，
        # 而且错误是在调用方线程里处理的（拆管线更安全）。
        self.bus = self.pipe.get_bus()
        self.pipe.set_state(Gst.State.PLAYING)
        enc = "vaapi" if "vaapih264enc" in desc else "x264"
        what = "推流" if first else f"重建推流（第 {self.n_restart} 次）"
        self.log("info", f"{what} {self.key} -> {self.uri.split('?')[0]} "
                         f"({self.w}x{self.h}@{self.fps} {self.bitrate}kbps {enc})")

    def _teardown(self):
        if self.pipe is not None:
            self.pipe.set_state(Gst.State.NULL)
        self.pipe = self.src = self.bus = None

    def _poll_bus(self):
        """非阻塞看一眼总线。返回 False 表示这条流挂了，要重建。"""
        if self.bus is None:
            return False
        msg = self.bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if msg is None:
            return True
        if msg.type == Gst.MessageType.ERROR:
            err, _dbg = msg.parse_error()
            self.log("warn",
                     f"推流 {self.key} 出错: {err.message} —— {RETRY_SEC:.0f} 秒后重建。"
                     f"**如果一直重建不上，多半是 OME 里还挂着一个同名的僵尸流**"
                     f"（上一个发布进程死得不干净），看 OME 日志有没有 "
                     f"「Reject to add stream ... same name」，有的话要重启 OME。"
                     f"检测和录制触发不受这条流影响。")
        else:
            self.log("warn", f"推流 {self.key} EOS —— {RETRY_SEC:.0f} 秒后重建")
        return False

    # ---- 推帧 ----

    def push(self, bgr):
        """把一枚 BGR 帧塞进管线。**任何情况下都不抛异常、不阻塞调用方。**"""
        now = time.monotonic()
        if self.pipe is None:
            if self._retry_at and now >= self._retry_at:
                self._retry_at = 0.0
                self.n_restart += 1
                self._build()
            else:
                return
        elif not self._poll_bus():
            self._teardown()
            self._retry_at = now + RETRY_SEC
            return
        if bgr.shape[0] != self.h or bgr.shape[1] != self.w:
            self.n_drop += 1
            if self.n_drop in (1, 100):
                self.log("warn", f"推流 {self.key}: 尺寸 {bgr.shape[1]}x{bgr.shape[0]} "
                                 f"和 caps {self.w}x{self.h} 对不上，丢弃")
            return
        # **不要用 Gst.Buffer.new_wrapped()。** 它的 introspection 标注是
        # (transfer full)，PyGObject 会把 Python 那块内存的所有权交给 GStreamer，
        # 之后 GStreamer 去 g_free 一块由 Python 分配的内存 —— 表现是**几十秒后
        # 在 GStreamer 的流线程里段错误**（主线程的栈看起来完全无辜，faulthandler
        # 只会指到当时在跑的那一行）。分配 ＋ fill 是把数据拷进 GStreamer 自己的
        # 内存里，没有歧义。1080×1080 一帧 3.5 MB，这一次拷贝可以忽略。
        data = bgr.tobytes()
        buf = Gst.Buffer.new_allocate(None, len(data), None)
        buf.fill(0, data)
        if self.src.emit("push-buffer", buf) != Gst.FlowReturn.OK:
            self.n_drop += 1
        else:
            self.n_push += 1

    def stop(self):
        # 不发 EOS：这是条 live 流，muxer 没什么要 flush 的，而在 srtsink 还连着
        # 的时候发 EOS 再立刻转 NULL 是条容易出事的路径。
        self._teardown()


def from_env(key_var, width, height, fps, bitrate_var, default_bitrate=2000,
             logger=None):
    """按 config.env 的约定建一个出口。缺哪个变量就报哪个，不给默认值。

    **推回本机的 OME 走的是 loopback**（`OME_HOST=127.0.0.1`）—— 和拉流那一侧
    同一个变量，同一台机器，不要绕出去再回来。
    """
    def need(k):
        try:
            return os.environ[k]
        except KeyError:
            raise SystemExit(f"[error] {k} 未设置 —— 没读到 config.env") from None

    return SrtVideoOut(
        key=need(key_var),
        width=width, height=height, fps=fps,
        bitrate_kbps=int(os.environ.get(bitrate_var, default_bitrate)),
        host=need("OME_HOST"), srt_port=int(need("OME_SRT_PORT")),
        vhost=need("OME_VHOST"), app=need("OME_APP"),
        latency_ms=int(os.environ.get("SRT_LATENCY", 20)),
        use_hw=os.environ.get("USE_HW_CODEC", "1") == "1",
        logger=logger,
    )
