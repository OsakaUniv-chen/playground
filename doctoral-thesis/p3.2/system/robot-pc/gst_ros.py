#!/usr/bin/env python3
"""记录那一半的公共部分：起 ROS 节点、发消息、把 gst 的时刻换成 UNIX 时间。

**推流不依赖 ROS。** 只有记录（`--publish`）才用得着这里，所以各脚本都是
按需 import 的 —— 没装 ROS 的机器上不加 `--publish` 照样推流，
`soundmap.py --print-caps` 也照样能在启动时回答 caps。

时刻的算法只有这一份（`unix_ns`），三个发布端都用它。写两份必然会走样，
而走样的表现是"bag 里各路对不齐"，事后极难查。

**★ 大数组字段一定要传 `array.array`，不要传 `bytes` / `list`。**
rosidl 的 `uint8[]` setter 拿到 `bytes` 会逐字节检查一遍：14 KB 一条要 750 us，
100 msg/s 就是 8.6% 一个核；给 `array.array("B", data)` 走缓冲区快路，4.7 us
（实测 158 倍，内容完全一样）。`float32[]` 同理：`tolist()` 318 us，
`array.array("f").frombytes(np_f32.tobytes())` 4.6 us。视频一帧 33 KB × 90 fps
踩上去就是白扔 20% 一个核。

**★ 建节点要在 `import gi` 之前。** 实测（Humble + numpy 1.x）：进程里先有
numpy、再 import gi、然后建 rclpy 节点 —— **直接 abort，一行错误信息都没有**。
三者缺任何一个都不复现，节点建在 gi 之前也不复现。所以 soundmap.py 那种
带 numpy 的发布端，`_ros_setup()` 必须放在 `import gi` 前面。
"""
from __future__ import annotations

import threading
import time


def unix_ns(pipeline, sample, buf, warn=None) -> int:
    """gst 的 buffer 时刻 -> UNIX 纳秒。

        unix = running_time(pts) + base_time + (REALTIME - MONOTONIC)

    **这是采集时刻，不是发布时刻。** 从采到发中间隔着编码和排队，几十毫秒
    起步；用发布时刻的话，各路的偏差还各不相同（视频比音频晚得多），
    bag 里就再也对不齐了。

    **管线时钟保持 MONOTONIC**（gst 默认）。切成 REALTIME 的话 NTP 一 step
    PTS 就跳变，那才是真的没救。这里只在换算的最后一步加上那个差值，
    差值本身以 1 Hz 记进 bag（record/clock_offset），事后能看出哪里 step 过。
    """
    base = pipeline.get_base_time()
    offset = time.clock_gettime_ns(time.CLOCK_REALTIME) - \
        time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    if buf.pts is None or base is None:
        if warn:
            warn("buffer 没有 PTS，退回当前时刻")
        return time.clock_gettime_ns(time.CLOCK_REALTIME)
    seg = sample.get_segment()
    running = seg.to_running_time(seg.format, buf.pts) if seg is not None else None
    if running is None:
        if warn:
            warn("PTS 换不成 running_time，退回当前时刻")
        return time.clock_gettime_ns(time.CLOCK_REALTIME)
    return int(running) + int(base) + offset


class RosPub:
    """一个进程一个节点，按 topic 建 publisher。

    `rclpy.init()` 一个进程只能做一次，所以这个类也是一个进程一个。
    """

    def __init__(self, node_name: str, robot: str, log=print):
        import sys

        # 上面那条顺序规则一旦被破坏，表现是无声 abort，谁也查不出来。
        # 这里在崩之前先说一句 —— 只在真的危险的组合下响。
        if "gi" in sys.modules and "numpy" in sys.modules:
            log("warn", "★ numpy 和 gi 都已经载入了才建 ROS 节点 —— "
                        "这个组合实测会直接 abort。把建节点挪到 import gi 之前")

        import rclpy
        from rclpy.node import Node

        self.rclpy = rclpy
        # **不让 rclpy 接管信号。** 它默认会装 SIGINT/SIGTERM 的处理器，那个
        # 处理器只是关掉 ROS context，**不会让进程退出** —— 于是推流进程收到
        # 监视循环发的 TERM 之后照样活着，永远不重起（实测：timeout 12 之后
        # 进程还在）。关掉之后信号回到进程本来的行为。
        try:
            from rclpy.signals import SignalHandlerOptions
            rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
        except (ImportError, TypeError):        # 老版本没有这个参数
            rclpy.init()
        self.node = Node(node_name)
        self.robot = robot
        self.log = log
        self._pubs = {}

        # **纯 publisher 不 spin 也能发**，但不 spin 的节点在 `ros2 node list`
        # 里不出现、`ros2 topic info` 也看不到。现场排查"到底发没发"全靠这两个
        # 命令，所以起个后台线程转着。
        self._t = threading.Thread(target=self._spin, daemon=True)
        self._t.start()

    def _spin(self):
        try:
            self.rclpy.spin(self.node)
        except Exception:
            pass                    # shutdown 的时候会抛，正常

    def publisher(self, topic: str, msg_type, depth: int = 10):
        """/<ROBOT_NAME>/<topic> 上的 publisher。depth 要和 recorder 那边配得上。"""
        full = f"/{self.robot}/{topic}"
        if full not in self._pubs:
            self._pubs[full] = self.node.create_publisher(msg_type, full, depth)
            self.log("info", f"[ros] 发布 {full}  ({msg_type.__name__})")
        return self._pubs[full]

    @staticmethod
    def stamp(msg, ns: int):
        """把 UNIX 纳秒写进消息的时刻字段。

        两种形状都要认：teleop_msgs 用 `std_msgs/Header`，而 **foxglove 的消息
        没有 header**，时刻直接叫 `timestamp`（CompressedVideo 就是这样）。
        """
        t = msg.header.stamp if hasattr(msg, "header") else msg.timestamp
        t.sec = ns // 1_000_000_000
        t.nanosec = ns % 1_000_000_000
        return msg

    def shutdown(self):
        """**先让 spin 线程退出，再拆节点。**

        反过来的话进程退出时会 `terminate called without an active exception`
        然后 core dump —— C++ 那边的线程还活着就被析构了。实测 cam.py 每次
        正常退出都会 dump 一个 core，日志里全是 GStreamer 的 CRITICAL，
        真的崩了反而看不出来。
        """
        try:
            self.rclpy.try_shutdown()       # spin() 会因此返回
        except Exception:
            pass
        if self._t.is_alive():
            self._t.join(timeout=2.0)
        try:
            self.node.destroy_node()
        except Exception:
            pass
