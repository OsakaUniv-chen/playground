#!/usr/bin/env python3
"""頭部の向きを決めて PC-B へ publish する（設計 §4.2）。

現状は骨格だけ。VLM も音響マップもまだ繋いでいないので、既定では
何も出さない（--demo で正弦波を出して経路だけ確認できる）。

pub: <robot>/head/command (BoxieMotors)  [pitch, yaw, roll] 単位は度
     PC-B の head_driver が購読し、可動域制限と smoothing を掛けて
     BLE でモータを回す。可動域は PC-B 側で掛かるので、こちらは
     制限を知らずに指令を出してよい。

記録の観点（設計 §5.5）:
    ここが出す指令は「モデルの判断」。操作者がゲームパッドで出す台車指令とは
    別 topic なので、bag の中で自然に区別できる。両方を混ぜないこと。
"""

import argparse
import math
import os
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from audio_common_msgs.msg import BoxieMotors


class HeadController(Node):
    def __init__(self, demo=False):
        super().__init__("head_controller")
        self.robot = os.environ.get("ROBOT_NAME", "robot")
        self.pub = self.create_publisher(
            BoxieMotors, f"/{self.robot}/head/command", 10
        )
        self.demo = demo
        self.t0 = time.monotonic()
        if demo:
            self.create_timer(0.1, self.tick_demo)   # 10 Hz
            self.get_logger().warn("--demo: 正弦波を出している（経路確認用）")
        else:
            self.get_logger().info(
                "待機中。VLM を繋いだら decide() から publish_goal() を呼ぶ"
            )

    def publish_goal(self, pitch_deg, yaw_deg, roll_deg=0):
        """[pitch, yaw, roll] を度で送る。"""
        msg = BoxieMotors()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "head"
        msg.data = [int(round(pitch_deg)), int(round(yaw_deg)), int(round(roll_deg))]
        self.pub.publish(msg)

    def tick_demo(self):
        t = time.monotonic() - self.t0
        # 可動域 (pitch ±30, yaw ±60) の内側で振る
        self.publish_goal(15 * math.sin(t * 0.3), 40 * math.sin(t * 0.5))

    # ---- ここから先が本体。未実装 ----

    def decide(self, image, acoustic_map):
        """場面から「誰に向くか」を決める。

        入力は `gst/recv_ome.py` の `OmeInputs` から取る:
            inp.latest_video("stream")   場面
            inp.latest_video("soundmap") 誰が喋っているか
            inp.latest_audio("mic")      現場の音

        TODO:
          - 音響マップをそのまま VLM に食わせるか、前段で言語化するか
          - 推論周期と許容遅延（feasibility study なのでまず結果が出るか）
        """
        raise NotImplementedError


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="正弦波を出して経路を確認する")
    args, _ = ap.parse_known_args()

    rclpy.init()
    node = HeadController(demo=args.demo)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
