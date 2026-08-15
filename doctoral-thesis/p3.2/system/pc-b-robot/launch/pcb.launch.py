"""PC-B の全プロセスを ros2 launch で起動する。

    ros2 launch pcb.launch.py                通常起動
    ros2 launch pcb.launch.py record:=true   収録も同時に開始
    ros2 launch pcb.launch.py fake:=false    実デバイスを使う

環境変数は先に env.sh で読んでおくこと:
    source env.sh && ros2 launch launch/pcb.launch.py

PC-B の起動はこれだけ。1 ノードだけ直しながら動かすときは、launch を止めて
そのノードを直接叩く。
"""

import os
from datetime import datetime

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROBOT = os.environ.get("ROBOT_NAME", "robot")
RECORD_DIR = os.environ.get("RECORD_DIR", os.path.expanduser("~/p32_bags"))
SPLIT_MB = int(os.environ.get("BAG_SPLIT_MB", 2048))

RECORD_TOPICS = [
    f"/{ROBOT}/camera/video",
    f"/{ROBOT}/onboard_mic/audio", f"/{ROBOT}/onboard_mic/info",
    f"/{ROBOT}/mic_array/audio", f"/{ROBOT}/mic_array/info",
    f"/{ROBOT}/soundmap/raw",
    f"/{ROBOT}/operator_mic/audio", f"/{ROBOT}/operator_mic/info",
    f"/{ROBOT}/rover/twist", f"/{ROBOT}/rover/action_sent",
    f"/{ROBOT}/head/command", f"/{ROBOT}/head/applied",
    f"/{ROBOT}/head/current", f"/{ROBOT}/head/status",
    f"/{ROBOT}/operator/ptt",
    f"/{ROBOT}/record/clock_offset",
]


def proc(name, script, respawn=True):
    """1 プロセス。respawn=True なら落ちても launch が上げ直す。"""
    return ExecuteProcess(
        cmd=["python3", "-u", os.path.join(HERE, script)],
        name=name,
        output="screen",
        respawn=respawn,
        respawn_delay=2.0,
    )


def generate_launch_description():
    record = LaunchConfiguration("record")
    session = datetime.now().strftime("%Y%m%d_%H%M%S")

    return LaunchDescription([
        DeclareLaunchArgument("record", default_value="false"),
        DeclareLaunchArgument("fake", default_value=os.environ.get("USE_FAKE_SOURCES", "1")),

        # --- センサ -> ROS ---
        proc("cam_bridge", "bridge/cam_bridge.py"),
        proc("soundmap_bridge", "bridge/soundmap_bridge.py"),
        proc("operator_mic_bridge", "bridge/operator_mic_bridge.py"),
        proc("clock_node", "bridge/clock_node.py"),

        # --- 指令 -> モータ ---
        proc("rover_driver", "driver/rover_driver.py"),
        proc("head_driver", "driver/head_driver.py"),

        # --- 収録 ---
        # respawn しない。収録が落ちたら気付けるようにする（黙って
        # 上げ直すと、その間のデータが抜けたことが分からなくなる）
        ExecuteProcess(
            condition=IfCondition(record),
            cmd=[
                "ros2", "bag", "record", "-s", "mcap",
                "-o", os.path.join(RECORD_DIR, session),
                "--max-bag-size", str(SPLIT_MB * 1024 * 1024),
                *RECORD_TOPICS,
            ],
            name="record",
            output="screen",
        ),
    ])
