#!/usr/bin/env python3
"""gst -> ROS ブリッジの共通部分。

各ブリッジノードはこの GstBridgeNode を継承し、build_pipeline() で
自分のパイプラインを組み、on_sample() で ROS メッセージに詰める。

時刻の扱い:
    パイプラインクロックは MONOTONIC のまま使い、記録側で UNIX 時間へ換算する。
        unix_ns = segment.to_running_time(pts) + pipeline.get_base_time() + offset
        offset  = CLOCK_REALTIME - CLOCK_MONOTONIC
    REALTIME クロックに切り替えると NTP の step で PTS が飛ぶため使わない。

    PTS をそのまま使ってはいけない。x264enc は負の DTS を避けるため PTS に
    3600000 秒の固定オフセットを載せる（実測で確認）。segment 経由で
    running_time に直せばこれが消える。
"""

import array
import os
import time
from functools import partial

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

import rclpy  # noqa: E402
from rclpy.executors import ExternalShutdownException  # noqa: E402
from rclpy.node import Node  # noqa: E402
from rclpy.time import Time  # noqa: E402


def clock_offset_ns() -> int:
    """CLOCK_REALTIME - CLOCK_MONOTONIC [ns]."""
    return time.clock_gettime_ns(time.CLOCK_REALTIME) - time.clock_gettime_ns(
        time.CLOCK_MONOTONIC
    )


def env(name: str, default: str = None) -> str:
    """環境変数を読む。**config.env が持つ項目には既定値を渡さない。**

    既定値をここに書くと config.env と二重定義になり、片方だけ直したときに
    黙って食い違う（実際 SOUNDMAP_BITRATE が 500 と 2000 で割れていた）。
    設定の出所は config.env ひとつに絞り、未設定なら起動時に落とす。
    既定値を渡してよいのは config.env に無い項目だけ。
    """
    if default is None:
        try:
            return os.environ[name]
        except KeyError:
            raise RuntimeError(
                f"{name} が未設定。env.sh を読まずに起動している"
                f"（common/config.env か各 PC の config.env が設定する）"
            ) from None
    return os.environ.get(name, default)


def env_int(name: str, default: int = None) -> int:
    try:
        return int(env(name, None if default is None else str(default)))
    except ValueError:
        if default is None:
            raise
        return default


def env_bool(name: str, default: bool = None) -> bool:
    v = env(name, None if default is None else ("1" if default else "0"))
    return v.strip() in ("1", "true", "True", "yes")


def robot_ns() -> str:
    # 既定値は置かない。未設定のまま起動すると別の接頭辞で publish してしまい、
    # 収録側と噛み合わずに「エラー無しで何も録れていない」状態になる。
    return "/" + env("ROBOT_NAME")


# AudioDataStamped には 2 系統ある。
#   reference code の blr/audio_common_msgs : header + audio(AudioData).data
#   ros2_ws / 一部の配布版                  : header + data
# どちらでも動くようにここで吸収する。
_AUDIO_NESTED = None


def set_audio_data(msg, data: bytes) -> None:
    """AudioDataStamped の data フィールドに bytes を入れる。"""
    global _AUDIO_NESTED
    if _AUDIO_NESTED is None:
        _AUDIO_NESTED = hasattr(msg, "audio")
    buf = array.array("B", data)
    if _AUDIO_NESTED:
        msg.audio.data = buf
    else:
        msg.data = buf


