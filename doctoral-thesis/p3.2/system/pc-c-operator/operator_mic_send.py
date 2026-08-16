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


def env(k, d=None):
    """config.env が持つ項目には既定値を渡さない（二重定義にすると片方が古くなる）。"""
    if d is None:
        try:
            return os.environ[k]
        except KeyError:
            raise RuntimeError(
                f"{k} が未設定。env.sh を読まずに起動している"
                f"（common/config.env か pc-c-operator/config.env が設定する）"
            ) from None
    return os.environ.get(k, d)


class OperatorMicSender(Node):
    def __init__(self):
        super().__init__("operator_mic_sender")
        Gst.init(None)
        ns = "/" + env("ROBOT_NAME")           # 既定値は置かない（env.sh 必須）
        fake = env("USE_FAKE_SOURCES") == "1"

        src = (
            "audiotestsrc is-live=true wave=sine freq=440"
            if fake
            else f"alsasrc device={env('OPERATOR_MIC_DEVICE')} buffer-time=200000"
        )
        # 送出レートは OPERATOR_MIC_RATE（PC-B が記録するレート）ではない。
        # OME が WebRTC 用に Opus へ変換する都合で 48 kHz を入れる。
        head = (
            f"{src} ! audioconvert ! audioresample "
            f"! audio/x-raw,format=S16LE,rate={env('OPERATOR_MIC_SEND_RATE')},"
            f"channels={env('OPERATOR_MIC_CHANNELS')} "
        )

        rtmp = (
            f"rtmp://{env('PC_C_IP')}:{env('OME_RTMP_PORT')}"
            f"/{env('OME_APP')}/"
            f"{env('STREAM_KEY_OPERATOR_MIC')} live=true"
        )
        # sync=false。live 送出なので実時間で届く。既定の sync=true だと
        # rtmpsink がパイプライン latency ぶん抱え込んでから出すので、
        # 操作者の声が機体のスピーカーに出るまでが延びるだけで得が無い。
        desc = (
            f"{head}! voaacenc bitrate=32000 ! aacparse "
            f"! flvmux streamable=true ! rtmpsink sync=false location=\"{rtmp}\""
        )

        self.get_logger().info(f"pipeline: {desc}")
        self.pipeline = Gst.parse_launch(desc)
        self.pipeline.set_state(Gst.State.PLAYING)
        self.get_logger().info(f"操作者マイク -> {rtmp.split(' ')[0]}")

        # **bus を見ないと失敗が黙る。** set_state(PLAYING) はパイプラインが
        # 実際に流れる前に返るので、OME がまだ立っていない・stream key が
        # 違う・マイクが開けない、はすべて後から bus のエラーとして来る。
        # 拾わないと「操作者が喋っているのに機体から声が出ないが、ログは
        # 正常」という一番手間のかかる形になる。PC-B 側は
        # bridge/gst_ros_common.py の GstBridgeNode が同じことをしている
        # （こちらはその基底を使っていないので個別に持つ）。
        self.bus = self.pipeline.get_bus()
        self.create_timer(0.5, self._poll_bus)

    _BUS_FILTER = (Gst.MessageType.ERROR | Gst.MessageType.EOS
                   | Gst.MessageType.WARNING)

    def _poll_bus(self):
        msg = self.bus.timed_pop_filtered(0, self._BUS_FILTER)
        while msg is not None:
            if msg.type == Gst.MessageType.ERROR:
                err, dbg = msg.parse_error()
                self.get_logger().error(f"gst error: {err} | {dbg}")
            elif msg.type == Gst.MessageType.WARNING:
                err, dbg = msg.parse_warning()
                self.get_logger().warn(f"gst warning: {err} | {dbg}")
            elif msg.type == Gst.MessageType.EOS:
                self.get_logger().error("gst EOS: マイクの経路が終了した")
            msg = self.bus.timed_pop_filtered(0, self._BUS_FILTER)

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
