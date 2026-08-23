#!/usr/bin/env python3
"""记录：所有东西进同一个 bag，只在有人的时候录。

    各 topic ──▶ 环形缓冲（PREROLL_SEC）──┐
                                          ├──▶ rosbag2 / mcap
    record/trigger（stream-server 5 Hz）──┘ 门控

**判断在 stream-server，这里只管录。** 收到的就是一个 bool，连"为什么要录"
都不知道 —— 将来往那个信号里 OR 别的触发源（操作者按一下按钮之类），
这边一行都不用改。

**为什么不用 `ros2 bag record`：** 那个 CLI 做不了前录。等人出现了才开始录的话，
"人出现之前的十几秒"永远拿不到，而那段恰恰是解释"他为什么走过来"的部分。
所以这里自己包一层 rosbag2_py 的 SequentialWriter，前面挂一个环形缓冲。

**收不到 trigger 就连续录（fail-open）。** stream-server 挂了、LAN 断了、
检测节点卡死了 —— 哪种都一样，"悄无声息地什么都没录到"是最坏的结果。
门控状态本身也写进 bag，事后才解释得了某一段为什么是空的。

用法:
    python3 recorder.py                     生产：起来就等 trigger
    python3 recorder.py --force             不等 trigger，直接连续录（手动确认用）
    python3 recorder.py --topics soundmap/map,record/clock_offset
                                            只订这几条（缺 msg 包时也能验逻辑）
"""
from __future__ import annotations

import argparse
import importlib
import os
import shutil
import signal
import sys
import time
from collections import deque

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from rclpy.serialization import serialize_message

# =====================================================================
# 配置。**部署参数（录到哪、前后录多久）在 config.env，不在这里。**
# 这一段是结构性的东西：录哪些 topic、缓冲留多少余量。
# =====================================================================

# 录哪些 topic。前缀 /<ROBOT_NAME>/ 省略；和 system-architecture.md §6.3 的表一致。
#   kind = "video"   H.264。**切点必须落在关键帧上**，见 Ring.take
#          "data"    按时刻切就行
#          "trigger" 门控输入，同时也录进去（事后要能对照）
#   depth = 订阅队列深度。视频一帧几十 KB，深了白占内存；音频 100 msg/s，浅了会丢
TOPICS = [
    # (topic, 类型, kind, depth)
    ("fisheye/video",       "foxglove_msgs/msg/CompressedVideo", "video",   60),
    ("realsense/video",     "foxglove_msgs/msg/CompressedVideo", "video",   60),
    ("navcam/video",        "foxglove_msgs/msg/CompressedVideo", "video",   60),
    ("onboard_mic/audio",   "teleop_msgs/msg/AudioChunk",            "data",   200),
    ("mic_array/audio",     "teleop_msgs/msg/AudioChunk",            "data",   200),
    ("operator_mic/audio",  "teleop_msgs/msg/AudioChunk",            "data",   200),
    ("soundmap/map",        "teleop_msgs/msg/SoundMap",              "data",    60),
    ("record/clock_offset", "teleop_msgs/msg/ClockOffset",           "data",    10),
    ("record/gate",         "teleop_msgs/msg/RecordGate",            "data",    10),
    ("record/trigger",      "std_msgs/msg/Bool",                 "trigger",  5),
]

# 环形缓冲比 PREROLL_SEC 多留这么久 [s]。**H.264 要从关键帧开始切**，
# 切点落在 GOP 中间时得往前找最近的那个关键帧，它可能比 PREROLL 还早一点。
# GOP = 1 s（keyframe-period=30 @30fps），留 2 s 富余。
RING_MARGIN_SEC = 2.0

# 环形缓冲的硬上限 [MB]。按时间裁本来就够（22.5 Mbps × 12 s ≈ 34 MB），
# 这条是保险丝：万一哪个发布端的时间戳是坏的，别把机体的内存吃光。
RING_MAX_MB = 512

# 门控检查周期 [s]。postroll 到点、trigger 断了都靠它发现。
TICK_SEC = 0.2

# 多久报一行状态 [s]。状态面板拿这一行显示（和 soundmap.py / speaker.py 一样）。
REPORT_SEC = 10

# 缺包时的提示。**不静默跳过** —— 少录一路而没人发现是最坏的结果。
APT_HINT = {
    "foxglove_msgs": "sudo apt install ros-$ROS_DISTRO-foxglove-msgs",
    "teleop_msgs": "在 workspace 里 colcon build --packages-select teleop_msgs，再 source 那个 workspace",
}


def log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}", flush=True)