class GstBridgeNode(Node):
    """gst パイプラインを持ち、appsink から ROS へ流すノードの基底クラス。

    GLib の MainLoop は回さない。appsink の new-sample はストリーミング
    スレッドから直接呼ばれるため MainLoop は不要で、bus は ROS タイマから
    ポーリングする（イベントループを 2 つ持たない）。
    """

    def __init__(self, node_name: str):
        super().__init__(node_name)
        Gst.init(None)

        self.pipeline = None
        self._offset_ns = clock_offset_ns()

        pipeline_desc = self.build_pipeline()
        self.get_logger().info(f"pipeline: {pipeline_desc}")
        self.pipeline = Gst.parse_launch(pipeline_desc)

        for name in self.appsink_names():
            sink = self.pipeline.get_by_name(name)
            if sink is None:
                raise RuntimeError(f"appsink name={name} がパイプラインに見つからない")
            # 記録用 appsink の設定（既定値はどれも録画向きではない）
            sink.set_property("emit-signals", True)
            sink.set_property("sync", False)        # 実時間で間引かない
            sink.set_property("max-buffers", 200)   # 0 = 無制限だと膨らむ
            sink.set_property("drop", False)        # 記録は落とさない
            sink.connect("new-sample", partial(self._on_new_sample, name))

        self.bus = self.pipeline.get_bus()
        self.create_timer(0.5, self._poll_bus)

        # offset はメッセージごとに読み直す（vDSO 呼び出しなので安価）。
        # 1 Hz の記録は clock_node.py が別途 bag に残す。
        self.create_timer(1.0, self._refresh_offset)

        # PLAYING はここでは開始しない。基底クラスの __init__ はサブクラスの
        # __init__ より先に終わるため、ここで流し始めると publisher や
        # カウンタが揃う前に on_sample が呼ばれる。start_pipeline() を
        # run() から呼ぶ。

    def start_pipeline(self):
        self.pipeline.set_state(Gst.State.PLAYING)
        self.get_logger().info("pipeline PLAYING")

    # ---- サブクラスが実装する ----

    def build_pipeline(self) -> str:
        """gst-launch 形式の文字列を返す。記録用 appsink には name を付けること。"""
        raise NotImplementedError

    def appsink_names(self) -> list:
        """接続する appsink の name 一覧。"""
        return ["rec"]

    def on_sample(self, name: str, data: bytes, unix_ns: int, sample) -> None:
        """appsink から 1 サンプル届いたときに呼ばれる。"""
        raise NotImplementedError

    # ---- 内部 ----

    def _refresh_offset(self):
        self._offset_ns = clock_offset_ns()

    def sample_to_unix_ns(self, sample, buf) -> int:
        base = self.pipeline.get_base_time()
        if buf.pts == Gst.CLOCK_TIME_NONE or base == Gst.CLOCK_TIME_NONE:
            # タイムスタンプが無いバッファは現在時刻で代用する（本来は起きない）
            self.get_logger().warn("buffer に PTS が無い", throttle_duration_sec=10.0)
            return time.clock_gettime_ns(time.CLOCK_REALTIME)
        segment = sample.get_segment()
        running = segment.to_running_time(Gst.Format.TIME, buf.pts)
        if running is None or running == Gst.CLOCK_TIME_NONE:
            self.get_logger().warn(
                "running_time に変換できない", throttle_duration_sec=10.0
            )
            return time.clock_gettime_ns(time.CLOCK_REALTIME)
        return int(running) + int(base) + self._offset_ns

    def to_ros_time(self, unix_ns: int) -> Time:
        return Time(nanoseconds=unix_ns).to_msg()

    def _on_new_sample(self, name, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            data = bytes(info.data)
            unix_ns = self.sample_to_unix_ns(sample, buf)
            self.on_sample(name, data, unix_ns, sample)
        except Exception as e:  # ここで例外を漏らすとパイプラインが止まる
            self.get_logger().error(f"on_sample[{name}]: {e}")
        finally:
            buf.unmap(info)
        return Gst.FlowReturn.OK

    def _poll_bus(self):
        msg = self.bus.timed_pop_filtered(
            0, Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.WARNING
        )
        while msg is not None:
            if msg.type == Gst.MessageType.ERROR:
                err, dbg = msg.parse_error()
                self.get_logger().error(f"gst error: {err} | {dbg}")
            elif msg.type == Gst.MessageType.WARNING:
                err, dbg = msg.parse_warning()
                self.get_logger().warn(f"gst warning: {err} | {dbg}")
            elif msg.type == Gst.MessageType.EOS:
                self.get_logger().error("gst EOS: ソースが終了した")
            msg = self.bus.timed_pop_filtered(
                0, Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.WARNING
            )

    def destroy_node(self):
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
        super().destroy_node()


def run(node_cls):
    """定型の main。"""
    rclpy.init()
    node = None
    try:
        node = node_cls()
        node.start_pipeline()   # サブクラスの初期化が終わってから流し始める
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.try_shutdown()
