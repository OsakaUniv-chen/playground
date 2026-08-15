#!/usr/bin/env python3
"""Boxie の BLE モータ（頭部 2 軸 + 両腕）。

頭部の指令元は PC-D の VLM（設計 §4.2）、腕は操作者のボタン。
roll 軸は使わない（機体にはあるが P3.2 では動かさない）。

BLE アダプタを複数プロセスで奪い合わないよう、頭部と腕を 1 ノードにまとめる。

topic:
    sub  <robot>/head/command   BoxieMotors   指令 [pitch, yaw] 度
    pub  <robot>/head/applied   BoxieMotors   clamp + smooth 後に実際に送った値
    pub  <robot>/head/current   BoxieMotors   モータから読んだ実測値（既定 20 Hz）
    sub  <robot>/arm/command    BoxieMotors   腕 [left, right] 度
    pub  <robot>/arm/current    BoxieMotors   腕の実測値
    pub  <robot>/status         BoxieStatus   接続状態

型は ~/ros2_ws の audio_common_msgs をそのまま使う（int16[3] = [pitch, yaw, roll]、
単位は度）。既存の boxie_node が使う `/boxie/boxie_command` 系とは topic を
分けてある。同時に起動すると両方がモータを掴みに行くので、**どちらか一方だけ**
動かすこと。

指令・実際に送った値・実測値を別 topic に分けているのは記録のため（設計 §5.5）。
可動域に当たった場面や smoothing の効き具合は、指令値だけでは分からない。

pykeigan が無い環境（実機に繋がっていない開発機）では自動的に模擬モードになる。
"""

import os
import threading
import time

import rclpy
from audio_common_msgs.msg import BoxieMotors, BoxieStatus
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

try:
    from pykeigan import blecontroller

    HAVE_PYKEIGAN = True
except ImportError:
    HAVE_PYKEIGAN = False


# ---- モータ ----

class MotorStub:
    """実機が無いときの代用。指令角へ 1 次遅れで近づく。"""

    def __init__(self, name, tau=0.15):
        self.name = name
        self.pos = 0.0
        self.target = 0.0
        self.tau = tau
        self.t_last = time.monotonic()

    def move_to_deg(self, deg):
        self.target = float(deg)

    def read_deg(self):
        now = time.monotonic()
        dt = now - self.t_last
        self.t_last = now
        self.pos += (self.target - self.pos) * min(1.0, dt / self.tau)
        return self.pos

    def close(self):
        pass


class KeiganMotor:
    """pykeigan の BLEController の薄いラッパ。"""

    def __init__(self, name, address, speed, accel, torque):
        self.name = name
        self.address = address
        self.speed = speed
        self.accel = accel
        self.torque = torque
        self.dev = None
        self.connect()

    def connect(self):
        self.dev = blecontroller.BLEController(self.address)
        self.dev.enable_action()
        self.dev.set_speed(self.speed)
        self.dev.set_acc(self.accel)
        self.dev.set_max_torque(self.torque)

    def move_to_deg(self, deg):
        from pykeigan import utils

        self.dev.move_to_pos(utils.deg2rad(float(deg)))

    def read_deg(self):
        from pykeigan import utils

        m = self.dev.read_motor_measurement()
        return utils.rad2deg(m.get("position", 0.0))

    def close(self):
        try:
            self.dev.disable_action()
        except Exception:
            pass


