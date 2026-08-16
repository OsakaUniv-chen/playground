#!/usr/bin/env python3
"""Twist -> Keigan ALI。沿用元の方式をそのまま使う。

経路（沿用元と同じ）:
    ゲームパッド -> app.py -> <robot>/rover/twist -> ここ -> MQTT control/joy -> ALI

沿用元 reference code/blr/rover/ の構成:
    joy2twist.py     Joy のスティック生値を Twist に載せる（加工しない）
    twist2alimove.py Twist を 8 方向の離散 action に量子化し MQTT へ
こちらは joy2twist の役割を app.py が担い、この節が twist2alimove に当たる。
量子化の対応表と MQTT の topic 名は沿用元のまま。

足したのは watchdog だけ。指令が無線を越える構成では、リンクが切れた瞬間に
「止まれ」も届かず、台車は最後の指令のまま走り続ける。沿用元にも停止を送る
`scheduler()` はあったが、どこからも呼ばれていなかった。

速度は ALI 側で決まる。8 方向の離散指令なので、こちら側で値を掛けても
方向が変わらないだけで速度には効かない（速度上限は ALI の設定で絞る）。

MQTT が繋がっていなくてもノードは動く（ログのみ）。
"""

import os
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from geometry_msgs.msg import Twist
from rclpy.node import Node

try:
    import paho.mqtt.client as mqtt

    HAVE_MQTT = True
except ImportError:
    HAVE_MQTT = False

# 8 方向 + 停止。沿用元の対応表そのまま。
#   1:前  2:前左  3:左  4:後左  5:後  6:後右  7:右  8:前右  0:停止
ACTION_TABLE = {
    (1, 0): 1, (1, -1): 2, (0, -1): 3, (-1, -1): 4,
    (-1, 0): 5, (-1, 1): 6, (0, 1): 7, (1, 1): 8,
    (0, 0): 0,
}


class RoverDriver(Node):
    def __init__(self):
        super().__init__("rover_driver")
        ns = "/" + os.environ["ROBOT_NAME"]

        # 既定値は置かない（env.sh 必須）。config.env と二重に持つと片方だけ古くなる。
        self.declare_parameter("mqtt_host", os.environ["ALI_MQTT_HOST"])
        self.declare_parameter("mqtt_port", int(os.environ["ALI_MQTT_PORT"]))
        self.declare_parameter("watchdog_sec", 0.5)
        self.declare_parameter("deadzone", 0.5)   # 沿用元と同じ閾値
        self.declare_parameter("use_mqtt", True)

        self.watchdog_sec = self.get_parameter("watchdog_sec").value
        self.deadzone = self.get_parameter("deadzone").value

        self.sub = self.create_subscription(
            Twist, f"{ns}/rover/twist", self.on_twist, 10
        )
        self.last_cmd_time = None
        self.last_action = None
        self.mqtt = None
        if HAVE_MQTT and self.get_parameter("use_mqtt").value:
            self._connect_mqtt()
        else:
            self.get_logger().warn("MQTT 無効。action はログのみ")

        self.create_timer(0.1, self.watchdog)
        self.get_logger().info(
            f"rover_driver: watchdog={self.watchdog_sec}s deadzone={self.deadzone}"
        )

    def _connect_mqtt(self):
        host = self.get_parameter("mqtt_host").value
        port = self.get_parameter("mqtt_port").value
        try:
            self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
            self.mqtt.connect(host, port, 60)
            self.mqtt.loop_start()
            self.get_logger().info(f"MQTT 接続: {host}:{port}")
        except Exception as e:
            self.mqtt = None
            self.get_logger().warn(f"MQTT 接続失敗 ({host}:{port}): {e}")

    def on_twist(self, msg: Twist):
        a = msg.linear.x
        s = msg.angular.z
        accel = 0 if abs(a) < self.deadzone else (1 if a > 0 else -1)
        steer = 0 if abs(s) < self.deadzone else (1 if s > 0 else -1)
        self.last_cmd_time = time.monotonic()
        self._send_action(ACTION_TABLE.get((accel, steer), 0))

    def watchdog(self):
        """最後の指令から watchdog_sec 経過したら停止させる。

        DDS の subscribe は「来なくなった」だけでは止まらない。リンクが
        切れた場合、これが無いと台車は最後の指令のまま走り続ける。
        """
        if self.last_cmd_time is None:
            return
        if time.monotonic() - self.last_cmd_time > self.watchdog_sec:
            if self.last_action not in (0, None):
                self.get_logger().warn("watchdog: 指令が途絶えたので停止")
            self._send_action(0)
            self.last_cmd_time = None

    def _send_action(self, action: int):
        if action != self.last_action:
            self.get_logger().info(f"action -> {action}")
        self.last_action = action
        if self.mqtt is not None:
            try:
                self.mqtt.publish("control/joy", '{"data":%d}' % action)
            except Exception as e:
                self.get_logger().error(f"MQTT publish 失敗: {e}")

    def destroy_node(self):
        self._send_action(0)
        if self.mqtt is not None:
            self.mqtt.loop_stop()
        super().destroy_node()


def main():
    rclpy.init()
    node = RoverDriver()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
