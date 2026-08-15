#!/usr/bin/env python3
"""Xacti カメラ + 機体マイク -> OME(RTMP) と ROS 記録。

    v4l2src ─ h264parse ─ tee ─ queue(leaky) ─ flvmux ─ rtmpsink  (OME: <robot>stream)
                               └ queue ────── appsink rec_video  (記録)
    alsasrc ───────────────── tee ─ queue(leaky) ─ aac ─ rtmpsink (OME: <robot>mic)
                               └ queue ────── appsink rec_audio  (記録)

映像と機体マイクは別ストリームにしてある。PC-D が音声だけを取り出せる
ようにするため。同一ストリームなら PeerConnection 内で自動的に唇同期が
掛かるが、それを捨てる代わりに経路の独立を取っている（記録側は PC-B の
bag で時刻が揃うので、精密な対応付けはそちらで行う）。

カメラは MJPG で取り、有効範囲（中央 1080×1080）を切り出してから H.264 に
符号化する。映像は符号化したまま ROS に載せる（sensor_msgs/Image に展開すると
数十倍になる）。h264parse の config-interval=-1 は必須 — SPS/PPS を IDR ごとに
入れないと bag を分割したときに 2 個目以降が単体で復号できない。
"""

import array

from audio_common_msgs.msg import AudioDataStamped, AudioInfo
from rclpy.qos import DurabilityPolicy, QoSProfile

from gst_ros_common import (GstBridgeNode, env, env_bool, env_int, robot_ns,
                            run, set_audio_data)

try:
    from foxglove_msgs.msg import CompressedVideo

    HAVE_FOXGLOVE = True
except ImportError:  # 退路: sensor_msgs/CompressedImage に format="h264" で入れる
    from sensor_msgs.msg import CompressedImage

    HAVE_FOXGLOVE = False


