#!/usr/bin/env python3
"""操作者语音 -> 机体扬声器（AT-CSP1）。

    OME ──WebRTC(Opus)──▶ ome_receiver ─ appsrc ─ audioconvert ─ alsasink

**这是 robot-pc 上唯一一条下行流。** 另外四条是 SRT 往 OME 推，这条反过来。
出 OME 一律 WebRTC，所以它不能像上行那样
`gst-launch` 一行搞定 —— 收流用同目录的 `ome_receiver.py`（signalling、
libsoup 2.4/3.0 的差异、ICE 那堆坑都在那份里，有实绩）。

**放音必须用 AT-CSP1 本身。** 它自带硬件回声消除，操作者的声音从它自己放出去，
机体麦克风才不会把这段声音又收回去送回操作者那边（换个别的喇叭放，回声消除
就不认识那路信号了）。

**记录**：加 `--publish` 会把收到的音频同时往 ROS 上发一份
（`operator_mic/audio`），由同机的 recorder.py 收进 bag。不加就完全不碰 ROS。

用法（正常由 start_gstreamer.sh 起，设备由它解析好传进来）:
    python3 speaker.py --device hw:CARD=ATCSP1,DEV=0
    python3 speaker.py --fake             # 不碰声卡，只验证收流
    python3 speaker.py --fake --seconds 10
"""
from __future__ import annotations

# GIO 不要去找 proxy。理由和 ome_receiver.py 里那段一样（libproxy 的 C++
# 异常），必须在 import gi.repository 之前 —— GIO 是延迟加载模块。
# 连的是 LAN，本来也用不着 proxy。
import os

os.environ.setdefault("GIO_USE_PROXY_RESOLVER", "dummy")

import argparse  # noqa: E402
import array  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# **robot-pc 这个目录要能单独扔到机体上跑**，所以收流那份就放在同目录，
# 不去引隔壁的 stream-server/。那边那份是源头，这里是副本，改要改源头
# 再拷过来（两份现在是逐字节一样的，diff 一下就知道有没有走样）。
sys.path.insert(0, HERE)          # 被 import 进来跑的时候也找得到
try:
    from ome_receiver import OmeReceiver  # noqa: E402
except Exception as e:            # gi / libsoup 缺了也会掉进来
    raise SystemExit(f"[error] 导入同目录的 ome_receiver 失败: {e}") from None

# 多久报一行收到多少 [s]。状态面板拿这一行显示（和 soundmap.py 一样的做法）。
REPORT_SEC = 10


def log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}", flush=True)


def env(key: str) -> str:
    """config.env 负责的项，这里不给默认值（两处写默认值必然对不上）。"""
    try:
        return os.environ[key]
    except KeyError:
        raise SystemExit(
            f"[error] {key} 未设置 —— 没读到 config.env。"
            f"用 ./start_gstreamer.sh 启动，或先 `source config.env`。"
        ) from None


