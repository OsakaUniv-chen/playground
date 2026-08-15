#!/usr/bin/env python3
"""16ch マイクアレイ -> 1-bit 音響マップ -> OME と ROS 記録。

    alsasrc(16ch) ─ appsink ─┬─ 生データを ROS へ（記録用）
                             └─ 1-bit 生成 ─┬─ Float32MultiArray を ROS へ（記録用）
                                            └─ appsrc ─ H.264 ─ RTMP ─ OME

**16ch の生データは機体から出さない。** 音響マップだけを OME へ送り、PC-D は
映像と同じ経路で受け取る。生データは機体内に残す — マップは派生物なので、
生データが無いと後から別の手法で作り直せない。

生成器は ../soundmap/ に取り込んである（外のディレクトリを参照しない。
PC-B のフォルダごと配ればそのまま動く）。CPU のみで動き、N100 の実測で
25.5 ms/map・1 コア・最大 27 Hz。既定の fs=44100 / channels=16 は
こちらの UMA16v2 と一致する。
"""

import array
import os
import sys

import gi
import numpy as np

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402
from audio_common_msgs.msg import AudioDataStamped, AudioInfo
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import Float32MultiArray, MultiArrayDimension

from gst_ros_common import (GstBridgeNode, env, env_bool, env_int, robot_ns,
                            run, set_audio_data)

# 生成器は ../soundmap/ に同梱。自分の位置からの相対で引くので、
# どこから起動しても、どの機械に配っても同じように読める。
_GEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "soundmap")
if _GEN_DIR not in sys.path:
    sys.path.insert(0, _GEN_DIR)

try:
    import cv2 as _CV2
except ImportError:      # 色を付けられないだけ。灰色で送る
    _CV2 = None

try:
    from onebit_soundmap import OneBitSoundMapAPI

    HAVE_GENERATOR = True
except ImportError as e:
    HAVE_GENERATOR = False
    _IMPORT_ERROR = e