def env(key: str) -> str:
    """config.env 负责的项，这里不给默认值（两处写默认值必然对不上）。"""
    try:
        return os.environ[key]
    except KeyError:
        raise SystemExit(
            f"[error] {key} 未设置 —— 没读到 config.env。"
            f"用 ./start_gstreamer.sh 启动，或先 `source config.env`。"
        ) from None


def load_type(type_str: str):
    """"pkg/msg/Type" -> 消息类。"""
    pkg, _, name = type_str.split("/")
    try:
        return getattr(importlib.import_module(f"{pkg}.msg"), name)
    except (ImportError, AttributeError) as e:
        raise SystemExit(
            f"[error] 拿不到消息类型 {type_str}（{e}）。"
            f"{APT_HINT.get(pkg, '这个包没装')}"
        ) from None


def stamp_ns(msg) -> int:
    """消息自带的采集时刻。

    **不用"收到的那一刻"** —— gst 那几路的时刻是从管线 PTS 换算出来的
    采集时刻，比到达时刻早一整条编码+发布的路。没有 header 的（trigger）
    才退回当前时刻。
    """
    # teleop_msgs 用 std_msgs/Header；**foxglove 的消息没有 header**，
    # 时刻直接叫 timestamp（CompressedVideo 就是）。两种都要认，
    # 认错的表现是视频按"到达时刻"入库，和音频差着一整条编码+发布的路。
    h = getattr(msg, "header", None)
    t = h.stamp if h is not None else getattr(msg, "timestamp", None)
    if t is None:
        return time.clock_gettime_ns(time.CLOCK_REALTIME)
    return int(t.sec) * 1_000_000_000 + int(t.nanosec)


def is_keyframe(data: bytes) -> bool:
    """这一帧 H.264 是不是关键帧（Annex-B）。

    发布端是 `h264parse config-interval=-1`，所以关键帧前面一定带着 SPS(7)，
    非关键帧的第一个 NAL 是 1。AUD(9) / SEI(6) 可能夹在前面，所以往后多看几个。
    """
    i, seen = 0, 0
    n = len(data)
    while i + 4 < n and seen < 6:
        if data[i] == 0 and data[i + 1] == 0:
            if data[i + 2] == 1:
                nal = data[i + 3] & 0x1F
                i += 4
            elif data[i + 2] == 0 and data[i + 3] == 1 and i + 5 < n:
                nal = data[i + 4] & 0x1F
                i += 5
            else:
                i += 1
                continue
            if nal in (5, 7):        # IDR / SPS
                return True
            if nal in (1,):          # 非 IDR 的片，后面不会再有 SPS 了
                return False
            seen += 1
        else:
            i += 1
    return False


# =====================================================================
# 环形缓冲
# =====================================================================


class Ring:
    """每个 topic 一条队列，按时刻裁。

    存的是**已经序列化好的字节**：吐进 bag 的时候直接写，不用再走一遍
    序列化；而且视频一帧只有一份拷贝。
    """

    def __init__(self, keep_ns: int, max_bytes: int):
        self.keep_ns = keep_ns
        self.max_bytes = max_bytes
        self.q: dict[str, deque] = {}
        self.bytes = 0
        self._warned = 0.0

    def push(self, topic: str, ts: int, data: bytes, key: bool) -> None:
        self.q.setdefault(topic, deque()).append((ts, data, key))
        self.bytes += len(data)

    def trim(self, now_ns: int) -> None:
        cut = now_ns - self.keep_ns
        for dq in self.q.values():
            while dq and dq[0][0] < cut:
                self.bytes -= len(dq.popleft()[1])
        # 保险丝：时间戳坏掉的话上面那句裁不动，这里按字节强裁最老的。
        while self.bytes > self.max_bytes:
            longest = max(self.q.values(), key=len, default=None)
            if not longest:
                break
            self.bytes -= len(longest.popleft()[1])
            now = time.monotonic()
            if now - self._warned > 10:
                self._warned = now
                log("warn", f"★ 环形缓冲超过 {self.max_bytes // 1048576} MB 了，"
                            f"按字节强裁 —— 多半是哪一路的时间戳不对")

    def take(self, topic: str, cut_ns: int, video: bool) -> list:
        """要从 cut_ns 开始录，这条 topic 该吐出哪些。"""
        dq = self.q.get(topic)
        if not dq:
            return []
        items = list(dq)
        if not video:
            return [it for it in items if it[0] >= cut_ns]
        # **H.264 从 GOP 中间开始写的话，开头几十帧解不出来。**
        # 往前找 cut 之前最近的关键帧；找不到就用 cut 之后的第一个
        # （宁可少录一点，也不要写一段解不开的）。
        start = None
        for i, (ts, _d, key) in enumerate(items):
            if key and ts <= cut_ns:
                start = i
            elif key and ts > cut_ns and start is None:
                start = i
                break
        return items[start:] if start is not None else []