class Speaker:
    def __init__(self, device: str | None, jitter_ms: int, publish: bool = False):
        self.device = device            # None = --fake
        self.jitter_ms = jitter_ms
        self.publish = publish
        self.ros = None                 # RosPub，--publish 时才有
        self.spk = None                 # appsrc
        self.rx = None
        self.pipeline = None
        self.loop = None
        self.caps = None                # 当前压给 appsrc 的 caps
        self.rc = 0
        self.n = 0                      # 这 10 秒收到多少包
        self.n_total = 0

    # ---- 放音那一端 ----

    def pipeline_desc(self) -> str:
        # 音从 ome_receiver 那边 push 进 appsrc，这里只负责往声卡送。
        #
        # **caps 不在这里钉死**（见 _on_audio）：钉死了 OME 那边一变
        # 就是 not-negotiated，而且只有扬声器这一路静默地死掉，别的看不出来。
        #
        # leaky=downstream：网络抖一下宁可丢掉旧的音，也不要把延迟攒起来 ——
        # 这是对话，晚到的话本来也没用了。200 ms 是丢之前允许攒的量。
        #
        # sync=false：这条流的时钟在操作者那台机器上，跟本机时钟没关系；
        # 节奏由声卡的环形缓冲自己把着（写满就阻塞）。
        sink = ("fakesink sync=false" if self.device is None
                else f"alsasink device={self.device} sync=false")
        return (
            "appsrc name=spk is-live=true format=time do-timestamp=true "
            "block=false max-bytes=2000000 "
            "! queue max-size-time=200000000 max-size-buffers=0 max-size-bytes=0 "
            "leaky=downstream "
            f"! audioconvert ! audioresample ! {sink}"
        )

    def _on_bus(self, _bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            # 声卡被别人占着、拔掉了，都会走到这里。退出去让监视循环重起，
            # 重起时会重新解析设备（见 start_gstreamer.sh 的 run_speaker）。
            log("error", f"扬声器管线: {err} | {dbg}")
            self.rc = 1
            self.loop.quit()
        elif msg.type == Gst.MessageType.WARNING:
            err, dbg = msg.parse_warning()
            log("warn", f"扬声器管线: {err} | {dbg}")

    # ---- 收流那一端 ----

    def _ros_setup(self):
        """记录那一半。**按需 import** —— 不加 --publish 就完全不碰 ROS。"""
        from gst_ros import RosPub
        from teleop_msgs.msg import AudioChunk

        robot = os.environ.get("ROBOT_NAME")
        if not robot:
            raise SystemExit("[error] --publish 要 ROBOT_NAME（source config.env）")
        self.AudioChunk = AudioChunk
        self.ros = RosPub("speaker", robot, log)
        self.pub = self.ros.publisher("operator_mic/audio", AudioChunk, 200)

    def _publish(self, data, caps):
        """把收到的这块音频发给 recorder。

        **时刻用"这台机器收到的时刻"**，不用 sample 的 PTS —— 这块音频是在
        操作者那台机器上采的，PTS 属于那边的管线，和本机的基准时钟没有关系。
        基准时钟只有 robot-pc 一个（架构文档 §6.4），到达时刻就是这条流在
        这个基准下唯一说得清的时刻。
        """
        st = caps.get_structure(0)
        ok_r, rate = st.get_int("rate")
        ok_c, ch = st.get_int("channels")
        if not (ok_r and ok_c):
            return
        m = self.AudioChunk()
        self.ros.stamp(m, time.clock_gettime_ns(time.CLOCK_REALTIME))
        m.header.frame_id = "operator_mic"
        m.encoding = st.get_string("format") or "S16LE"
        m.channels = ch
        m.sample_rate = rate
        m.samples = len(data) // (2 * ch) if ch else 0
        # array.array 而不是 bytes：rosidl 的 uint8[] setter 拿到 bytes 会
        # 逐字节检查，慢两个数量级（见 soundmap.py 里那段注释）。
        m.data = array.array("B", data)
        self.pub.publish(m)

    def _on_audio(self, data, sample):
        """ome_receiver 的流线程调过来。别在这里做重活。"""
        caps = sample.get_caps()
        if self.caps is None or not caps.is_equal(self.caps):
            # 用实际到达的 caps，不用猜的。重连之后格式变了也跟着变。
            self.spk.set_property("caps", caps)
            self.caps = caps
            log("info", f"扬声器输入格式: {caps.to_string()}")
        if self.ros is not None:
            self._publish(data, caps)
        if self.spk.emit("push-buffer", Gst.Buffer.new_wrapped(data)) != Gst.FlowReturn.OK:
            # 丢一包不值得刷屏，10 秒那一行会显示到底收了多少。
            return
        self.n += 1
        self.n_total += 1

    def _report(self):
        if self.n:
            log("info", f"[10s] 收到 {self.n} 包")
        else:
            # 操作者不说话的时候本来就没有音频（麦克风常开，但对面可能
            # 根本没起推流）。**所以这条流不能用"有没有数据"判死** ——
            # 别的流那套数据看门狗对它不适用，见 start_gstreamer.sh 的 WATCHED。
            log("info", "[10s] 静默（对面没在推？）")
        self.n = 0
        return True

    # ---- 跑 ----

    def run(self, seconds: float = 0.0) -> int:
        Gst.init(None)

        # **缺 gstreamer1.0-nice 的话 WebRTC 会静默失败** —— webrtcbin 直接
        # 链了 libnice，但 ICE 的实体是 nicesrc/nicesink 这两个 gst element，
        # 在另一个包里。缺了只会打一句警告然后 create-answer 不返回 answer，
        # 表现是"signalling 通了、就是永远没有声音"。这里提前挡住。
        if Gst.ElementFactory.find("nicesrc") is None:
            raise SystemExit(
                "[error] 没有 nicesrc —— 装 gstreamer1.0-nice"
                "（sudo apt install gstreamer1.0-nice）。"
                "缺了它 WebRTC 收流会静默失败。"
            )

        if self.publish:
            self._ros_setup()

        desc = self.pipeline_desc()
        log("info", f"[放音] {desc}")
        self.pipeline = Gst.parse_launch(desc)
        self.spk = self.pipeline.get_by_name("spk")

        self.loop = GLib.MainLoop()
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus)

        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            log("error", f"扬声器打不开（{self.device}）。aplay -l 看看，"
                         f"或者是不是被别的进程占着")
            return 1

        host, port = env("OME_HOST"), int(env("OME_WS_PORT"))
        app, key = env("OME_APP"), env("KEY_OPERATORMIC")
        self.rx = OmeReceiver(
            host, port, app, key,
            on_audio=self._on_audio,
            logger=lambda lv, m: log(lv, f"[ome] {m}"),
            # 抖动缓冲。对话链路，宁可短一点。
            latency_ms=self.jitter_ms,
            # **不给 audio_caps。** 让它把 OME 给什么就交上来什么，后面
            # audioconvert/audioresample 去适配声卡 —— 少一处能对不上的地方。
        )
        # 推流端没起来的时候 OME 会返 404，接收端会一直重试，所以**启动顺序
        # 无所谓**：这台机器可以先于操作者那台起来。
        self.rx.start()
        log("info", f"操作者语音: ws://{host}:{port}/{app}/{key} -> "
                    f"{self.device or 'fakesink（--fake）'}")

        GLib.timeout_add_seconds(REPORT_SEC, self._report)
        # GLib 的主循环挡着 Python 的信号处理，所以走 unix_signal_add，
        # 不然 tmux 关窗口/监视循环发的 TERM 要等到有其他事件才被处理。
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, self._quit)
        if seconds > 0:
            GLib.timeout_add_seconds(int(seconds), self._quit)

        try:
            self.loop.run()
        finally:
            if self.rx is not None:
                self.rx.stop()
            self.pipeline.set_state(Gst.State.NULL)
            log("info", f"终了 收到 {self.n_total} 包")
        return self.rc

    def _quit(self, *_a):
        self.loop.quit()
        return GLib.SOURCE_REMOVE


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="从 OME 收操作者语音，放到机体扬声器")
    p.add_argument("--device",
                   help="ALSA 放音设备，形如 hw:CARD=ATCSP1,DEV=0。"
                        "**别用 default** —— 插拔一个 USB 音频就会静默地换成别的。"
                        "正常由 start_gstreamer.sh 按 SPEAKER_NAME 解析后传进来")
    p.add_argument("--fake", action="store_true",
                   help="不碰声卡，收下来丢掉（只验证收流那一半）")
    p.add_argument("--publish", action="store_true",
                   help="同时往 ROS 发 operator_mic/audio（给 recorder.py 录）。"
                        "不加就完全不碰 ROS")
    p.add_argument("--seconds", type=float, default=0.0,
                   help="跑这么多秒就退出（手动确认用，0 = 一直跑）")
    a = p.parse_args(argv)

    if not a.fake and not a.device:
        raise SystemExit("[error] 要 --device 或 --fake")

    jitter = int(os.environ.get("SPEAKER_JITTER_MS", "100"))
    return Speaker(None if a.fake else a.device, jitter, a.publish).run(a.seconds)


if __name__ == "__main__":
    sys.exit(main())
