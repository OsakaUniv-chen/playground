#!/usr/bin/env python3
"""操作者マイク -> OME(RTMP)。ブラウザを経由せず PC-C 上でマイクを直接取る。

    PC-C -> OME -> PC-B（スピーカー）と PC-D（推論）

OME へは FLV に載る AAC で入れ、OME が WebRTC 用に Opus へ変換して配る。

**マイクは常時オン。** 送話の切り替え（PTT）は持たないので、操作者側の
物音や独り言もそのまま機体のスピーカーから出る。
"""

import os

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

import rclpy  # noqa: E402
from rclpy.executors import ExternalShutdownException  # noqa: E402
from rclpy.node import Node  # noqa: E402


def env(k, d=""):
    return os.environ.get(k, d)


class OperatorMicSender(Node):
    def __init__(self):
        super().__init__("operator_mic_sender")
        Gst.init(None)
        ns = "/" + os.environ["ROBOT_NAME"]   # 既定値は置かない（env.sh 必須）
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
            f"{head}! voaacenc bitrate=32000 ! aacparse "
            f"! flvmux streamable=true ! rtmpsink location=\"{rtmp}\""
        )

        self.get_logger().info(f"pipeline: {desc}")
        self.pipeline = Gst.parse_launch(desc)
        self.pipeline.set_state(Gst.State.PLAYING)
        self.get_logger().info(f"操作者マイク -> {rtmp.split(' ')[0]}")

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
