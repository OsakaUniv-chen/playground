#!/usr/bin/env python3
"""操作者マイク（PC-C から OME 経由）-> 機体スピーカー と ROS 記録。

    OmeReceiver ─ on_audio ─┬─ ROS publish        (記録)
                            └─ appsrc ─ alsasink  (スピーカー)

OME を通すので PC-D も同じ音声を取れる。

タイムスタンプは **PC-B 到着時刻**。PC-C で採られた音なので採取時刻は
こちらでは分からない。基準時計は PC-B だけなので、
PC-C と時刻を合わせる必要は無い。

`gstreamer1.0-nice` が要る（無いと WebRTC の answer が黙って失敗する）。
"""

import os
import sys
import time

from audio_common_msgs.msg import AudioDataStamped

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from gst_ros_common import (GstBridgeNode, env, env_bool, env_int, robot_ns,
                            run, set_audio_data)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "common"))


class OperatorMicBridge(GstBridgeNode):
    def __init__(self):
        self.ns = robot_ns()
        self.channels = env_int("OPERATOR_MIC_CHANNELS")
        self.rate = env_int("OPERATOR_MIC_RATE")
        self.caps = (f"audio/x-raw,format=S16LE,rate={self.rate},"
                     f"channels={self.channels}")
        self.spk = None
        self.rx = None
        self._spk_ready = False
        super().__init__("operator_mic_bridge")

        self.pub = self.create_publisher(
            AudioDataStamped, f"{self.ns}/operator_mic/audio", 100
        )

        self.n = 0
        self.create_timer(10.0, self._report)

    # ---- 経路ごとのパイプライン ----

    def _ome_log(self, level, msg):
        """OmeReceiver のログを ROS のログに載せる。

        **重要度ごとに別の行から呼ぶこと。** rclpy のロガーは呼び出し元を
        (ファイル, 行, 関数) で識別して設定を覚えるので、1 行の中で
        `info` と `warn` を切り替えると 2 回目に
        `ValueError: Logger severity cannot be changed between calls.` で落ちる。
        送出がまだ無いとき OME は 404 を返し、受信側はそれを warn で
        報告して繋ぎ直す ── ここが落ちると**起動順を問わないという
        前提そのものが崩れる**（実際に launch で踏んだ）。
        """
        text = f"[ome] {msg}"
        if level == "error":
            self.get_logger().error(text)
        elif level == "warn":
            self.get_logger().warn(text)
        else:
            self.get_logger().info(text)

    def build_pipeline(self) -> str:
        fake = env_bool("USE_FAKE_SOURCES")
        sink = (
            "fakesink sync=false"
            if fake
            else f"alsasink device={env('SPEAKER_DEVICE')} sync=false"
        )

        # 音は OmeReceiver 側から appsrc に押し込む。ここはスピーカーへ
        # 出すだけの短いパイプライン。記録は on_audio から直接 publish
        # するので appsink は要らない。
        # caps はここでは決めない。最初のバッファが来たときに、実際に
        # 届いた caps をそのまま appsrc に載せる（_on_ome_audio）。
        # 決め打ちにすると OME 側の都合で変わったときに not-negotiated で
        # スピーカーだけ黙って死ぬ。
        return (
            f"appsrc name=spk is-live=true format=time do-timestamp=true "
            f"block=false max-bytes=2000000 "
            f"! queue max-size-time=200000000 leaky=downstream "
            f"! audioconvert ! audioresample ! {sink}"
        )

    def appsink_names(self) -> list:
        return []          # 記録は _on_ome_audio から直接 publish する

    def start_pipeline(self):
        super().start_pipeline()
        from ome_receiver import OmeReceiver

        self.spk = self.pipeline.get_by_name("spk")
        # 受信側なので OME_HOST を見る（config.env で既定は PC_C_IP）。
        host = env("OME_HOST")
        self.rx = OmeReceiver(
            host, env_int("OME_WS_PORT"), env("OME_APP"),
            env("STREAM_KEY_OPERATOR_MIC"),
            on_audio=self._on_ome_audio,
            # 記録するレートに揃えてから受ける（OME から出るのは 48 kHz）
            audio_caps=self.caps,
            logger=self._ome_log,
        )
        self.rx.start()
        self.get_logger().info(
            f"操作者マイクを OME から受ける: "
            f"ws://{host}:{env('OME_WS_PORT')}/"
            f"{env('OME_APP')}/{env('STREAM_KEY_OPERATOR_MIC')}"
        )

    # ---- 受け取り ----

    def _on_ome_audio(self, data, sample):
        """OmeReceiver のストリーミングスレッドから呼ばれる。

        到着時刻をここで打つ。gst の PTS 換算（sample_to_unix_ns）は使わない
        — 別パイプラインの時計なので base_time が噛み合わない。この音の
        `header.stamp` はもともと「PC-B 到着時刻」なので、これで正しい。
        """
        self._publish(data, time.clock_gettime_ns(time.CLOCK_REALTIME))
        if self.spk is None:
            return
        if not self._spk_ready:
            caps = sample.get_caps()
            self.spk.set_property("caps", caps)
            self._spk_ready = True
            self.get_logger().info(f"スピーカーへ: {caps.to_string()}")
        buf = Gst.Buffer.new_wrapped(data)
        if self.spk.emit("push-buffer", buf) != Gst.FlowReturn.OK:
            self.get_logger().warn("スピーカーへの push に失敗",
                                   throttle_duration_sec=10.0)

    def _publish(self, data, unix_ns):
        msg = AudioDataStamped()
        msg.header.stamp = self.to_ros_time(unix_ns)  # = PC-B 到着時刻
        msg.header.frame_id = "operator_mic"
        set_audio_data(msg, data)
        self.pub.publish(msg)
        self.n += 1

    def _report(self):
        self.get_logger().info(f"operator_mic {self.n} msg (10s)")
        self.n = 0

    def destroy_node(self):
        if self.rx is not None:
            self.rx.stop()
        super().destroy_node()


if __name__ == "__main__":
    run(OperatorMicBridge)