# =====================================================================
# 一次录制 = 一个 bag
# =====================================================================


class Session:
    def __init__(self, path: str, storage: str, split_mb: int, topics: list):
        import rosbag2_py

        self.path = path
        self.n = 0
        self.bytes = 0
        self.t0 = time.monotonic()

        opts = rosbag2_py.StorageOptions(
            uri=path,
            storage_id=storage,
            # 内部切片。崩溃时最多丢末尾一片，而读取端把整个目录当一段连续记录。
            max_bagfile_size=split_mb * 1024 * 1024,
            # 写盘在 rosbag2 自己的线程上做，别让它卡住 ROS 的回调。
            max_cache_size=64 * 1024 * 1024,
        )
        try:
            self.w = rosbag2_py.SequentialWriter()
            self.w.open(opts, rosbag2_py.ConverterOptions("", ""))
        except Exception as e:
            raise SystemExit(
                f"[error] 打不开 bag（storage={storage}）: {e}\n"
                f"        mcap 要装 ros-$ROS_DISTRO-rosbag2-storage-mcap；"
                f"没有的话把 config.env 的 BAG_STORAGE 改成 sqlite3"
            ) from None
        # **用完整的 /<ROBOT_NAME>/… 名字建 topic。** 写短名的话 `ros2 bag play`
        # 会把它们发到 /record/trigger 这种没有前缀的地方去，和现场的名字对不上。
        for _short, full, type_str, _kind, _depth in topics:
            self.w.create_topic(rosbag2_py.TopicMetadata(
                name=full, type=type_str, serialization_format="cdr"))

    def write(self, topic: str, data: bytes, ts: int) -> None:
        # log_time 用采集时刻（§6.4）：读取端不管看哪个时刻，拿到的都是采集时刻。
        self.w.write(topic, data, ts)
        self.n += 1
        self.bytes += len(data)

    def close(self) -> None:
        # Humble 的 SequentialWriter 没有 close()，靠析构收尾。
        self.w = None


# =====================================================================
# 记录节点
# =====================================================================

IDLE, RECORDING, POSTROLL = 0, 1, 2
STATE_NAME = {IDLE: "待机", RECORDING: "录制中", POSTROLL: "postroll"}


