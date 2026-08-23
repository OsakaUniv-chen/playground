#!/usr/bin/env python3
"""三路相机：采集 → 编码 → SRT 推给 OME，顺带把编好的帧交给记录。

    v4l2src ─[jpegdec]─[裁/转]─ h264enc ─ h264parse ─ tee ─┬─ mpegtsmux ─ srtsink   (推流)
                                                            └─ appsink ─ ROS        (记录)
    fisheye 多一路音频：
    alsasrc ─ audioconvert ─ tee ─┬─ voaacenc ─ mux.                                (推流)
                                  └─ appsink ─ ROS                                  (记录)

**为什么不是一行 gst-launch。** 记录要的是采集时刻，而它只有持有管线的进程
算得出来（`running_time + base_time + offset`）；命令行既发不了 ROS 消息，
也交不出 PTS。**不加 `--publish` 的时候，这个脚本做的事和原来那行 gst-launch
一模一样** —— 没装 ROS 的机器上照样推流。

**记录的是编码之后的 H.264**（Annex-B、一帧一条、IDR 自带 SPS），推流和记录
共用同一份编码结果，不重复编。设备解析不在这里 —— 由 start_gstreamer.sh 每次
重起时重新解析好传进来（那样拔插换了 /dev/videoN 也能接回来）。

用法（正常由 start_gstreamer.sh 起）:
    python3 cam.py fisheye   --device /dev/video0 --mic hw:CARD=ATCSP1,DEV=0 --publish
    python3 cam.py realsense --device /dev/video6
    python3 cam.py navcam    --fake --seconds 10
"""
from __future__ import annotations

import argparse
import array
import os
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 多久报一行 [s]。状态面板拿这一行显示（和 soundmap.py / speaker.py 一样）。
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


def srt_uri(key: str) -> str:
    return (f"srt://{env('OME_HOST')}:{env('OME_SRT_PORT')}?mode=caller"
            f"&latency={env('SRT_LATENCY')}&streamid={env('OME_VHOST')}/{env('OME_APP')}/{key}")


def h264enc(bitrate: str, gop: str, hw: bool) -> str:
    """编码器。**目的是腾 CPU 不是提速** —— 这台机器要同时跑三路编码、
    声音图生成和 bag 写入，软解 MJPG + x264 会吃掉 1.5~2 个核。"""
    if hw:
        return (f"vaapipostproc ! video/x-raw,format=NV12 "
                f"! vaapih264enc rate-control=cbr bitrate={bitrate} keyframe-period={gop}")
    # **必须固定 profile=baseline。** 上游不是 I420 时 x264enc 会选 High 4:4:4，
    # 浏览器解不出来；而 OME 是 bypass 转发，SDP 里照样写着 baseline ——
    # 表现为协商成功但就是不出画面。
    return (f"videoconvert ! video/x-raw,format=I420 "
            f"! x264enc tune=zerolatency speed-preset=veryfast bitrate={bitrate} "
            f"key-int-max={gop} ! video/x-h264,profile=baseline")


