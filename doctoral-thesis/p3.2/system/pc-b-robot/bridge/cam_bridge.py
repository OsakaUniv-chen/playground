#!/usr/bin/env python3
"""Xacti カメラ -> OME(RTMP) と ROS 記録。

    v4l2src ─ h264parse ─ tee ─ queue(leaky) ─ flvmux ─ rtmpsink  (OME: <robot>stream)
                               └ queue ────────────── appsink rec (記録)

機体マイクは別ストリーム（`<robot>mic`）で、**別プロセス**の
onboard_mic_bridge.py が持つ。PC-D が音声だけを取り出せるようにするためで、
プロセスを分けてあるのは ALSA デバイスが開けなくてもカメラを巻き込まない
ようにするため（詳しくは onboard_mic_bridge.py の冒頭）。

カメラは MJPG で取り、有効範囲（中央 1080×1080）を切り出してから H.264 に
符号化する。映像は符号化したまま ROS に載せる（sensor_msgs/Image に展開すると
数十倍になる）。h264parse の config-interval=-1 は必須 — SPS/PPS を IDR ごとに
入れないと bag を分割したときに 2 個目以降が単体で復号できない。
"""

import array
import glob
import os

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from gst_ros_common import (GstBridgeNode, env, env_bool, env_int, robot_ns,  # noqa: E402
                            run)

_V4L_SYSFS = "/sys/class/video4linux"


def _v4l2_nodes():
    """今ある v4l2 ノードを [(/dev/videoN, カード名, index), ...] で返す。"""
    out = []
    for d in glob.glob(f"{_V4L_SYSFS}/video*"):
        base = os.path.basename(d)
        try:
            with open(os.path.join(d, "name")) as f:
                name = f.read().strip()
            with open(os.path.join(d, "index")) as f:
                index = int(f.read().strip())
        except (OSError, ValueError):
            continue
        out.append((f"/dev/{base}", name, index))
    return sorted(out, key=lambda t: int(t[0].rsplit("video", 1)[1]))


