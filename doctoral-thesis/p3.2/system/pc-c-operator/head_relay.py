#!/usr/bin/env python3
"""PC-D の頭部指令を受けて ROS へ流し直す中継（設計 §4.2）。

    PC-D (理研) ── TCP/JSON ──> ここ ── ROS publish ──> PC-B の head_driver

**なぜ DDS を直接跨がせないか。** PC-D は理研にあり、PC-C も PC-D も
着信ポートを持たない。DDS の discovery はマルチキャストで、ユニキャストに
固定しても NAT の穴あけが要る。さらに PC-D は galactic、PC-B / PC-C は
humble で既定の RMW が違い（CycloneDDS / FastDDS）、distro 間通信は
公式には非対応。跨ぐのは 10 Hz・整数 3 個の topic 1 本なので、
それだけのために DDS を通すより素の TCP のほうが確実で、
**PC-D 側に ROS が要らなくなる**。

経路は PC-C から張った SSH トンネル（`-R`）の上に乗る。PC-D から見ると
このサーバが自分の localhost に居るように見える。**そのため既定では
127.0.0.1 にだけ bind する**（トンネルの出口以外から触らせない）。

プロトコルは 1 行 1 指令の JSON:

    {"pitch": 12.0, "yaw": -30.0, "roll": 0.0}\\n

`roll` は省略可。単位は度。可動域制限は PC-B の head_driver が掛けるので、
ここでは値をそのまま渡す。
"""

import json
import os
import socket
import threading

import rclpy
from audio_common_msgs.msg import BoxieMotors
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

ROBOT_NAME = os.environ.get("ROBOT_NAME", "robot")
PORT = int(os.environ.get("HEAD_RELAY_PORT", 7997))
BIND = os.environ.get("HEAD_RELAY_BIND", "127.0.0.1")


class HeadRelay(Node):
    def __init__(self):
        super().__init__("head_relay")
        self.pub = self.create_publisher(
            BoxieMotors, f"/{ROBOT_NAME}/head/command", 10
        )
        self.n = 0
        self.peer = None
        self.create_timer(10.0, self._report)

        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind((BIND, PORT))
        self.srv.listen(1)
        threading.Thread(target=self._accept_loop, daemon=True).start()
        self.get_logger().info(
            f"頭部指令を待つ: tcp://{BIND}:{PORT} -> /{ROBOT_NAME}/head/command"
        )

    def _accept_loop(self):
        while True:
            try:
                conn, addr = self.srv.accept()
            except OSError:
                return
            self.peer = addr
            self.get_logger().info(f"PC-D が繋がった: {addr}")
            try:
                self._serve(conn)
            except Exception as e:
                self.get_logger().warn(f"接続が切れた: {e}")
            finally:
                conn.close()
                self.peer = None
                self.get_logger().info("PC-D が切れた。次の接続を待つ")

    def _serve(self, conn):
        conn.settimeout(None)
        buf = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                return                      # 相手が閉じた
            buf += chunk
            # 行が溜まっていなければ次を待つ。1 行 1 指令。
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if line:
                    self._handle(line)

    def _handle(self, line):
        try:
            d = json.loads(line.decode())
        except Exception as e:
            self.get_logger().warn(f"解析できない行を捨てた: {e}",
                                   throttle_duration_sec=5.0)
            return
        msg = BoxieMotors()
        # 到着時刻を打つ。PC-D の時計とは合っていないので、向こうの
        # 送信時刻は使わない（記録の基準時計は PC-B、設計 §5.2）。
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "head"
        try:
            msg.data = [
                int(round(float(d.get("pitch", 0)))),
                int(round(float(d.get("yaw", 0)))),
                int(round(float(d.get("roll", 0)))),
            ]
        except (TypeError, ValueError) as e:
            self.get_logger().warn(f"値が数値でない: {e}", throttle_duration_sec=5.0)
            return
        self.pub.publish(msg)
        self.n += 1

    def _report(self):
        state = f"接続中 {self.peer}" if self.peer else "未接続"
        self.get_logger().info(f"head/command {self.n} msg (10s) — {state}")
        self.n = 0

    def destroy_node(self):
        try:
            self.srv.close()
        except Exception:
            pass
        super().destroy_node()


def main():
    rclpy.init()
    node = HeadRelay()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