class Recorder(Node):
    def __init__(self, only: list | None, force: bool):
        super().__init__("recorder")
        self.robot = env("ROBOT_NAME")
        self.dir = os.path.expanduser(env("RECORD_DIR"))
        self.preroll_ns = int(float(env("PREROLL_SEC")) * 1e9)
        self.postroll_sec = float(env("POSTROLL_SEC"))
        self.trigger_timeout = float(env("TRIGGER_TIMEOUT_SEC"))
        self.storage = env("BAG_STORAGE")
        self.split_mb = int(env("BAG_SPLIT_MB"))
        self.min_free_gb = float(env("RECORD_MIN_FREE_GB"))
        self.force = force

        os.makedirs(self.dir, exist_ok=True)

        # (短名, 完整名, 类型, kind, depth)。短名只用来给人看和 --topics 过滤，
        # 订阅、缓冲、写 bag 一律用完整名。
        self.topics = [(t[0], f"/{self.robot}/{t[0]}", t[1], t[2], t[3])
                       for t in TOPICS if only is None or t[0] in only]
        if not self.topics:
            raise SystemExit(f"[error] --topics 一个都没匹配上: {only}")

        self.ring = Ring(self.preroll_ns + int(RING_MARGIN_SEC * 1e9),
                         RING_MAX_MB * 1024 * 1024)
        self.session: Session | None = None
        self.state = IDLE
        self.trigger = False
        # **从进程起来的那一刻开始算。** 初值给 0 的话，"从来没收到过 trigger"
        # （stream-server 根本没起、域号对不上、网络不通）就永远不会触发
        # fail-open —— 而那恰恰是最需要兜底连续录的情况。踩过：面板上一直
        # "待机"，缓冲涨到几十 MB，一个 bag 都没有。
        self.trigger_seen = time.monotonic()
        self.fail_open = False
        self.postroll_until = 0.0
        self.n_msg = 0                 # 这 10 秒收到多少条

        types = {t[2]: load_type(t[2]) for t in self.topics}
        self.gate_type = types.get("teleop_msgs/msg/RecordGate")
        self.offset_type = types.get("teleop_msgs/msg/ClockOffset")

        for _short, full, type_str, kind, depth in self.topics:
            if kind == "trigger":
                # ★ **必须 BEST_EFFORT。** 发布端（person_detect）是 BEST_EFFORT，
                # 这边用默认的 RELIABLE 的话两边不兼容，一条都收不到 **而且不报错**。
                qos = QoSProfile(depth=depth,
                                 reliability=ReliabilityPolicy.BEST_EFFORT,
                                 history=HistoryPolicy.KEEP_LAST)
            else:
                qos = QoSProfile(depth=depth,
                                 reliability=ReliabilityPolicy.RELIABLE,
                                 history=HistoryPolicy.KEEP_LAST)
            self.create_subscription(
                types[type_str], full,
                lambda m, t=full, k=kind: self._on_msg(t, k, m), qos)

        # 自己发的两条也走一遍 topic（而不是直接塞进 bag）：
        # 这样 `ros2 topic echo` 看得到，录进去的和别人看到的是同一份。
        if self.gate_type is not None:
            self.gate_pub = self.create_publisher(
                self.gate_type, f"/{self.robot}/record/gate", 10)
        if self.offset_type is not None:
            self.offset_pub = self.create_publisher(
                self.offset_type, f"/{self.robot}/record/clock_offset", 10)
            self.create_timer(1.0, self._clock_tick)

        self.create_timer(TICK_SEC, self._tick)
        self.create_timer(REPORT_SEC, self._report)

        log("info", f"录到 {self.dir}（storage={self.storage}, "
                    f"切片 {self.split_mb} MB）")
        log("info", f"前录 {self.preroll_ns / 1e9:.0f}s / 后录 {self.postroll_sec:.0f}s / "
                    f"trigger 断 {self.trigger_timeout:.0f}s 就连续录")
        for _short, full, type_str, kind, _d in self.topics:
            log("info", f"  订阅 {full}  ({type_str}{'，兼门控' if kind == 'trigger' else ''})")
        if force:
            log("info", "★ --force：不等 trigger，直接开录")
            self._start("--force")

    # ---- 收数据 ----

    def _on_msg(self, topic: str, kind: str, msg) -> None:
        if kind == "trigger":
            self._on_trigger(bool(msg.data))
        ts = stamp_ns(msg)
        data = serialize_message(msg)
        key = kind == "video" and is_keyframe(bytes(msg.data))
        self.ring.push(topic, ts, data, key)
        self.n_msg += 1
        if self.session is not None:
            # 已经在录：环形缓冲那份只是留着给下一段用，这条直接落盘。
            self.session.write(topic, data, ts)
        self.ring.trim(time.clock_gettime_ns(time.CLOCK_REALTIME))

    def _on_trigger(self, value: bool) -> None:
        self.trigger_seen = time.monotonic()
        if self.fail_open:
            self.fail_open = False
            log("info", "trigger 回来了 —— 从连续录切回门控")
            if self.session is not None and not value:
                self._to_postroll("trigger 回来了而且是 false")
        if value == self.trigger:
            if value and self.state == POSTROLL:
                # ④ postroll 期间人又回来了：**不关文件，接着录。**
                # 人来来回回就开开关关的话，文件被切碎，对话也被切碎。
                self.state = RECORDING
                self._gate("postroll 期间又来人了，接着录")
            return
        self.trigger = value
        if value:
            if self.session is None:
                self._start("trigger=true")
            else:
                self.state = RECORDING
                self._gate("trigger=true")
        else:
            if self.session is not None:
                self._to_postroll("trigger=false")

    # ---- 门控 ----

    def _tick(self) -> None:
        now = time.monotonic()
        # trigger 断了 -> 兜底连续录（fail-open）
        if not self.force and now - self.trigger_seen > self.trigger_timeout \
                and not self.fail_open:
            self.fail_open = True
            log("warn", f"★ {self.trigger_timeout:.0f}s 没收到 trigger —— "
                        f"切成连续录（stream-server 挂了？LAN 断了？）")
            if self.session is None:
                self._start("fail-open")
            else:
                self.state = RECORDING
                self._gate("fail-open")
        if self.state == POSTROLL and now >= self.postroll_until:
            self._stop("postroll 到点")

    def _to_postroll(self, why: str) -> None:
        self.state = POSTROLL
        self.postroll_until = time.monotonic() + self.postroll_sec
        self._gate(f"{why} -> 续录 {self.postroll_sec:.0f}s")

    def _start(self, why: str) -> None:
        free_gb = shutil.disk_usage(self.dir).free / 1e9
        if free_gb < self.min_free_gb:
            # 约 10 GB/小时（§6.5）。空间见底了就别开新的了，但要吵。
            log("error", f"★ {self.dir} 只剩 {free_gb:.1f} GB "
                         f"(< RECORD_MIN_FREE_GB={self.min_free_gb})，不开新的 bag")
            return
        name = f"{self.robot}_{time.strftime('%Y%m%d_%H%M%S')}"
        self.session = Session(os.path.join(self.dir, name), self.storage,
                               self.split_mb, self.topics)
        self.state = RECORDING
        log("info", f"=== 开录 {name}（{why}，剩余空间 {free_gb:.0f} GB）===")
        log("info", "    ※ 开始录了就拍一下手 —— 鱼眼/机体麦克风/16ch 同时收到，"
                    "事后用来验时刻换算")

        # ② 先把环形缓冲吐出去，再转入实时写入。
        cut = time.clock_gettime_ns(time.CLOCK_REALTIME) - self.preroll_ns
        items = []
        for _short, full, _t, kind, _d in self.topics:
            for ts, data, _k in self.ring.take(full, cut, kind == "video"):
                items.append((ts, full, data))
        items.sort(key=lambda x: x[0])      # 按时刻归并，bag 里是有序的
        for ts, topic, data in items:
            self.session.write(topic, data, ts)
        if items:
            span = (items[-1][0] - items[0][0]) / 1e9
            log("info", f"    前录吐出 {len(items)} 条 / {span:.1f}s")
        self._gate(why)

    def _stop(self, why: str) -> None:
        if self.session is None:
            return
        s = self.session
        self.session = None
        self.state = IDLE
        s.close()
        log("info", f"=== 停录 {os.path.basename(s.path)}（{why}）"
                    f"{s.n} 条 / {s.bytes / 1e6:.0f} MB / "
                    f"{time.monotonic() - s.t0:.0f}s ===")
        self._gate(why)

    def _gate(self, note: str) -> None:
        if self.gate_type is None:
            return
        m = self.gate_type()
        m.header.stamp = self.get_clock().now().to_msg()
        m.state = self.state
        m.trigger = self.trigger
        m.fail_open = self.fail_open
        m.session = os.path.basename(self.session.path) if self.session else ""
        m.note = note
        self.gate_pub.publish(m)

    # ---- 时刻的对应关系 ----

    def _clock_tick(self) -> None:
        """MONOTONIC ↔ REALTIME 的对应，1 Hz 记进 bag。

        gst 那几路的时刻是 MONOTONIC 基准换算过来的。**整条序列都留着**：
        事后看它平滑就说明换算可信，有台阶就说明那里 NTP step 过。
        """
        mono = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        real = time.clock_gettime_ns(time.CLOCK_REALTIME)
        m = self.offset_type()
        m.header.stamp.sec = real // 1_000_000_000
        m.header.stamp.nanosec = real % 1_000_000_000
        m.monotonic_ns = mono
        m.realtime_ns = real
        m.offset_ns = real - mono
        self.offset_pub.publish(m)

    def _report(self) -> None:
        if self.session is not None:
            free = shutil.disk_usage(self.dir).free / 1e9
            log("info", f"[10s] {STATE_NAME[self.state]} "
                        f"{os.path.basename(self.session.path)} "
                        f"{self.session.n} 条 / {self.session.bytes / 1e6:.0f} MB / "
                        f"剩 {free:.0f} GB"
                        + ("（连续录）" if self.fail_open else ""))
        else:
            log("info", f"[10s] 待机（收到 {self.n_msg} 条 / 缓冲 "
                        f"{self.ring.bytes / 1e6:.0f} MB / trigger={self.trigger}）")
        self.n_msg = 0

    def shutdown(self) -> None:
        self._stop("进程退出")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="robot-pc 的记录：所有 topic 进一个 bag")
    p.add_argument("--force", action="store_true",
                   help="不等 trigger，起来就连续录（手动确认用）")
    p.add_argument("--topics",
                   help="只订这几条（逗号分隔，不带 /<ROBOT_NAME>/ 前缀）。"
                        "缺 msg 包的机器上验逻辑用")
    a = p.parse_args(argv)
    only = [t.strip() for t in a.topics.split(",")] if a.topics else None

    rclpy.init()
    node = Recorder(only, a.force)

    stop = False

    def _sig(*_a):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    # 不用 rclpy.spin()：它在 C 里阻塞，SIGTERM（tmux 关窗口、监视循环重起）
    # 要等到下一条消息才轮得到 Python 的处理器。这里自己转，最多晚 TICK_SEC。
    try:
        while rclpy.ok() and not stop:
            rclpy.spin_once(node, timeout_sec=TICK_SEC)
    finally:
        node.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
