#!/usr/bin/env python3
"""操作 UI サーバ（PC-C で動く）。

沿用元 reference code/teleop_interface/app.py からの変更点:
  - 機体ではなく PC-C で動かす。指令は DDS でネットワークを越える
  - MongoDB / pyindy を削除（発話系は音声そのままの伝送に置き換え）
  - `camera` コマンドを削除（頭部の指令元は PC-D の VLM）
  - `speak` を PTT（プッシュトゥトーク）に置き換え。押下区間を topic で残す
  - TLS を外す。ブラウザと同一機なので http://localhost が secure context になり
    Gamepad API とマイク取得の要件を満たす
  - robot_name を環境変数から読む（沿用元はソース直書き）

ブラウザ -> ここ: Socket.IO
ここ -> PC-B    : ROS 2 DDS
"""

import os
import threading

import rclpy
from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, emit
from geometry_msgs.msg import Twist
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from audio_common_msgs.msg import BoxieMotors
from std_msgs.msg import Bool, Int8

ROBOT_NAME = os.environ.get("ROBOT_NAME", "robot")
PORT = int(os.environ.get("UI_PORT", 7779))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("UI_SECRET", "p32-teleop")
socketio = SocketIO(app, cors_allowed_origins="*")


class TeleopPublisher(Node):
    def __init__(self):
        super().__init__("teleop_ui")
        ns = f"/{ROBOT_NAME}"
        self.pub_twist = self.create_publisher(Twist, f"{ns}/rover/twist", 10)
        # PTT 押下区間。発話がテキストで残らないぶん、これが
        # 「いつ喋ったか」のラベルになる（設計 §5.5）
        self.pub_ptt = self.create_publisher(Bool, f"{ns}/operator/ptt", 10)
        self.pub_button = self.create_publisher(Int8, f"{ns}/operator/button", 10)
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

    def send_ptt(self, pressed: bool):
        self.pub_ptt.publish(Bool(data=bool(pressed)))
        self.get_logger().info(f"PTT {'ON' if pressed else 'OFF'}")

    def send_button(self, index: int):
        self.pub_button.publish(Int8(data=int(index)))

    def send_arm(self, up: bool):
        deg = self.arm_up_deg if up else self.arm_down_deg
        msg = BoxieMotors()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "arm"
        msg.data = [deg, deg]        # 左右そろえて動かす
        self.pub_arm.publish(msg)
        self.get_logger().info(f"arm {'up' if up else 'down'} ({deg} deg)")


ros_node = None
controller_addr = None


@app.route("/")
def index():
    return render_template("base.html", robot_name=ROBOT_NAME)


@app.route("/status")
def status():
    return jsonify({"robot": ROBOT_NAME, "controller": controller_addr or ""})


@socketio.on("connect")
def on_connect():
    global controller_addr
    if controller_addr is None:
        controller_addr = request.remote_addr
        emit("connected", f"controller ({controller_addr})")
        print("controller connected:", controller_addr)
    else:
        emit("connected", f"viewer — {controller_addr} が操作中")
        print("viewer connected:", request.remote_addr)


@socketio.on("disconnect")
def on_disconnect():
    global controller_addr
    if controller_addr == request.remote_addr:
        controller_addr = None
        if ros_node is not None:
            ros_node.send_twist(0.0, 0.0)   # 切断時は必ず停止指令
            ros_node.send_ptt(False)
        print("controller disconnected")


@socketio.on("command")
def on_command(data):
    """ブラウザからの指令。

    排他制御はブラウザ相手にしか効かない。DDS 側では誰でも publish できるので、
    「誰の指令か」は記録側で区別する（設計 §5.5）。
    """
    if controller_addr != request.remote_addr:
        emit("command_ack", {"status": "occupied"})
        return
    try:
        typ = data.get("type", "")
        content = data.get("content", "")
        if typ == "twist":
            ros_node.send_twist(content[0], content[1])
        elif typ == "ptt":
            ros_node.send_ptt(bool(content))
        elif typ == "button_press":
            ros_node.send_button(int(content))
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
