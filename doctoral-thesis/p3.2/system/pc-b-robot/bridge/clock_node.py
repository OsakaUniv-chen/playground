#!/usr/bin/env python3
"""CLOCK_MONOTONIC と CLOCK_REALTIME の対応を 1 Hz で bag に残す。

gst の PTS は MONOTONIC 基準なので、UNIX 時間への換算に offset を使う。
offset は NTP の frequency slewing 中はほぼ一定だが、step が入ると飛ぶ。
系列で残しておけば、収録中に時計が動いても事後に補間で直せる。

収録後の確認: offset 系列が滑らかなら換算は信用できる。段差があれば
その時刻の前後で補正が要る。
"""

import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.time import Time

from p3_msgs.msg import ClockOffset

from gst_ros_common import robot_ns


class ClockNode(Node):
    def __init__(self):
        super().__init__("clock_node")
        self.pub = self.create_publisher(
            ClockOffset, f"{robot_ns()}/record/clock_offset", 10
        )
        self.prev_offset = None
        self.create_timer(1.0, self.tick)
        self.get_logger().info("clock_offset を 1 Hz で publish")

    def tick(self):
        mono = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        real = time.clock_gettime_ns(time.CLOCK_REALTIME)
        offset = real - mono

        msg = ClockOffset()
        msg.header.stamp = Time(nanoseconds=real).to_msg()
        msg.monotonic_ns = mono
        msg.realtime_ns = real
        msg.offset_ns = offset
        self.pub.publish(msg)

        # 1 秒で 1 ms 以上動いたら NTP が step した可能性が高い
        if self.prev_offset is not None:
            jump = abs(offset - self.prev_offset)
            if jump > 1_000_000:
                self.get_logger().warn(
                    f"clock offset が {jump/1e6:.1f} ms 飛んだ（NTP step の疑い）"
                )
        self.prev_offset = offset


def main():
    rclpy.init()
    node = ClockNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
