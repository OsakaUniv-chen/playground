"""PC-B の全プロセスを ros2 launch で起動する。

**普段は ./run.sh を使う**（env.sh の読み込みごと 1 本にまとめてある）。
ここを直接叩くのは、env.sh を既に読んだ shell で試すときだけ:

    source env.sh
    ros2 launch pcb.launch.py                 収録込みで起動（record 既定 true）
    ros2 launch pcb.launch.py record:=false   収録せずに起動

収録先は RECORD_DIR/<起動時刻>（既定 ~/p32/rosbags/YYYYMMDD_HHMMSS）。
一覧と実際の書き出しは record.sh が持つ。

**実機かフェイクかは common/config.env の USE_FAKE_SOURCES だけで決まる**
（既定 0 = 実デバイス）。起動時に何かを足す必要は無い。`fake:=true` /
`fake:=false` も受けるが、これは 1 回だけ逆を試したいときの逃げ道で、
書かなければ config.env の値がそのまま使われる。

PC-B の起動はこれだけ。1 ノードだけ直しながら動かすときは、launch を止めて
そのノードを直接叩く。
"""

import os
from datetime import datetime

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration

HERE = os.path.dirname(os.path.abspath(__file__))

# 収録する topic の一覧は record.sh だけが持つ。ここでそれを呼ぶ形にして、
# 同じ一覧を 2 か所に書かない（片方だけ直して片方が古くなる）。
RECORD_SH = os.path.join(HERE, "record.sh")


def proc(name, script, respawn=True, extra_env=None):
    """1 プロセス。respawn=True なら落ちても launch が上げ直す。"""
    return ExecuteProcess(
        cmd=["python3", "-u", os.path.join(HERE, script)],
        name=name,
        output="screen",
        respawn=respawn,
        respawn_delay=2.0,
        additional_env=extra_env or {},
    )


def generate_launch_description():
    record = LaunchConfiguration("record")
    session = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 既定は config.env の値。fake:=false と書いたときだけ上書きする。
    # 各 bridge は USE_FAKE_SOURCES しか見ないので、launch 引数を
    # そのまま環境変数として渡す（渡さないと fake:= が黙って無視される）。
    fake_env = {"USE_FAKE_SOURCES": LaunchConfiguration("fake")}

    return LaunchDescription([
        # **既定で録る。** 録り忘れた場面は取り返せないが、要らない bag は
        # 後から消せる。非対称なので、既定は「録る」側に倒してある。
        DeclareLaunchArgument("record", default_value="true"),
        DeclareLaunchArgument("fake", default_value=os.environ["USE_FAKE_SOURCES"]),

        # --- センサ -> ROS ---
        proc("cam_bridge", "bridge/cam_bridge.py", extra_env=fake_env),
        # 機体マイクは cam_bridge と別プロセス。ALSA デバイスが開けなくても
        # カメラを巻き込まないようにするため（onboard_mic_bridge.py 冒頭）。
        proc("onboard_mic_bridge", "bridge/onboard_mic_bridge.py", extra_env=fake_env),
        proc("soundmap_bridge", "bridge/soundmap_bridge.py", extra_env=fake_env),
        proc("operator_mic_bridge", "bridge/operator_mic_bridge.py", extra_env=fake_env),
        proc("clock_node", "bridge/clock_node.py"),

        # --- 指令 -> モータ ---
        proc("rover_driver", "driver/rover_driver.py"),
        proc("head_driver", "driver/head_driver.py"),

        # --- 収録 ---
        # respawn しない。収録が落ちたら気付けるようにする（黙って
        # 上げ直すと、その間のデータが抜けたことが分からなくなる）
        ExecuteProcess(
            condition=IfCondition(record),
            cmd=[RECORD_SH, session],
            name="record",
            output="screen",
        ),
    ])
