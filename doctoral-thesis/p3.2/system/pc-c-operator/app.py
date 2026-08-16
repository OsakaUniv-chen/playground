#!/usr/bin/env python3
"""操作 UI サーバ（PC-C で動く）。

沿用元 reference code/teleop_interface/app.py からの変更点:
  - 機体ではなく PC-C で動かす。指令は DDS でネットワークを越える
  - MongoDB / pyindy を削除（発話系は音声そのままの伝送に置き換え）
  - `camera` コマンドを削除（頭部の指令元は PC-D の VLM）
  - TLS を外す。ブラウザと同一機なので http://localhost が secure context になり
    Gamepad API とマイク取得の要件を満たす
  - robot_name を環境変数から読む（沿用元はソース直書き）

ブラウザ -> ここ: Socket.IO
ここ -> PC-B    : ROS 2 DDS
"""

import os
import threading

import rclpy
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
from geometry_msgs.msg import Twist
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from audio_common_msgs.msg import BoxieMotors

ROBOT_NAME = os.environ["ROBOT_NAME"]   # 既定値は置かない（env.sh 必須）
PORT = int(os.environ.get("UI_PORT", 7779))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("UI_SECRET", "p32-teleop")
socketio = SocketIO(app, cors_allowed_origins="*")


class TeleopPublisher(Node):
    def __init__(self):
        super().__init__("teleop_ui")
        ns = f"/{ROBOT_NAME}"
        self.pub_twist = self.create_publisher(Twist, f"{ns}/rover/twist", 10)
        # 腕の上げ下げ。角度は PC-B 側のパラメータで決まるので、
        # ここは「上げ／下げ」だけを送る。
        self.pub_arm = self.create_publisher(BoxieMotors, f"{ns}/arm/command", 10)
        self.arm_up_deg = int(os.environ.get("ARM_UP_DEG", 0))
        self.arm_down_deg = int(os.environ.get("ARM_DOWN_DEG", 45))
        self.get_logger().info(f"teleop_ui publishing under {ns}")

    def send_twist(self, linear_x, angular_z):
        msg = Twist()
        msg.linear.x = float(linear_x)
        msg.angular.z = float(angular_z)
        self.pub_twist.publish(msg)

    def send_arm(self, up: bool):
        deg = self.arm_up_deg if up else self.arm_down_deg
        msg = BoxieMotors()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "arm"
        msg.data = [deg, deg]        # 左右そろえて動かす
        self.pub_arm.publish(msg)
        self.get_logger().info(f"arm {'up' if up else 'down'} ({deg} deg)")


ros_node = None


@app.route("/")
def index():
    return render_template("base.html", robot_name=ROBOT_NAME)


@socketio.on("connect")
def on_connect():
    emit("connected", f"connected to {ROBOT_NAME}")
    print("browser connected")


@socketio.on("disconnect")
def on_disconnect():
    # 画面が閉じたら必ず停止指令。ブラウザ側の watchdog は当てにしない。
    if ros_node is not None:
        ros_node.send_twist(0.0, 0.0)
    print("browser disconnected")


@socketio.on("command")
def on_command(data):
    """ブラウザからの指令。"""
    try:
        typ = data.get("type", "")
        content = data.get("content", "")
        if typ == "twist":
            ros_node.send_twist(content[0], content[1])
        elif typ == "arm":
            ros_node.send_arm(content == "up")
        else:
            emit("command_ack", {"status": "unknown", "message": typ})
            return
        emit("command_ack", {"status": "ok"})
    except Exception as e:
        print("command error:", e)
        emit("command_ack", {"status": "error", "message": str(e)})


def main():
    global ros_node
    rclpy.init()
    ros_node = TeleopPublisher()
    executor = MultiThreadedExecutor()
    executor.add_node(ros_node)
    threading.Thread(target=executor.spin, daemon=True).start()

    # TLS 無し。ブラウザは同一機から http://localhost:PORT/ で開くこと。
    # 別機から開くと secure context を失い Gamepad API が動かない。
    print(f"UI: http://localhost:{PORT}/   (robot={ROBOT_NAME})")
    socketio.run(app, host="0.0.0.0", port=PORT, allow_unsafe_werkzeug=True)

    ros_node.destroy_node()
    rclpy.try_shutdown()


if __name__ == "__main__":
    main()
