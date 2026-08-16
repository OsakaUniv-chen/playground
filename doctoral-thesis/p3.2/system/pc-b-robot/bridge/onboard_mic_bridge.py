#!/usr/bin/env python3
"""機体マイク -> OME(RTMP) と ROS 記録。

    alsasrc ─ tee ─ queue(leaky) ─ aac ─ flvmux ─ rtmpsink  (OME: <robot>mic)
                 └ queue ────────────────────── appsink rec (記録)

**cam_bridge とは別プロセスにしてある。** 送出は元々別ストリーム
（`<robot>stream` と `<robot>mic`）で、PC-D が音声だけを取り出せるように
してあったが、同じ `Gst.Pipeline` に同居させていたので次の 2 つを
巻き込んでいた:

  1. **状態遷移が一体。** `ONBOARD_MIC_DEVICE` は現地で確認する項目で、
     開けなければ `set_state(PLAYING)` が失敗する。同居していると
     **カメラまで道連れ**になり、遠隔操作の生命線が落ちる。
     マイクは無くても操作は続けられるので、切り離しておく。
  2. **latency がパイプライン全体で 1 つ。** gst は全分岐の最大値を取って
     全 sink に配る。`alsasrc buffer-time=200000` は「報告する最大 latency
     が 200 ms」という意味なので、同居していると映像側にも乗り得た。

同一ストリームなら PeerConnection 内で自動的に唇同期が掛かるが、それは
元々捨ててある（別 muxer・別ストリーム）。精密な対応付けは PC-B の bag で
時刻が揃うのでそちらで行う。
"""

from audio_common_msgs.msg import AudioDataStamped

import gi

gi.require_version("Gst", "1.0")

from gst_ros_common import (GstBridgeNode, env, env_bool, env_int, robot_ns,  # noqa: E402
                            run, set_audio_data)


class OnboardMicBridge(GstBridgeNode):
    def __init__(self):
        self.ns = robot_ns()
        super().__init__("onboard_mic_bridge")

        self.pub = self.create_publisher(
            AudioDataStamped, f"{self.ns}/onboard_mic/audio", 50
        )

        self.n = 0
        self.create_timer(10.0, self._report)

    # ---- パイプライン ----

    def build_pipeline(self) -> str:
        fake = env_bool("USE_FAKE_SOURCES")
        rate = env_int("ONBOARD_MIC_RATE")
        ch = env_int("ONBOARD_MIC_CHANNELS")

        if fake:
            src = "audiotestsrc is-live=true wave=pink-noise"
        else:
            src = f"alsasrc device={env('ONBOARD_MIC_DEVICE')} buffer-time=200000"

        # OME は PC-C に systemd で常駐しているので、必ず先に立っている前提。
        rtmp = (
            f"rtmp://{env('PC_C_IP')}:{env('OME_RTMP_PORT')}"
            f"/{env('OME_APP')}/{env('STREAM_KEY_MIC')} live=true"
        )
        # sync=false。live 送出なのでバッファは実時間で届く。既定の sync=true
        # だと rtmpsink がパイプライン latency ぶん抱え込んでから出すので、
        # 往復に効いてくるだけで得が無い。
        send = (
            f"voaacenc bitrate=64000 ! aacparse "
            f"! flvmux streamable=true ! rtmpsink sync=false location=\"{rtmp}\""
        )

        return (
            f"{src} ! audio/x-raw,format=S16LE,rate={rate},channels={ch} ! tee name=at "
            f"at. ! queue max-size-time=500000000 leaky=downstream "
            f"! audioconvert ! audioresample ! {send} "
            f"at. ! queue max-size-time=2000000000 ! appsink name=rec"
        )

    # ---- 記録 ----

    def on_sample(self, name, data, unix_ns, sample):
        msg = AudioDataStamped()
        msg.header.stamp = self.to_ros_time(unix_ns)
        msg.header.frame_id = "onboard_mic"
        set_audio_data(msg, data)
        self.pub.publish(msg)
        self.n += 1

    def _report(self):
        self.get_logger().info(f"audio {self.n} msg (10s)")
        self.n = 0


if __name__ == "__main__":
    run(OnboardMicBridge)
