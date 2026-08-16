#!/usr/bin/env python3
"""Boxie の BLE モータ 5 個（頭部 pitch/yaw/roll + 両腕）。

頭部の指令元は PC-D の VLM、腕は操作者のボタン。

**roll は動かさないが、通電はする。** 5 個すべてに enable_action を掛け、
roll は起動時の位置（preset_position で 0 とした点）へ保持し続ける。
通電しないとトルクが掛からず、**首が左右に揺れる**ため。

BLE アダプタを複数プロセスで奪い合わないよう、頭部と腕を 1 ノードにまとめる。

topic:
    sub  <robot>/head/command   BoxieMotors   指令 [pitch, yaw] 度
    sub  <robot>/arm/command    BoxieMotors   腕 [left, right] 度
    pub  <robot>/keigan_motor/status  BoxieStatus  接続状態

**関節角の読み戻しは記録しない。** BLE の帯域が乏しく、定期的に角度を
読むと指令の送信を圧迫するため。記録に残るのは指令だけで、可動域制限と
smoothing の効きは、ここのパラメータから事後に再現する。

ただし **10 秒に 1 回、生存確認として `read_motor_measurement()` を叩く**
（5 個で 0.5 回/秒。既存 boxie_node の check_motor_health と同じやり方）。
落ちた軸は繋ぎ直し、結果を毎回 status に出す。**変化時だけでなく毎回出す**
ので、bag には必ず 10 秒おきの状態が残る。

型は ~/ros2_ws の audio_common_msgs をそのまま使う（int16[3] = [pitch, yaw, roll]、
単位は度）。既存の boxie_node が使う `/boxie/boxie_command` 系とは topic を
分けてある。同時に起動すると両方がモータを掴みに行くので、**どちらか一方だけ**
動かすこと。

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
    """実機が無いときの代用。指令を受け取って捨てるだけ。"""

    def __init__(self, name):
        self.name = name

    def move_to_deg(self, deg):
        pass

    def probe(self):
        pass

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
        """既存 boxie_node の _connect_single_motor と同じ手順で初期化する。

        preset_position(0) が要点 — 「いま居る位置」を 0 度と定義する。
        可動域制限（±30 / ±60 度）はこの 0 を基準に効くので、これが無いと
        モータ内部の絶対原点を基準に動いてしまう。
        """
        self.dev = blecontroller.BLEController(self.address)
        self.dev.enable_action()
        time.sleep(0.1)
        self.dev.reset_all_pid()
        self.dev.reset_all_registers()
        self.dev.enable_continual_motor_measurement()
        self.dev.set_acc(self.accel)
        self.dev.set_dec(self.accel)
        self.dev.set_max_torque(self.torque)
        self.dev.set_speed(self.speed)
        self.dev.set_curve_type(0)
        self.dev.preset_position(0)

    def move_to_deg(self, deg):
        from pykeigan import utils

        self.dev.move_to_pos(utils.deg2rad(float(deg)))

    def probe(self):
        """生存確認。繋がっていなければ例外が出る。角度は使わない。"""
        self.dev.read_motor_measurement()

    def close(self):
        try:
            self.dev.disable_action()
        except Exception:
            pass


class HeadDriver(Node):
    HEAD_AXES = ("pitch", "yaw")          # 指令を受けて動かす
    HOLD_AXES = ("roll",)                 # 通電して初期位置に保つだけ
    ARM_AXES = ("left_arm", "right_arm")
    ALL_AXES = HEAD_AXES + HOLD_AXES + ARM_AXES   # 通電する 5 個

    def __init__(self):
        super().__init__("head_driver")
        ns = "/" + os.environ["ROBOT_NAME"]

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
        self.declare_parameter("addr_roll", os.environ.get(
            "ADDR_HEAD_ROLL", "ec:64:f6:ba:ca:7e"))
        self.declare_parameter("addr_left_arm", os.environ.get(
            "ADDR_LEFT_ARM", "c5:0b:cb:17:b4:a5"))
        self.declare_parameter("addr_right_arm", os.environ.get(
            "ADDR_RIGHT_ARM", "e5:c6:61:06:da:8f"))
        self.declare_parameter("speed", 20.0)
        self.declare_parameter("acceleration", 200.0)
        self.declare_parameter("torque", 0.2)
        self.declare_parameter("smooth_alpha", 0.6)
        # 生存確認と status 送出の周期。5 個 / 10 s なので BLE への負荷は無視できる
        self.declare_parameter("health_check_interval", 10.0)

        self.limits = {a: int(self.get_parameter(f"max_{a}").value)
                       for a in self.HEAD_AXES}
        arm_lim = int(self.get_parameter("max_arm").value)
        self.limits.update({a: arm_lim for a in self.ARM_AXES})
        self.alpha = float(self.get_parameter("smooth_alpha").value)
        self.smoothed = {a: 0.0 for a in self.HEAD_AXES + self.ARM_AXES}

        self.sub = self.create_subscription(
            BoxieMotors, f"{ns}/head/command", self.on_command, 10
        )
        self.pub_status = self.create_publisher(
            BoxieStatus, f"{ns}/keigan_motor/status", 10
        )
        # --- 腕 ---
        self.sub_arm = self.create_subscription(
            BoxieMotors, f"{ns}/arm/command", self.on_arm_command, 10
        )
        self.lock = threading.Lock()
        self.motors = {}
        self.connect_motors()

        self.create_timer(
            float(self.get_parameter("health_check_interval").value),
            self.health_check,
        )
        self.get_logger().info(
            f"head_driver: 可動域(度) {self.limits} / "
            f"{'BLE' if HAVE_PYKEIGAN else '模擬モード'}"
        )

    # ---- 接続 ----

    def _new_motor(self, axis):
        """1 軸を繋いで初期化する。失敗したら例外。"""
        return KeiganMotor(
            axis,
            self.get_parameter(f"addr_{axis}").value,
            float(self.get_parameter("speed").value),
            float(self.get_parameter("acceleration").value),
            float(self.get_parameter("torque").value),
        )

    def connect_motors(self):
        """5 軸すべてを繋ぐ。roll も含めて通電する。"""
        for axis in self.ALL_AXES:
            addr = self.get_parameter(f"addr_{axis}").value
            if HAVE_PYKEIGAN and addr:
                try:
                    self.motors[axis] = self._new_motor(axis)
                    self.get_logger().info(f"BLE 接続 {axis}: {addr}")
                    continue
                except Exception as e:
                    self.get_logger().error(f"BLE 接続失敗 {axis} ({addr}): {e}")
            self.motors[axis] = MotorStub(axis)
        self._hold_uncontrolled()
        self.publish_status()

    def _hold_uncontrolled(self):
        """roll を初期位置（preset_position で 0 とした点）に保持する。

        指令は一切受け付けないが、通電したまま 0 を指示し続けることで
        トルクが掛かり、首が揺れなくなる。
        """
        for axis in self.HOLD_AXES:
            try:
                with self.lock:
                    self.motors[axis].move_to_deg(0)
            except Exception as e:
                self.get_logger().error(f"{axis} の保持に失敗: {e}")

    def health_check(self):
        """10 秒ごとに全軸の生存を確かめ、落ちていれば繋ぎ直す。

        既存 boxie_node の check_motor_health と同じで、`read_motor_measurement()`
        が例外を出すかどうかで判定する。BLE は現地の電波状況で落ちるので、
        落ちたまま黙って動かないより、繋ぎ直して状態を残すほうがよい。

        **status は毎回出す。** 変化時だけだと、順調なときに bag へ 1 件も
        残らず「繋がっていた」ことを事後に確かめられなくなる。
        """
        if not HAVE_PYKEIGAN:
            self.publish_status()
            return

        reconnected = False
        for axis, m in list(self.motors.items()):
            if not isinstance(m, MotorStub):
                try:
                    with self.lock:
                        m.probe()
                    continue
                except Exception as e:
                    self.get_logger().warn(f"{axis} が応答しない: {e}")
                    with self.lock:
                        m.close()
                        self.motors[axis] = MotorStub(axis)
            # ここに来るのは stub（元から／たった今落ちた）だけ
            if not self.get_parameter(f"addr_{axis}").value:
                continue
            try:
                with self.lock:
                    self.motors[axis] = self._new_motor(axis)
                self.get_logger().info(f"再接続 {axis}")
                reconnected = True
            except Exception:
                pass
        if reconnected:
            self._hold_uncontrolled()
        self.publish_status()

    # ---- 指令 ----

    def on_command(self, msg: BoxieMotors):
        vals = list(msg.data)
        if len(vals) < 2:
            self.get_logger().warn(
                "BoxieMotors.data は [pitch, yaw]", throttle_duration_sec=5.0
            )
            return

        for axis, v in zip(self.HEAD_AXES, vals):   # roll は無視する
            clamped = self._clamp(v, self.limits[axis], axis)
            # EMA で滑らかにする。VLM の出力が飛んだときに首が急に振れないように。
            self.smoothed[axis] = (
                self.alpha * clamped + (1.0 - self.alpha) * self.smoothed[axis]
            )
            deg = int(round(self.smoothed[axis]))
            try:
                with self.lock:
                    self.motors[axis].move_to_deg(deg)
            except Exception as e:
                self.get_logger().error(f"{axis} 送信失敗: {e}")
                with self.lock:
                    self.motors[axis] = MotorStub(axis)   # health_check が拾う
                self.publish_status("disconnected")

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

    def publish_status(self, extra=None):
        n_all = len(self.ALL_AXES)
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