class Cam:
    def __init__(self, name: str, device: str | None, mic: str | None,
                 fake: bool, publish: bool):
        self.name = name
        self.device = device
        self.mic = mic
        self.fake = fake
        self.publish = publish
        self.ros = None
        self.pipeline = None
        self.loop = None
        self.rc = 0

        # 统计 / 看门狗
        self.n_frame = 0            # 这 10 秒编了多少帧
        self.n_audio = 0
        self.n_sent = 0             # 这 10 秒往 srtsink 送了多少个包
        self.bytes_sent = 0
        self.last_sent = 0.0        # 最后一次有数据进 srtsink 的时刻（monotonic）
        self.stall_after = float(env("STALL_CHECK_SEC")) * float(env("STALL_MISSES"))

    # ---- 管线 ----

    def _src(self) -> str:
        """采集那一段（到编码之前）。"""
        n, fake = self.name, self.fake
        if n == "fisheye":
            w, h, fps = env("FISHEYE_SRC_WIDTH"), env("FISHEYE_SRC_HEIGHT"), env("FISHEYE_FPS")
            if fake:
                src = f"videotestsrc is-live=true pattern=ball ! video/x-raw,width={w},height={h},framerate={fps}/1"
            else:
                # 相机只出 MJPG，从设备取 1920x1080 后裁中央 1080x1080。
                src = (f"v4l2src device={self.device} ! image/jpeg,width={w},height={h},framerate={fps}/1 "
                       f"! {self.jpegdec}")
            src += f" ! videocrop left={env('FISHEYE_CROP_LEFT')} right={env('FISHEYE_CROP_RIGHT')}"
            if env("FISHEYE_ROTATE_180") == "1":
                src += " ! videoflip method=rotate-180"   # 相机是倒装的
            return src
        if n == "realsense":
            w, h, fps, fmt = (env("REALSENSE_WIDTH"), env("REALSENSE_HEIGHT"),
                              env("REALSENSE_FPS"), env("REALSENSE_FORMAT"))
            if fake:
                return f"videotestsrc is-live=true pattern=smpte ! video/x-raw,width={w},height={h},framerate={fps}/1"
            return (f"v4l2src device={self.device} "
                    f"! video/x-raw,format={fmt},width={w},height={h},framerate={fps}/1")
        w, h, fps, fmt = (env("NAVCAM_WIDTH"), env("NAVCAM_HEIGHT"),
                          env("NAVCAM_FPS"), env("NAVCAM_FORMAT"))
        if fake:
            return f"videotestsrc is-live=true pattern=snow ! video/x-raw,width={w},height={h},framerate={fps}/1"
        if fmt == "MJPG":
            return (f"v4l2src device={self.device} ! image/jpeg,width={w},height={h},framerate={fps}/1 "
                    f"! {self.jpegdec}")
        return (f"v4l2src device={self.device} "
                f"! video/x-raw,format={fmt},width={w},height={h},framerate={fps}/1")

    def desc(self) -> str:
        n = self.name
        fps = env(f"{n.upper()}_FPS")
        bitrate = env(f"{n.upper()}_BITRATE")
        key = env(f"KEY_{n.upper()}")

        # **capsfilter 放在 tee 之前。** tee 的两个分支拿到的必然是同一份 caps，
        # 分开写只会在协商上打架。byte-stream/au 是两边都要的：mpegtsmux 要它，
        # foxglove 的 CompressedVideo 也规定 H.264 用 Annex-B、一帧一条。
        video = (f"{self._src()} "
                 f"! {h264enc(bitrate, fps, self.hw)} "
                 f"! h264parse config-interval=-1 "
                 f"! video/x-h264,stream-format=byte-stream,alignment=au "
                 f"! tee name=vt "
                 f"vt. ! queue max-size-buffers=3 leaky=downstream ! mux. ")
        if self.publish:
            # 记录这一支给深一点的队列：写 bag 偶尔卡一下不该反压到推流上，
            # leaky 保证真堵住的时候丢的是记录而不是直播。
            video += ("vt. ! queue max-size-buffers=60 leaky=downstream "
                      "! appsink name=vsink emit-signals=true sync=false max-buffers=60 drop=true ")

        audio = ""
        if n == "fisheye":
            rate, ch = env("ONBOARD_MIC_RATE"), env("ONBOARD_MIC_CHANNELS")
            if self.fake or not self.mic:
                # **麦克风不能连累视频。** 卡不在就换静音源，视频照常送。
                src = "audiotestsrc is-live=true wave=silence"
            else:
                src = f"alsasrc device={self.mic}"
            audio = (f"{src} ! audioconvert ! audioresample "
                     f"! audio/x-raw,format=S16LE,rate={rate},channels={ch} "
                     f"! tee name=at "
                     f"at. ! queue max-size-buffers=10 leaky=downstream "
                     f"! voaacenc bitrate={env('ONBOARD_MIC_BITRATE')} ! aacparse "
                     f"! queue max-size-buffers=3 leaky=downstream ! mux. ")
            if self.publish:
                # **记录的是 AAC 之前的原始 S16LE**：0.77 Mbps，无损，
                # 事后重做算法用得上；AAC 那份只是给操作者听的。
                audio += ("at. ! queue max-size-buffers=200 leaky=downstream "
                          "! appsink name=asink emit-signals=true sync=false max-buffers=200 drop=true ")

        return (f"mpegtsmux name=mux alignment=7 "
                f"! srtsink name=sink uri=\"{srt_uri(key)}\" sync=false "
                f"{video}{audio}")

    # ---- ROS ----

    def _ros_setup(self):
        """记录那一半。**按需 import**，也**必须在 import gi 之前**（见 gst_ros.py）。"""
        from gst_ros import RosPub
        from teleop_msgs.msg import AudioChunk
        from foxglove_msgs.msg import CompressedVideo

        robot = os.environ.get("ROBOT_NAME")
        if not robot:
            raise SystemExit("[error] --publish 要 ROBOT_NAME（source config.env）")
        self.CompressedVideo, self.AudioChunk = CompressedVideo, AudioChunk
        self.ros = RosPub(f"cam_{self.name}", robot, log)
        self.pub_video = self.ros.publisher(f"{self.name}/video", CompressedVideo, 60)
        self.pub_audio = (self.ros.publisher("onboard_mic/audio", AudioChunk, 200)
                          if self.name == "fisheye" else None)

    def _on_video(self, sink):
        from gi.repository import Gst
        from gst_ros import unix_ns

        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            m = self.CompressedVideo()
            self.ros.stamp(m, unix_ns(self.pipeline, sample, buf))
            m.frame_id = self.name
            m.format = "h264"
            # array.array 而不是 bytes：rosidl 的 uint8[] setter 拿到 bytes 会
            # 逐字节检查，33 KB 一帧 x 30 fps 就是白扔一个核的一大半。
            m.data = array.array("B", info.data)
            self.pub_video.publish(m)
            self.n_frame += 1
        except Exception as e:
            log("error", f"发视频帧: {e}")     # 漏出去会把管线停掉
        finally:
            buf.unmap(info)
        return Gst.FlowReturn.OK

    def _on_audio(self, sink):
        from gi.repository import Gst
        from gst_ros import unix_ns

        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            ch, rate = int(env("ONBOARD_MIC_CHANNELS")), int(env("ONBOARD_MIC_RATE"))
            m = self.AudioChunk()
            self.ros.stamp(m, unix_ns(self.pipeline, sample, buf))
            m.header.frame_id = "onboard_mic"
            m.encoding = "S16LE"
            m.channels = ch
            m.sample_rate = rate
            m.samples = info.size // (2 * ch)
            m.data = array.array("B", info.data)
            self.pub_audio.publish(m)
            self.n_audio += 1
        except Exception as e:
            log("error", f"发音频块: {e}")
        finally:
            buf.unmap(info)
        return Gst.FlowReturn.OK

    # ---- 跑 ----

    def _on_bus(self, _bus, msg):
        from gi.repository import Gst
        if msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log("error", f"{self.name}: {err} | {dbg}")
            self.rc = 1
            self.loop.quit()
        elif msg.type == Gst.MessageType.WARNING:
            err, dbg = msg.parse_warning()
            log("warn", f"{self.name}: {err} | {dbg}")

    def _sent_probe(self, _pad, info):
        """srtsink 收到一个包就记一下。**看门狗盯的是这里**。

        放在复用之后（而不是源那一端）是因为：任一路断了 mpegtsmux 就不再出包，
        一个探针盖住整条链路 —— 源、编码、复用、以及 sink 堵住不走。
        源那一端只看得见自己那一路：机体麦克风单独卡死时视频照样在流，
        探针一切正常，而 OME 那边什么都没收到。
        """
        from gi.repository import Gst
        self.last_sent = time.monotonic()
        self.n_sent += 1
        # **mpegtsmux 推的是 buffer list，不是单个 buffer。** 只挂
        # PadProbeType.BUFFER 的话这个探针一次都不会被调 —— 表现是看门狗
        # 每 15 秒把一条好好的流杀掉一次（踩过，所以两种都要收）。
        # 而且**要先看 info 的类型**：对着 list 调 get_buffer() 每次都会
        # 刷一条 GStreamer-CRITICAL，一秒几百条。
        if info.type & Gst.PadProbeType.BUFFER:
            buf = info.get_buffer()
            if buf is not None:
                self.bytes_sent += buf.get_size()
        else:
            bl = info.get_buffer_list()
            if bl is not None:
                for i in range(bl.length()):
                    self.bytes_sent += bl.get(i).get_size()
        return Gst.PadProbeReturn.OK

    def _watchdog(self):
        """**"管线活着但没有数据" 只能这么发现。** USB 抽风、驱动卡死的时候
        进程不退出、也不报错，管线停在 PLAYING 上一个包都不动。
        这里发现之后留个记号再退出，外面的监视循环照常重起（重起时会重新
        解析设备，USB 重新枚举完能接回来）。"""
        if time.monotonic() - self.last_sent < self.stall_after:
            return True
        log("error", f"★ {self.name} 卡死：{self.stall_after:.0f}s 一个包都没出去。"
                     f"退出让监视循环重起（查 lsusb -t / dmesg）")
        try:
            open(os.path.join(env("LOG_DIR"), f"{self.name}.stalled"), "w").close()
        except OSError:
            pass
        self.rc = 1
        self.loop.quit()
        return False

    def _report(self):
        parts = [f"{self.bytes_sent / 1e6 * 8 / REPORT_SEC:.1f} Mbps 出去"]
        if self.ros is not None:
            parts.append(f"记录 {self.n_frame} 帧"
                         + (f" + 音频 {self.n_audio} 块" if self.n_audio else ""))
        log("info", "[10s] " + " / ".join(parts))
        self.n_frame = self.n_audio = self.n_sent = 0
        self.bytes_sent = 0
        return True

    def run(self, seconds: float = 0.0) -> int:
        # ROS 节点要建在 import gi 之前，见 gst_ros.py 里那条。
        if self.publish:
            self._ros_setup()

        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import GLib, Gst

        Gst.init(None)
        self.hw = (env("USE_HW_CODEC") == "1"
                   and Gst.ElementFactory.find("vaapih264enc") is not None)
        self.jpegdec = ("vaapijpegdec" if self.hw and Gst.ElementFactory.find("vaapijpegdec")
                        else "jpegdec")

        desc = self.desc()
        log("info", f"[{self.name}] {desc}")
        self.pipeline = Gst.parse_launch(desc)
        if self.publish:
            self.pipeline.get_by_name("vsink").connect("new-sample", self._on_video)
            a = self.pipeline.get_by_name("asink")
            if a is not None:
                a.connect("new-sample", self._on_audio)

        self.pipeline.get_by_name("sink").get_static_pad("sink").add_probe(
            Gst.PadProbeType.BUFFER | Gst.PadProbeType.BUFFER_LIST, self._sent_probe)

        self.loop = GLib.MainLoop()
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus)
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            log("error", f"{self.name} 起不来（设备 {self.device or 'fake'}）")
            return 1

        self.last_sent = time.monotonic()      # 从 PLAYING 开始算
        GLib.timeout_add_seconds(REPORT_SEC, self._report)
        GLib.timeout_add(int(float(env("STALL_CHECK_SEC")) * 1000), self._watchdog)
        # GLib 的主循环挡着 Python 的信号处理，走 unix_signal_add 才收得到
        # 监视循环 / tmux 发的 TERM。
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, self._quit)
        if seconds > 0:
            GLib.timeout_add_seconds(int(seconds), self._quit)

        try:
            self.loop.run()
        finally:
            self.pipeline.set_state(Gst.State.NULL)
            if self.ros is not None:
                self.ros.shutdown()
        return self.rc

    def _quit(self, *_a):
        from gi.repository import GLib
        self.loop.quit()
        return GLib.SOURCE_REMOVE


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="一路相机：推 OME + （可选）交给记录")
    p.add_argument("name", choices=["fisheye", "realsense", "navcam"])
    p.add_argument("--device", help="/dev/videoN。由 start_gstreamer.sh 解析好传进来")
    p.add_argument("--mic", help="fisheye 专用：机体麦克风的 ALSA 设备。不给就用静音源")
    p.add_argument("--fake", action="store_true", help="不碰设备，用测试源验链路")
    p.add_argument("--publish", action="store_true",
                   help="同时把编好的帧发给 recorder。不加就完全不碰 ROS")
    p.add_argument("--seconds", type=float, default=0.0, help="跑这么多秒就退（手动确认用）")
    a = p.parse_args(argv)
    if not a.fake and not a.device:
        raise SystemExit("[error] 要 --device 或 --fake")
    return Cam(a.name, a.device, a.mic, a.fake, a.publish).run(a.seconds)


if __name__ == "__main__":
    sys.exit(main())
