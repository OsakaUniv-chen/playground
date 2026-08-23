#!/usr/bin/env python3
"""操作 UI 服务（跑在 rog-server 上）。

    tele-pc 的浏览器 ──HTTP──▶ 这里（发页面）
    tele-pc 的浏览器 ──WebRTC─▶ OME（收画面和声音图，**不经过这里**）
    这里 ──ROS/DDS──▶ robot-pc（操作指令）

搬自旧实现的 `pc-c-operator/app.py`（git `f91be30`）。**旧实现里它跑在操作者
自己的机器上，和 OME、浏览器三者同机**；现在按架构 §4 拆开了：页面由
rog-server 上的这个服务发，浏览器在 tele-pc 上，媒体流一点都不经过这里。

搬过来改了这些：

  - **OME 的地址不再是 localhost。** 浏览器和 OME 不在同一台机器上了，
    页面里那个 `ws://` 要指 `rog-server.local`（`OME_HOST_BROWSER`）。
  - **stream key 跟着改名**：画面取 `STREAM_KEY_VIEW`（`rgb_sm`），
    声音取 `STREAM_KEY_ONBOARDMIC`（`onboardmic`）。
  - **绑定地址**：旧实现绑死 127.0.0.1，因为浏览器就在同一台机器上。
    现在不行了，见下面 ★★ 那段。
  - **手臂指令暂时发不了**，见下面 ★ 那段。
  - 配置从 `common/config.env` ＋ `pc-c-operator/config.env` 两层，
    变成本目录一个 `config.env`（`common/` 那一层随四机结构一起没了）。

## 手柄能用（实测，别再听「secure context」那套）

先前这里写过「Gamepad API 只在 secure context 里可用，所以从 tele-pc 打开读不到
手柄」—— **那是错的**，源头是旧实现的一句注释。实测 Chrome 127 从
`http://192.168.1.100:8123`（非 localhost 的 http 源）：`isSecureContext` 是
`false`，但 `navigator.getGamepads` 照样是 function、调用不抛异常、返回 4 个
槽位，和 `http://localhost` 一模一样。**Gamepad API 没被挡。**（Firefox 没测。）

## 页面没有认证，而它能开动机体

绑在局域网上（tele-pc 要能打开），而 `UI_SECRET` 是 Flask 的会话密钥、不是门禁。
同一个 AP 上谁打开这个地址都能开车。现场是封闭的 AP，暂按可接受处理。

## ★ 手臂指令现在发不出去

旧实现用 `audio_common_msgs/BoxieMotors` 发手臂角度，那个类型来自旧实现搬来的
一份第三方消息包 —— 新结构里没有它（`teleop_msgs` 只有记录用的四个类型）。
而**整条操作指令通路本来就还没设计**（架构文档开头把它划在范围外）。

所以现在：`rover/twist` 用 `geometry_msgs/Twist`，到处都有，能发；
手臂那条**关着**（`ARM_ENABLE=0`），打开会在启动时明确报错告诉你缺什么。
"""

import os
import signal
import sys
import threading

from flask import Flask, render_template
from flask_socketio import SocketIO, emit

ROBOT_NAME = os.environ["ROBOT_NAME"]      # 不给默认值（config.env 必读）
PORT = int(os.environ["UI_PORT"])
BIND = os.environ["UI_BIND"]
ROS_ENABLE = os.environ["UI_ROS_ENABLE"] == "1"
ARM_ENABLE = os.environ["ARM_ENABLE"] == "1"

# 页面里那个 WebRTC 的地址。**这是给浏览器用的，不是给本进程用的** ——
# 浏览器在 tele-pc 上，所以要写 rog-server 的名字，不能写 localhost。
# （stream-server 自己的 config.env 里 OME_HOST 是 127.0.0.1，那是本机拉流用的，
# 两个不是一回事，别混。）
OME_WS = (f"ws://{os.environ['OME_HOST_BROWSER']}:{os.environ['OME_WS_PORT']}"
          f"/{os.environ['OME_APP']}/")
STREAM_KEY_VIEW = os.environ["STREAM_KEY_VIEW"]
STREAM_KEY_ONBOARDMIC = os.environ["STREAM_KEY_ONBOARDMIC"]

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ["UI_SECRET"]
socketio = SocketIO(app, cors_allowed_origins="*")

ros_node = None


