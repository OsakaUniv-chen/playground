#!/usr/bin/env python3
"""操作者マイク -> OME(RTMP)。ブラウザを経由せず PC-C 上でマイクを直接取る。

    PC-C -> OME -> PC-B（スピーカー）と PC-D（推論）   設計 §2 §4.1

OME へは FLV に載る AAC で入れ、OME が WebRTC 用に Opus へ変換して配る。

PTT: <robot>/operator/ptt (Bool) を購読する。常時オンにすると操作者側の
雑音と独り言がそのまま機体から出る。

**valve ではなく volume で消す。** valve で下流を止めると RTMP が無音の
まま途切れ、OME 側のストリームが落ちて受信側の WebRTC セッションごと
切れる。復帰に数秒かかるので PTT には使えない。音を出さないだけにして、
ストリーム自体は流し続ける。
"""

import os

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

import rclpy  # noqa: E402
from rclpy.executors import ExternalShutdownException  # noqa: E402
from rclpy.node import Node  # noqa: E402
from std_msgs.msg import Bool  # noqa: E402


def env(k, d=""):
    return os.environ.get(k, d)


class OperatorMicSender(Node):
    def __init__(self):
        super().__init__("operator_mic_sender")
        Gst.init(None)
        ns = "/" + env("ROBOT_NAME", "robot")
        fake = env("USE_FAKE_SOURCES", "1") == "1"

        src = (
            "audiotestsrc is-live=true wave=sine freq=440"
            if fake
            else f"alsasrc device={env('OPERATOR_MIC_DEVICE', 'default')} buffer-time=200000"
        )
        head = (
            f"{src} ! audioconvert ! audioresample "
            f"! audio/x-raw,format=S16LE,rate=48000,channels=1 "
        )

        rtmp = (
            f"rtmp://{env('PC_C_IP', '127.0.0.1')}:{env('OME_RTMP_PORT', '1935')}"
            f"/{env('OME_APP', 'app')}/"
            f"{env('STREAM_KEY_OPERATOR_MIC', 'operatormic')} live=true"
        )
        desc = (
            f"{head}! volume name=ptt mute=true "
            f"! voaacenc bitrate=32000 ! aacparse "
            f"! flvmux streamable=true ! rtmpsink location=\"{rtmp}\""
        )

        self.get_logger().info(f"pipeline: {desc}")
        self.pipeline = Gst.parse_launch(desc)
        self.ptt = self.pipeline.get_by_name("ptt")
        self.pipeline.set_state(Gst.State.PLAYING)

        self.sub = self.create_subscription(Bool, f"{ns}/operator/ptt", self.on_ptt, 10)
        self.get_logger().info(f"操作者マイク -> {rtmp.split(' ')[0]}  (PTT 待ち)")

    def on_ptt(self, msg: Bool):
        self.ptt.set_property("mute", not msg.data)
        self.get_logger().info(f"PTT {'ON — 送話中' if msg.data else 'OFF'}")

    def destroy_node(self):
        self.pipeline.set_state(Gst.State.NULL)
        super().destroy_node()


def main():
    rclpy.init()
    node = OperatorMicSender()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