class SoundMapBridge(GstBridgeNode):
    def __init__(self):
        self.ns = robot_ns()
        self.channels = env_int("MIC_ARRAY_CHANNELS", 16)
        self.rate = env_int("MIC_ARRAY_RATE", 44100)
        self.ms_per_msg = env_int("MIC_ARRAY_MS_PER_MSG", 10)
        # 生成周期と積分窓は別物。窓は周期より長く取り、毎回ずらして使う。
        # 1-bit 相関器は積分時間で S/N と空間分解能を稼ぐ一方、計算量も
        # 窓の長さにほぼ比例する。
        # word-wolf の実測条件: 160 msg × 128 sample/ch @44.1 kHz = 464 ms。
        self.map_hz = env_int("SOUNDMAP_HZ", 10)
        self.window_ms = env_int("SOUNDMAP_WINDOW_MS", 464)
        super().__init__("soundmap_bridge")

        # --- 生データ（記録のみ。外へは出さない）---
        self.pub_audio = self.create_publisher(
            AudioDataStamped, f"{self.ns}/mic_array/audio", 100
        )
        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.pub_info = self.create_publisher(
            AudioInfo, f"{self.ns}/mic_array/info", latched
        )
        info = AudioInfo()
        info.channels = self.channels
        info.sample_rate = self.rate
        info.sample_format = env("MIC_ARRAY_FORMAT", "S16LE")
        info.bitrate = self.rate * self.channels * 16
        info.coding_format = "WAVE"
        self.pub_info.publish(info)

        # --- 音響マップ（生成値。OME へ送る画像はここから作る）---
        self.pub_map = self.create_publisher(
            Float32MultiArray, f"{self.ns}/soundmap/raw", 10
        )

        if HAVE_GENERATOR:
            self.gen = OneBitSoundMapAPI()
            self.get_logger().info(
                f"1-bit 音響マップ: {self.channels}ch {self.rate}Hz / "
                f"{self.map_hz} Hz 生成・窓 {self.window_ms} ms"
            )
        else:
            self.gen = None
            self.get_logger().error(
                f"生成器を読めない（{_GEN_DIR}）: {_IMPORT_ERROR}。"
                "生データの記録だけ続ける"
            )

        # 生成器は「生の int16/16ch バイト列の列」を受け取る。appsink から
        # 来たものをそのまま溜めて渡す（numpy への変換は生成器側で行う）。
        # 窓（生成に渡す長さ）と歩幅（何バイトごとに生成するか）
        self.window_bytes = self.rate * self.window_ms // 1000 * self.channels * 2
        self.stride_bytes = (self.rate // self.map_hz) * self.channels * 2
        self.chunks = []          # 直近 window_bytes ぶんを保持する
        self.buffered = 0
        self.since_last = 0
        self._caps_set = False

        self.send_pipeline = None
        self.mapsrc = None
        if self.gen is not None:
            self._build_send_pipeline()

        self.n_audio = 0
        self.n_map = 0
        self.map_ms_total = 0.0
        self.create_timer(10.0, self._report)

    def _build_send_pipeline(self):
        """音響マップを画像として OME へ送る。

        生成値（64×64 float）は ROS 側に記録するので、こちらは表示・推論用。

        **生成された解像度のまま送る。** 情報量は 64×64 しかないので、
        送出側で引き伸ばしても増えない。拡大は受け側（ブラウザの CSS、
        PC-D の前処理）でやればよく、そのほうが符号化する画素数が
        285 分の 1 で済む。
        SOUNDMAP_SEND_WIDTH に 0 以外を入れると、その大きさに拡大して送る。
        """
        w = env_int("SOUNDMAP_SEND_WIDTH", 0)
        hz = self.map_hz
        rtmp = (
            f"rtmp://{env('PC_C_IP', '127.0.0.1')}:{env('OME_RTMP_PORT', '1935')}"
            f"/{env('OME_APP', 'app')}/{env('STREAM_KEY_SOUNDMAP', 'soundmap')} live=true"
        )
        # appsrc は block=false。RTMP 先が居ないときに push-buffer が
        # 止まると、appsink のコールバック（生データの記録も含む）ごと
        # 巻き込まれる。queue も leaky にして、送出が詰まっても
        # 生成と記録は走り続けるようにする。
        scale = (
            f"! videoscale method=nearest-neighbour ! video/x-raw,width={w},height={w} "
            if w else ""
        )
        desc = (
            f"appsrc name=mapsrc is-live=true do-timestamp=true format=time "
            f"block=false max-bytes=4000000 "
            f"! queue max-size-buffers=5 leaky=downstream "
            f"! videoconvert {scale}"
            f"! x264enc tune=zerolatency speed-preset=ultrafast "
            f"bitrate={env_int('SOUNDMAP_BITRATE', 2000)} key-int-max={hz} "
            # appsrc から来るのは BGR / GRAY8 なので、放っておくと x264enc が
            # High 4:4:4 (Y444) を選ぶ。ブラウザはこれを復号できず、OME は
            # bypass なので SDP には baseline (42e01f) と書いたまま配る。
            # 結果、操作画面のマップだけが黙って映らなくなる。
            f"! video/x-h264,profile=baseline "
            f"! h264parse config-interval=-1 "
            f"! flvmux streamable=true ! rtmpsink location=\"{rtmp}\""
        )
        self.get_logger().info(f"送出: {desc}")
        self.send_pipeline = Gst.parse_launch(desc)
        self.mapsrc = self.send_pipeline.get_by_name("mapsrc")
        self.send_pipeline.set_state(Gst.State.PLAYING)

    def _push_to_ome(self, smap):
        """float のマップに疑似カラーを付けて appsrc へ流す。

        灰色のまま操作画面に重ねると沈んで見えないので、送出側で色を付ける。
        既存の可視化（generator-compare）と同じ inferno 系にしてある。
        PC-D 側も同じ色で受け取るので、VLM に食わせる画も揃う。
        """
        if self.mapsrc is None:
            return
        lo, hi = float(smap.min()), float(smap.max())
        span = hi - lo if hi > lo else 1.0
        gray = ((smap - lo) / span * 255.0).clip(0, 255).astype(np.uint8)
        if _CV2 is not None:
            img = _CV2.applyColorMap(gray, _CV2.COLORMAP_INFERNO)   # BGR
            fmt = "BGR"
        else:
            img = gray
            fmt = "GRAY8"
        h, w = img.shape[:2]
        if self._caps_set is False:
            caps = Gst.Caps.from_string(
                f"video/x-raw,format={fmt},width={w},height={h},"
                f"framerate={self.map_hz}/1"
            )
            self.mapsrc.set_property("caps", caps)
            self._caps_set = True
        buf = Gst.Buffer.new_wrapped(img.tobytes())
        ret = self.mapsrc.emit("push-buffer", buf)
        if ret != Gst.FlowReturn.OK:
            self.get_logger().warn(
                f"OME への push が通らない: {ret}", throttle_duration_sec=10.0
            )

    def build_pipeline(self) -> str:
        fake = env_bool("USE_FAKE_SOURCES", True)
        latency_us = self.ms_per_msg * 1000
        spb = self.rate * self.ms_per_msg // 1000

        if fake:
            src = f"audiotestsrc is-live=true wave=ticks samplesperbuffer={spb} ! audioconvert"
        else:
            src = (
                f"alsasrc device={env('MIC_ARRAY_DEVICE', 'hw:CARD=UMA16v2,DEV=0')} "
                f"buffer-time=200000 latency-time={latency_us}"
            )
        hw_caps = (
            f"audio/x-raw,format={env('MIC_ARRAY_HW_FORMAT', 'S32LE')},"
            f"rate={self.rate},channels={self.channels}"
        )
        caps = (
            f"audio/x-raw,format={env('MIC_ARRAY_FORMAT', 'S16LE')},"
            f"rate={self.rate},channels={self.channels}"
        )
        # 送出用の tee は無い。16ch はここから外に出ない。
        return f"{src} ! {hw_caps} ! audioconvert ! {caps} ! appsink name=rec"

    def on_sample(self, name, data, unix_ns, sample):
        # 生データはそのまま記録へ
        msg = AudioDataStamped()
        msg.header.stamp = self.to_ros_time(unix_ns)
        msg.header.frame_id = "mic_array"
        set_audio_data(msg, data)
        self.pub_audio.publish(msg)
        self.n_audio += 1

        if self.gen is None:
            return

        # 窓は捨てずに持ち越す。古いものから落として window_bytes を保つ。
        self.chunks.append(data)
        self.buffered += len(data)
        self.since_last += len(data)
        while self.buffered - len(self.chunks[0]) >= self.window_bytes:
            self.buffered -= len(self.chunks.pop(0))

        if self.since_last >= self.stride_bytes and self.buffered >= self.window_bytes:
            self.since_last = 0
            self._generate(list(self.chunks), unix_ns)

    def _generate(self, chunks, unix_ns):
        import time

        t0 = time.monotonic()
        try:
            smap = self.gen.generate(chunks)
        except Exception as e:
            self.get_logger().error(f"生成失敗: {e}", throttle_duration_sec=5.0)
            return
        self.map_ms_total += (time.monotonic() - t0) * 1000
        self.n_map += 1

        smap = np.asarray(smap, dtype=np.float32)
        # header を持たない型なので、時刻は soundmap/stamp に別途出す。
        # bag の log_time でも追えるが、生成に使った音声の時刻はこちら。
        msg = Float32MultiArray()
        msg.layout.dim = [
            MultiArrayDimension(label="rows", size=smap.shape[0],
                                stride=smap.size),
            MultiArrayDimension(label="cols", size=smap.shape[1],
                                stride=smap.shape[1]),
        ]
        msg.data = array.array("f", smap.ravel())
        self.pub_map.publish(msg)

        self._push_to_ome(smap)

    def destroy_node(self):
        if self.send_pipeline is not None:
            self.send_pipeline.set_state(Gst.State.NULL)
        super().destroy_node()

    def _report(self):
        avg = self.map_ms_total / self.n_map if self.n_map else 0.0
        self.get_logger().info(
            f"生データ {self.n_audio} msg / マップ {self.n_map} 枚 "
            f"(生成 {avg:.1f} ms/枚, 10s)"
        )
        self.n_audio = self.n_map = 0
        self.map_ms_total = 0.0


if __name__ == "__main__":
    run(SoundMapBridge)