class HeadDriver(Node):
    HEAD_AXES = ("pitch", "yaw")          # roll は使わない
    ARM_AXES = ("left_arm", "right_arm")

    def __init__(self):
        super().__init__("head_driver")
        ns = "/" + os.environ.get("ROBOT_NAME", "robot")

        # ★ 安全側の可動域[度]。既存 boxie_node の既定値に合わせてある。
        #   実機で機体に当たらない範囲を確認して詰めること。
        self.declare_parameter("max_pitch", 30)
        self.declare_parameter("max_yaw", 60)
        # ★ 腕の可動域と、ボタンに割り当てる上げ／下げの角度。
        #   既存 boxie_node は 45 度を「下ろした位置」として初期化している。
        self.declare_parameter("max_arm", 90)
        self.declare_parameter("arm_up_deg", 0)
        self.declare_parameter("arm_down_deg", 45)
        # ★ BLE アドレス。`sudo hcitool lescan` か既存 boxie_node の設定から取る
        # 既定値は ~/ros2_ws の boxie_node の motor_defs から取った実機のもの
        self.declare_parameter("addr_pitch", os.environ.get(
            "ADDR_HEAD_PITCH", "f1:ae:4f:c1:57:c0"))
        self.declare_parameter("addr_yaw", os.environ.get(
            "ADDR_HEAD_YAW", "cd:93:a4:6f:be:9b"))
        self.declare_parameter("addr_left_arm", os.environ.get(
            "ADDR_LEFT_ARM", "c5:0b:cb:17:b4:a5"))
        self.declare_parameter("addr_right_arm", os.environ.get(
            "ADDR_RIGHT_ARM", "e5:c6:61:06:da:8f"))
        self.declare_parameter("speed", 20.0)
        self.declare_parameter("acceleration", 200.0)
        self.declare_parameter("torque", 0.2)
        self.declare_parameter("smooth_alpha", 0.6)
        self.declare_parameter("report_hz", 20.0)
        self.declare_parameter("reconnect_interval", 10.0)

        self.limits = {a: int(self.get_parameter(f"max_{a}").value)
                       for a in self.HEAD_AXES}
        arm_lim = int(self.get_parameter("max_arm").value)
        self.limits.update({a: arm_lim for a in self.ARM_AXES})
        self.alpha = float(self.get_parameter("smooth_alpha").value)
        self.smoothed = {a: 0.0 for a in self.HEAD_AXES + self.ARM_AXES}

        self.sub = self.create_subscription(
            BoxieMotors, f"{ns}/head/command", self.on_command, 10
        )
        self.pub_applied = self.create_publisher(BoxieMotors, f"{ns}/head/applied", 10)
        self.pub_current = self.create_publisher(BoxieMotors, f"{ns}/head/current", 10)
        self.pub_status = self.create_publisher(BoxieStatus, f"{ns}/status", 10)
        # --- 腕 ---
        self.sub_arm = self.create_subscription(
            BoxieMotors, f"{ns}/arm/command", self.on_arm_command, 10
        )
        self.pub_arm_current = self.create_publisher(
            BoxieMotors, f"{ns}/arm/current", 10
        )

        self.lock = threading.Lock()
        self.motors = {}
        self.connect_motors()

        hz = float(self.get_parameter("report_hz").value)
        self.create_timer(1.0 / hz, self.report_current)
        self.create_timer(
            float(self.get_parameter("reconnect_interval").value), self.health_check
        )
        self.get_logger().info(
            f"head_driver: 可動域(度) {self.limits} / "
            f"{'BLE' if HAVE_PYKEIGAN else '模擬モード'}"
        )

    # ---- 接続 ----

    def connect_motors(self):
        for axis in self.HEAD_AXES + self.ARM_AXES:
            addr = self.get_parameter(f"addr_{axis}").value
            if HAVE_PYKEIGAN and addr:
                try:
                    self.motors[axis] = KeiganMotor(
                        axis,
                        addr,
                        float(self.get_parameter("speed").value),
                        float(self.get_parameter("acceleration").value),
                        float(self.get_parameter("torque").value),
                    )
                    self.get_logger().info(f"BLE 接続 {axis}: {addr}")
                    continue
                except Exception as e:
                    self.get_logger().error(f"BLE 接続失敗 {axis} ({addr}): {e}")
            self.motors[axis] = MotorStub(axis)
        self.publish_status()

    def health_check(self):
        """繋がっていない軸があれば繋ぎ直す。

        BLE は現地の電波状況で落ちる。落ちたまま黙って動かないより、
        再接続を試み、状態を topic に出して記録に残すほうがよい。
        """
        if not HAVE_PYKEIGAN:
            return
        for axis, m in list(self.motors.items()):
            if isinstance(m, MotorStub):
                addr = self.get_parameter(f"addr_{axis}").value
                if not addr:
                    continue
                try:
                    with self.lock:
                        self.motors[axis] = KeiganMotor(
                            axis, addr,
                            float(self.get_parameter("speed").value),
                            float(self.get_parameter("acceleration").value),
                            float(self.get_parameter("torque").value),
                        )
                    self.get_logger().info(f"再接続 {axis}")
                    self.publish_status("reconnected")
                except Exception:
                    pass

    # ---- 指令 ----

    def on_command(self, msg: BoxieMotors):
        vals = list(msg.data)
        if len(vals) < 2:
            self.get_logger().warn(
                "BoxieMotors.data は [pitch, yaw]", throttle_duration_sec=5.0
            )
            return

        applied = []
        for axis, v in zip(self.HEAD_AXES, vals):   # roll は無視する
            lim = self.limits[axis]
            clamped = max(-lim, min(lim, float(v)))
            if abs(float(v)) > lim:
                self.get_logger().warn(
                    f"{axis} 指令 {v} 度が可動域 ±{lim} 外。丸める",
                    throttle_duration_sec=2.0,
                )
            # EMA で滑らかにする。VLM の出力が飛んだときに首が急に振れないように。
            self.smoothed[axis] = (
                self.alpha * clamped + (1.0 - self.alpha) * self.smoothed[axis]
            )
            deg = int(round(self.smoothed[axis]))
            applied.append(deg)
            try:
                with self.lock:
                    self.motors[axis].move_to_deg(deg)
            except Exception as e:
                self.get_logger().error(f"{axis} 送信失敗: {e}")
                with self.lock:
                    self.motors[axis] = MotorStub(axis)   # health_check が拾う
                self.publish_status("disconnected")

        self.publish_motors(self.pub_applied, applied)

    def _clamp(self, v, lim, label):
        """±lim 度に丸める。外に出た指令はログに残す。"""
        if abs(float(v)) > lim:
            self.get_logger().warn(
                f"{label} 指令 {v} 度が可動域 ±{lim} 外。丸める",
                throttle_duration_sec=2.0,
            )
        return max(-lim, min(lim, float(v)))

    def on_arm_command(self, msg: BoxieMotors):
        """腕 [left, right] を度で受ける。

        左右は機構的に鏡像なので、既存 boxie_node と同じく左は符号を反転して
        送る。指令はボタン由来で頻度が低いため smoothing は掛けない。
        """
        vals = list(msg.data)
        if len(vals) < 2:
            self.get_logger().warn("arm は [left, right]", throttle_duration_sec=5.0)
            return
        for axis, v, sign in zip(self.ARM_AXES, vals, (-1, 1)):
            deg = self._clamp(v, self.limits[axis], axis)
            self.smoothed[axis] = deg
            try:
                with self.lock:
                    self.motors[axis].move_to_deg(sign * deg)
            except Exception as e:
                self.get_logger().error(f"{axis} 送信失敗: {e}")
                with self.lock:
                    self.motors[axis] = MotorStub(axis)
        self.get_logger().info(f"arm -> {vals[:2]}")

    # ---- 実測 ----

    def report_current(self):
        def read(axes):
            out = []
            for a in axes:
                try:
                    with self.lock:
                        out.append(int(round(self.motors[a].read_deg())))
                except Exception:
                    out.append(0)
            return out

        self.publish_motors(self.pub_current, read(self.HEAD_AXES))
        self.publish_motors(self.pub_arm_current, read(self.ARM_AXES))

    def publish_motors(self, pub, vals):
        msg = BoxieMotors()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "head"
        msg.data = [int(v) for v in vals]
        pub.publish(msg)

    def publish_status(self, extra=None):
        n_all = len(self.HEAD_AXES) + len(self.ARM_AXES)
        connected = sum(1 for m in self.motors.values() if not isinstance(m, MotorStub))
        msg = BoxieStatus()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.status = extra or (
            "boxie_on" if connected == n_all else
            "boxie_off" if connected == 0 else "boxie_partial"
        )
        self.pub_status.publish(msg)

    def destroy_node(self):
        for m in self.motors.values():
            m.close()
        super().destroy_node()


def main():
    rclpy.init()
    node = HeadDriver()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