def resolve_cam_device(spec):
    """`CAM_DEVICE` を実際の `/dev/videoN` に解決する。

    **`/` で始まればそのまま使い、そうでなければカメラの名前として探す。**
    `/dev/videoN` の番号は USB の列挙順で決まるので、機体に挿し直したり
    他の USB 機器を足したりするだけでずれる。名前で引けば固定される
    （UMA16v2 を `hw:CARD=UMA16v2` で指しているのと同じ考え方。V4L2 には
    ALSA のような名前指定が無いので、ここで肩代わりする）。

    **やり方は沿用元の `camera_node.py`（`~/ros2_ws`、実績あり）と同じ。**
    あちらは pyudev で `video4linux` を列挙して同じ sysfs の `name` を読み、
    `split(':')[0]` を鍵に**同名のうち一番若い `/dev/videoN`** を採る。
    ここで pyudev を使わないのは、読む先が同じ sysfs で依存を 1 つ増やす
    だけだから。CX-MT500 の name は `CX-MT500: ...` の形なので、こちらは
    部分一致にしてある（`Xacti CX-MT500` のように前に何か付いても拾える）。

    **UVC カメラは 1 台で 2 ノード生える。** 映像の取り口と metadata 用で、
    名前だけで選ぶと metadata 側を掴んで caps 交渉に失敗することがある。
    若い番号を採れば避けられる（沿用元が実績で示しているのがこれ）。その上で
    sysfs の `index`（取り口が 0、metadata が 1）でも絞り、二重に外す。
    """
    if spec.startswith("/"):
        return spec

    nodes = _v4l2_nodes()
    named = [n for n in nodes if spec.lower() in n[1].lower()]
    # index=0 が取り口。念のため、名前は合うのに index=0 が無いドライバでは
    # 若い番号に落とす（掴み損ねて起動しないより、試して失敗するほうがよい）。
    hit = [n for n in named if n[2] == 0] or named
    if not hit:
        listing = ", ".join(f"{d}={name!r}(index={i})" for d, name, i in nodes)
        raise RuntimeError(
            f"CAM_DEVICE={spec!r} という名前のカメラが見つからない。"
            f"今あるのは: {listing or '（v4l2 デバイスが 1 つも無い）'}。"
            f"名前は `ls /sys/class/video4linux/*/name | xargs cat` か "
            f"`v4l2-ctl --list-devices` で確認する"
        )
    return hit[0][0]

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

        self.n_video = 0
        self.create_timer(10.0, self._report)

    # ---- パイプライン ----

    @staticmethod
    def _sw_crop(sw, sh, w, h) -> str:
        """有効範囲だけを切り出す（ソフトウェア）。左右／上下を均等に落とす。"""
        lr = max(0, (sw - w) // 2)
        tb = max(0, (sh - h) // 2)
        if not (lr or tb):
            return ""
        return (
            f"videocrop left={lr} right={sw - w - lr} "
            f"top={tb} bottom={sh - h - tb} ! "
        )

    def _transcode(self, sw, sh, w, h, fps, kbps, sw_enc):
        """MJPG -> 切り出し -> H.264。iGPU が使えるならそちらでやる。

        **速さのためではない。** 符号化＋復号は実測 1.5 ms で、往復 75 ms の
        中では誤差（大半は OME の固定 21 ms と 1 フレーム 33 ms）。狙いは
        **CPU を空けること**。PC-B は N100 の 4 コアで 16ch の音響マップ生成と
        bag 書き込みも同時に回しており、1080×1080@30 のソフトウェア x264 と
        MJPG 復号だけで 1.5〜2 コア持っていかれる。

        おまけとして、VA-API は常に NV12（4:2:0）なので、x264enc が上流の
        色形式によって High 4:4:4 を選んでしまう問題（README 参照）が
        構造的に起きなくなる。プロファイルも SDP の 42e01f と一致する
        constrained-baseline を直接指定できる。

        エレメントが無ければ黙ってソフトウェアに戻る。**現地で GPU が
        使えなくても起動はする。**
        """
        rot = env_bool("CAM_ROTATE_180")
        need = ("vaapijpegdec", "vaapipostproc", "vaapih264enc")
        if env_bool("USE_HW_CODEC") and all(
            Gst.ElementFactory.find(e) for e in need
        ):
            lr = max(0, (sw - w) // 2)
            tb = max(0, (sh - h) // 2)
            self.get_logger().info("映像: iGPU（VA-API）で復号・切り出し・符号化")
            return (
                "vaapijpegdec",
                (
                    f"vaapipostproc crop-left={lr} crop-right={sw - w - lr} "
                    f"crop-top={tb} crop-bottom={sh - h - tb} "
                    + ("video-direction=180 " if rot else "")
                    + "! "
                ),
                (
                    f"vaapih264enc rate-control=cbr bitrate={kbps} "
                    f"keyframe-period={fps} tune=low-power"
                ),
                "constrained-baseline",
            )

        missing = [e for e in need if Gst.ElementFactory.find(e) is None]
        self.get_logger().warn(
            "映像: ソフトウェアで符号化する。"
            + (f"無いエレメント: {', '.join(missing)}。" if missing else "")
            + "sudo apt install gstreamer1.0-vaapi で CPU が 1.5〜2 コア空く"
        )
        # videoconvert は必須。x264enc は上流が I420 でないと High 4:4:4 を
        # 選び、ブラウザが復号できなくなる（README 参照）。
        # 回転は切り出しの後に置く（画素数が 44% 少ない状態で回す）。
        flip = "videoflip method=rotate-180 ! " if rot else ""
        return (
            "jpegdec",
            self._sw_crop(sw, sh, w, h) + flip + "videoconvert ! ",
            sw_enc,
            "baseline",
        )

    def build_pipeline(self) -> str:
        fake = env_bool("USE_FAKE_SOURCES")
        sw = env_int("CAM_SRC_WIDTH")
        sh = env_int("CAM_SRC_HEIGHT")
        w = env_int("CAM_WIDTH")
        h = env_int("CAM_HEIGHT")
        fps = env_int("CAM_FPS")
        kbps = env_int("CAM_BITRATE")

        sw_enc = (
            f"x264enc tune=zerolatency speed-preset=ultrafast "
            f"bitrate={kbps} key-int-max={fps}"
        )
        # OME は PC-C に systemd で常駐しているので、必ず先に立っている前提。
        rtmp = (
            f"rtmp://{env('PC_C_IP')}:{env('OME_RTMP_PORT')}"
            f"/{env('OME_APP')}/{env('STREAM_KEY_MAIN')} live=true"
        )
        # sync=false。live 送出なのでフレームは実時間で届く。既定の sync=true
        # だと rtmpsink がパイプライン latency ぶん抱え込んでから出すので、
        # 往復 75 ms に効いてくるだけで得が無い。
        send_sink = f"flvmux streamable=true ! rtmpsink sync=false location=\"{rtmp}\""

        # --- 映像ソース ---
        if fake:
            # 試験経路はソフトウェア固定。GPU の有無で挙動が変わらないようにする。
            vsrc = (
                f"videotestsrc is-live=true pattern=ball "
                f"! video/x-raw,width={sw},height={sh},framerate={fps}/1 "
                f"! timeoverlay ! {self._sw_crop(sw, sh, w, h)}videoconvert "
                f"! {sw_enc} ! video/x-h264,profile=baseline"
            )
        else:
            # カメラは MJPG しか出さないので、H.264 への変換は避けられない
            # （OME の入口である RTMP が MJPG を運べない）。切り出しは復号後に
            # なるが、画素数が 44% 減るぶん符号化はむしろ軽くなる。
            dec, crop, enc, profile = self._transcode(sw, sh, w, h, fps, kbps, sw_enc)
            spec = env("CAM_DEVICE")
            dev = resolve_cam_device(spec)
            # 名前で引いた場合はどれを掴んだかを残す。挿し替えで別のカメラに
            # 化けていても、ログを見れば分かるようにするため。
            if dev != spec:
                self.get_logger().info(f"カメラ {spec!r} -> {dev}")
            vsrc = (
                f"v4l2src device={dev} "
                f"do-timestamp=true io-mode=2 "
                f"! image/jpeg,width={sw},height={sh},framerate={fps}/1 "
                f"! {dec} ! {crop}{enc} ! video/x-h264,profile={profile}"
            )

        return (
            f"{vsrc} ! h264parse config-interval=-1 ! tee name=vt "
            f"vt. ! queue max-size-buffers=100 leaky=downstream "
            f"! {send_sink} "
            f"vt. ! queue max-size-time=2000000000 ! appsink name=rec"
        )

    # ---- 記録 ----

    def on_sample(self, name, data, unix_ns, sample):
        stamp = self.to_ros_time(unix_ns)
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

    def _report(self):
        self.get_logger().info(f"video {self.n_video} msg (10s)")
        self.n_video = 0


if __name__ == "__main__":
    run(CamBridge)