class CamBridge(GstBridgeNode):
    def __init__(self):
        self.ns = robot_ns()
        super().__init__("cam_bridge")

        if HAVE_FOXGLOVE:
            self.pub_video = self.create_publisher(
                CompressedVideo, f"{self.ns}/camera/video", 10
            )
        else:
            self.get_logger().warn(
                "foxglove_msgs が無いので sensor_msgs/CompressedImage を使う "
                "(sudo apt install ros-$ROS_DISTRO-foxglove-msgs)"
            )
            self.pub_video = self.create_publisher(
                CompressedImage, f"{self.ns}/camera/video", 10
            )

        self.pub_audio = self.create_publisher(
            AudioDataStamped, f"{self.ns}/onboard_mic/audio", 50
        )

        # AudioInfo は latched で 1 回だけ。bag に残るので後から設定を探さずに済む。
        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.pub_info = self.create_publisher(
            AudioInfo, f"{self.ns}/onboard_mic/info", latched
        )
        info = AudioInfo()
        info.channels = env_int("ONBOARD_MIC_CHANNELS", 2)
        info.sample_rate = env_int("ONBOARD_MIC_RATE", 48000)
        info.sample_format = "S16LE"
        info.bitrate = info.sample_rate * info.channels * 16
        info.coding_format = "WAVE"
        self.pub_info.publish(info)

        self.n_video = 0
        self.n_audio = 0
        self.create_timer(10.0, self._report)

    # ---- パイプライン ----

    def appsink_names(self):
        return ["rec_video", "rec_audio"]

    def build_pipeline(self) -> str:
        fake = env_bool("USE_FAKE_SOURCES", True)
        sw = env_int("CAM_SRC_WIDTH", 1920)
        sh = env_int("CAM_SRC_HEIGHT", 1080)
        w = env_int("CAM_WIDTH", 1080)
        h = env_int("CAM_HEIGHT", 1080)
        fps = env_int("CAM_FPS", 30)
        kbps = env_int("CAM_BITRATE", 8000)

        # 有効範囲だけを切り出す。左右／上下を均等に落とす。
        crop_lr = max(0, (sw - w) // 2)
        crop_tb = max(0, (sh - h) // 2)
        crop = (
            f"videocrop left={crop_lr} right={sw - w - crop_lr} "
            f"top={crop_tb} bottom={sh - h - crop_tb} ! "
            if (crop_lr or crop_tb)
            else ""
        )
        enc = (
            f"x264enc tune=zerolatency speed-preset=ultrafast "
            f"bitrate={kbps} key-int-max={fps}"
        )
        # OME は PC-C に systemd で常駐しているので、必ず先に立っている前提。
        rtmp = (
            f"rtmp://{env('PC_C_IP', '127.0.0.1')}:{env('OME_RTMP_PORT', '1935')}"
            f"/{env('OME_APP', 'app')}/{env('STREAM_KEY_MAIN', 'stream')} live=true"
        )
        send_sink = f"flvmux streamable=true ! rtmpsink location=\"{rtmp}\""
        mic_rtmp = (
            f"rtmp://{env('PC_C_IP', '127.0.0.1')}:{env('OME_RTMP_PORT', '1935')}"
            f"/{env('OME_APP', 'app')}/{env('STREAM_KEY_MIC', 'mic')} live=true"
        )
        mic_sink = (
            f"voaacenc bitrate=64000 ! aacparse "
            f"! flvmux streamable=true ! rtmpsink location=\"{mic_rtmp}\""
        )

        # --- 映像ソース ---
        if fake:
            vsrc = (
                f"videotestsrc is-live=true pattern=ball "
                f"! video/x-raw,width={sw},height={sh},framerate={fps}/1 "
                f"! timeoverlay ! {crop}videoconvert "
                f"! {enc} ! video/x-h264,profile=baseline"
            )
        else:
            # MJPG で取り、切り出してから H.264 に符号化する。
            # 復号と再符号化はどのみち通るので、切り出しの追加コストは小さい。
            # 画素数が 44% 減るぶん符号化はむしろ軽くなる。
            vsrc = (
                f"v4l2src device={env('CAM_DEVICE', '/dev/video0')} "
                f"do-timestamp=true io-mode=2 "
                f"! image/jpeg,width={sw},height={sh},framerate={fps}/1 "
                f"! jpegdec ! {crop}videoconvert ! {enc} "
                f"! video/x-h264,profile=baseline"
            )

        # --- 音声ソース（機体マイク） ---
        arate = env_int("ONBOARD_MIC_RATE", 48000)
        ach = env_int("ONBOARD_MIC_CHANNELS", 2)
        if fake:
            asrc = "audiotestsrc is-live=true wave=pink-noise"
        else:
            asrc = f"alsasrc device={env('ONBOARD_MIC_DEVICE', 'default')} buffer-time=200000"
        asrc += f" ! audio/x-raw,format=S16LE,rate={arate},channels={ach}"

        return (
            f"{vsrc} ! h264parse config-interval=-1 ! tee name=vt "
            f"vt. ! queue max-size-buffers=100 leaky=downstream "
            f"! {send_sink} "
            f"vt. ! queue max-size-time=2000000000 ! appsink name=rec_video "
            f"{asrc} ! tee name=at "
            f"at. ! queue max-size-time=500000000 leaky=downstream "
            f"! audioconvert ! audioresample ! {mic_sink} "
            f"at. ! queue max-size-time=2000000000 "
            f"! appsink name=rec_audio"
        )

    # ---- 記録 ----

    def on_sample(self, name, data, unix_ns, sample):
        stamp = self.to_ros_time(unix_ns)
        if name == "rec_video":
            if HAVE_FOXGLOVE:
                msg = CompressedVideo()
                msg.timestamp = stamp
                msg.frame_id = "camera"
                msg.format = "h264"
            else:
                msg = CompressedImage()
                msg.header.stamp = stamp
                msg.header.frame_id = "camera"
                msg.format = "h264"
            msg.data = array.array("B", data)
            self.pub_video.publish(msg)
            self.n_video += 1
        elif name == "rec_audio":
            msg = AudioDataStamped()
            msg.header.stamp = stamp
            msg.header.frame_id = "onboard_mic"
            set_audio_data(msg, data)
            self.pub_audio.publish(msg)
            self.n_audio += 1

    def _report(self):
        self.get_logger().info(
            f"video {self.n_video} msg / audio {self.n_audio} msg (10s)"
        )
        self.n_video = 0
        self.n_audio = 0


if __name__ == "__main__":
    run(CamBridge)
