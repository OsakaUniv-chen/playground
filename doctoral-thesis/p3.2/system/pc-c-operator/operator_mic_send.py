#!/usr/bin/env python3
"""操作者マイク -> OME(RTMP)。ブラウザを経由せず PC-C 上でマイクを直接取る。

    PC-C -> OME -> PC-B（スピーカー）と PC-D（推論）

OME へは FLV に載る AAC で入れ、OME が WebRTC 用に Opus へ変換して配る。

**マイクは常時オン。** 送話の切り替え（PTT）は持たないので、操作者側の
物音や独り言もそのまま機体のスピーカーから出る。

**このプロセスは ROS を使わない。GStreamer だけ。rclpy を戻さないこと。**

以前は rclpy.spin() を待ちループ代わりに、ROS logger を出力先に使って
いたが、topic は 1 本も出し入れしていないので ROS 側の実体は無かった。
その版は PC-C で**何も出力しないまま SIGSEGV / SIGABRT で落ちる**ことが
あった（run.sh 経由だと mic だけが「死んでいる」になり、ログは 0 バイト。
他の ROS ノードが居るときに落ちやすいが、単独でも落ちた回があり、
再現は不安定）。rclpy を外した版は同じ 4 条件で 4/4 生存。

**原因は特定できていない。** gi と rclpy の同居そのものではない ──
PC-B のブリッジ（bridge/gst_ros_common.py）は同じ組み合わせ・同じ初期化
順序で、この機械で 9/9 落ちなかった。分かっているのは「使っていない
rclpy を外したら直った」ところまで。系統としては README.md §3 の
libunwind の件に近いが、そう言い切れる証拠は取れていない。

待ちループは GLib.MainLoop（GStreamer 本来のやり方）、出力は print。
"""

import os
import signal
import sys

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402


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


def log(level, text):
    """ROS logger と同じ見た目にしておく（log/mic.log を並べて読むため）。"""
    print(f"[{level}] [operator_mic_send]: {text}", flush=True)


class OperatorMicSender:
    _BUS_FILTER = (Gst.MessageType.ERROR | Gst.MessageType.EOS
                   | Gst.MessageType.WARNING)

    def __init__(self):
        Gst.init(None)
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

        log("INFO", f"pipeline: {desc}")
        self.pipeline = Gst.parse_launch(desc)
        self.pipeline.set_state(Gst.State.PLAYING)
        log("INFO", f"操作者マイク -> {rtmp.split(' ')[0]}"
                    f"{'（フェイク音源）' if fake else ''}")

        # **bus を見ないと失敗が黙る。** set_state(PLAYING) はパイプラインが
        # 実際に流れる前に返るので、OME がまだ立っていない・stream key が
        # 違う・マイクが開けない、はすべて後から bus のエラーとして来る。
        # 拾わないと「操作者が喋っているのに機体から声が出ないが、ログは
        # 正常」という一番手間のかかる形になる（PC-B 側は
        # bridge/gst_ros_common.py の GstBridgeNode が同じことをしている）。
        self.bus = self.pipeline.get_bus()
        GLib.timeout_add(500, self._poll_bus)

    def _poll_bus(self):
        msg = self.bus.timed_pop_filtered(0, self._BUS_FILTER)
        while msg is not None:
            if msg.type == Gst.MessageType.ERROR:
                err, dbg = msg.parse_error()
                log("ERROR", f"gst error: {err} | {dbg}")
            elif msg.type == Gst.MessageType.WARNING:
                err, dbg = msg.parse_warning()
                log("WARN", f"gst warning: {err} | {dbg}")
            elif msg.type == Gst.MessageType.EOS:
                log("ERROR", "gst EOS: マイクの経路が終了した")
            msg = self.bus.timed_pop_filtered(0, self._BUS_FILTER)
        return True                      # False を返すとタイマが外れる

    def close(self):
        self.pipeline.set_state(Gst.State.NULL)


def main():
    sender = OperatorMicSender()
    loop = GLib.MainLoop()

    # run.sh stop は SIGTERM を送る。捕まえないと MainLoop が回り続ける。
    def _bye(signum, _frame):
        log("INFO", f"signal {signum} を受けた。終了する")
        sender.close()
        loop.quit()

    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)

    try:
        loop.run()
    finally:
        sender.close()
        sys.stdout.flush()


if __name__ == "__main__":
    main()