class TeleopPublisher:
    """把浏览器的指令发成 ROS 消息。**按需 import rclpy**（UI_ROS_ENABLE=0 时
    这个类根本不建，页面照发，方便先调画面那半）。"""

    def __init__(self):
        import rclpy
        from rclpy.node import Node
        from geometry_msgs.msg import Twist

        self.arm_pub = None
        self.BoxieMotors = None
        if ARM_ENABLE:
            # ★ 见文件头：新结构里没有这个类型，打开就会在这里明确失败，
            # 而不是等按下按钮才静默地什么都不发生。
            try:
                from audio_common_msgs.msg import BoxieMotors
                self.BoxieMotors = BoxieMotors
            except ImportError:
                raise SystemExit(
                    "[error] ARM_ENABLE=1 但找不到 audio_common_msgs/BoxieMotors。\n"
                    "        那个类型来自旧实现搬来的第三方消息包，新结构里没有，\n"
                    "        而整条操作指令通路还没设计（见 README）。\n"
                    "        先用 ARM_ENABLE=0 跑，手臂那条等通路定了再说。"
                ) from None

        rclpy.init()
        self.node = Node("tele_server")
        ns = f"/{ROBOT_NAME}"
        self.pub_twist = self.node.create_publisher(Twist, f"{ns}/rover/twist", 10)
        self.Twist = Twist
        if self.BoxieMotors is not None:
            self.arm_pub = self.node.create_publisher(
                self.BoxieMotors, f"{ns}/arm/command", 10)
            self.arm_up_deg = int(os.environ["ARM_UP_DEG"])
            self.arm_down_deg = int(os.environ["ARM_DOWN_DEG"])
        self.node.get_logger().info(f"tele_server publishing under {ns}")

        from rclpy.executors import MultiThreadedExecutor
        ex = MultiThreadedExecutor()
        ex.add_node(self.node)
        threading.Thread(target=ex.spin, daemon=True).start()

    def send_twist(self, linear_x, angular_z):
        m = self.Twist()
        m.linear.x = float(linear_x)
        m.angular.z = float(angular_z)
        self.pub_twist.publish(m)

    def send_arm(self, up: bool):
        if self.arm_pub is None:
            return False
        deg = self.arm_up_deg if up else self.arm_down_deg
        m = self.BoxieMotors()
        m.header.stamp = self.node.get_clock().now().to_msg()
        m.header.frame_id = "arm"
        m.data = [deg, deg]          # 左右一起动
        self.arm_pub.publish(m)
        return True


@app.route("/")
def index():
    return render_template(
        "base.html",
        robot_name=ROBOT_NAME,
        ome_ws=OME_WS,
        stream_view=STREAM_KEY_VIEW,
        stream_onboardmic=STREAM_KEY_ONBOARDMIC,
        ros_enabled=ROS_ENABLE,
        arm_enabled=ARM_ENABLE,
    )


@socketio.on("connect")
def on_connect():
    emit("connected", f"connected to {ROBOT_NAME}"
                      + ("" if ROS_ENABLE else "（UI_ROS_ENABLE=0，指令不发出去）"))
    print("browser connected", flush=True)


@socketio.on("disconnect")
def on_disconnect():
    # 页面一关就发停止指令。**不指望浏览器那边的 watchdog。**
    if ros_node is not None:
        ros_node.send_twist(0.0, 0.0)
    print("browser disconnected", flush=True)


@socketio.on("command")
def on_command(data):
    try:
        typ = data.get("type", "")
        content = data.get("content", "")
        if ros_node is None:
            emit("command_ack", {"status": "disabled",
                                 "message": "UI_ROS_ENABLE=0"})
            return
        if typ == "twist":
            ros_node.send_twist(content[0], content[1])
        elif typ == "arm":
            if not ros_node.send_arm(content == "up"):
                emit("command_ack", {"status": "disabled",
                                     "message": "ARM_ENABLE=0"})
                return
        else:
            emit("command_ack", {"status": "unknown", "message": typ})
            return
        emit("command_ack", {"status": "ok"})
    except Exception as e:
        print("command error:", e, flush=True)
        emit("command_ack", {"status": "error", "message": str(e)})


def main():
    global ros_node
    if ROS_ENABLE:
        ros_node = TeleopPublisher()
    else:
        print("[warn] UI_ROS_ENABLE=0 —— 只发页面，指令不发出去", flush=True)

    # **自己抓信号，整个进程退掉。别去掉。**
    # 不加的话 `run_tele.sh stop`（SIGTERM）停不下来：
    #   - rcl 在 C 层抓着 SIGTERM，**只关掉 context，不结束进程**
    #     （Python 的 signal.getsignal 看不见它）
    #   - socketio.run 卡在 accept 循环里出不来
    #   - Werkzeug 的 serving 线程不是 daemon
    # 结果留下一个**「页面还回 200，但指令全部 publisher's context is invalid」**
    # 的空壳，还占着端口 —— 再起一次的话新的那个 Address already in use 死掉，
    # 空壳活着继续发页面。现场光看画面根本发现不了。
    # 要放在 rclpy.init() **之后**（放前面会被 rcl 覆盖）。
    def _bye(signum, _frame):
        print(f"signal {signum} を受けた。终止", flush=True)
        if ros_node is not None:
            try:
                ros_node.node.destroy_node()
            except Exception:
                pass
            try:
                import rclpy
                rclpy.try_shutdown()
            except Exception:
                pass
        sys.stdout.flush()
        os._exit(0)

    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)

    # 见文件头：这个页面没有认证却能开动机体。现场是封闭的 AP，暂按可接受处理。
    print(f"UI: http://{os.environ['OME_HOST_BROWSER']}:{PORT}/  "
          f"(robot={ROBOT_NAME}, bind={BIND})", flush=True)
    print("    从 tele-pc 的浏览器打开这个地址。手柄插在 tele-pc 上。", flush=True)
    if BIND != "127.0.0.1":
        print("[warn] 页面绑在 %s 上，且没有认证 —— 同一个网里的人都能开车。"
              % BIND, flush=True)
    socketio.run(app, host=BIND, port=PORT, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
