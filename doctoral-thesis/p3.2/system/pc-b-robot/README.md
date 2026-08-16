# PC-B ロボット miniPC

センサを gst で送り出し、操作者の音声を鳴らし、ROS の指令でモータを回す。
記録もここに集約する。ヘッドレスで動く。設計は [../teleop-architecture.md](../teleop-architecture.md)。

Ubuntu 22.04 + ROS 2 humble。CPU は N100 の 4 コアで、映像の符号化・音響マップ
生成・bag 書き込みを同時に回す。

以下 1 → 4 の順に一度やれば、あとは 4 だけを繰り返す。

## 1. フォルダを置く

PC-B に置くのは **`common/` と `pc-b-robot/` の 2 つ**。**必ず並べて置く。**
`env.sh` は `../common/config.env` を、`bridge/operator_mic_bridge.py` は
`../../common/ome_receiver.py` を相対パスで引くので、`pc-b-robot/` だけ配ると
起動した瞬間に落ちる。

手元（この repo）の `system/` で:

```bash
rsync -a --exclude __pycache__ common pc-b-robot <user>@<PC-B の IP>:~/p32/
```

置いた先はこうなる。`~/p32` は好きな場所でよいが、**この 2 つの相対位置だけは
変えない**:

```
~/p32/common/        config.env（設定の唯一の出所）・ome_receiver.py・p3_msgs/
~/p32/pc-b-robot/    2 以降の作業はすべてこのディレクトリで行う
```

## 2. 依存を入れる

```bash
# ROS 2 と GStreamer（PC-B の全ノードが両方を使う）
sudo apt install ros-humble-desktop \
    gstreamer1.0-tools gstreamer1.0-plugins-{base,good,bad,ugly} \
    gstreamer1.0-libav python3-gi v4l-utils

# 収録（mcap）・映像の msg 型・iGPU での映像符号化
sudo apt install ros-humble-rosbag2-storage-mcap ros-humble-foxglove-msgs \
    gstreamer1.0-vaapi intel-media-va-driver

# OME から操作者マイクを WebRTC で受けるのに要る
sudo apt install gstreamer1.0-nice

# 1-bit 音響マップ生成器。numpy は ros-humble-desktop が連れてくるが scipy は来ない
sudo apt install python3-scipy

# 台車（MQTT）と Keigan モータ（BLE）
pip install paho-mqtt pykeigan_motor
```

自作 msg（`ClockOffset`）をビルドする。**使うのは PC-B だけ**:

```bash
cp -r ~/p32/common/p3_msgs ~/ros2_ws/src/
cd ~/ros2_ws && colcon build --packages-select p3_msgs
```

**`audio_common_msgs` は `~/ros2_ws` にある沿用版を使う。** `AudioDataStamped` /
`BoxieMotors` / `BoxieStatus` はこの版にしか無い。**apt の
`ros-humble-audio-common-msgs`（4.x）は型定義が別物なので入れない**（理由は
[../README.md](../README.md) §1.3）。

入ったかどうかは 4 の起動前にまとめて見る（下の「起動前の確認」）。

## 3. 設定を埋める

**ハードは自動では見つからない。** 名前で引くもの（そのままでよい）と、現地で
調べて書くものがある。★ が付いているのが後者:

| 何 | 変数 | いま | 現地で |
|---|---|---|---|
| Xacti カメラ | `CAM_DEVICE` | `CX-MT500` | **そのまま。** 名前で `/dev/videoN` を解決する |
| 16ch アレイ | `MIC_ARRAY_DEVICE` | `hw:CARD=UMA16v2,DEV=0` | **そのまま。** ALSA のカード名指定 |
| Keigan モータ ×5 | `ADDR_*` | 実機の BLE MAC | 交換したときだけ直す |
| 機体マイク / スピーカー ★ | `ONBOARD_MIC_DEVICE` `SPEAKER_DEVICE` | `default`（**仮置き**） | 機種が決まったら `hw:CARD=<名前>` に |
| 〃 のレート ★ | `ONBOARD_MIC_RATE` `_CHANNELS` | 2ch / 48000 | 実機が対応する値に |
| Keigan ALI（台車）★ | `ALI_MQTT_HOST` `_PORT` | 沿用元の値 | **現場ごとに変わる** |
| OME の在り処 ★ | `PC_C_IP` | `192.168.1.100` | PC-C で `ip -4 -o a` |

レートとデバイス名は `../common/config.env`、残りは `config.env`。各行の
すぐ上に、その値の調べ方と外したときの症状をコメントで書いてある。

### 調べるコマンド

```bash
# カメラ ── 名前に CX-MT500 が入っていれば CAM_DEVICE はそのままでよい
cat /sys/class/video4linux/*/name
v4l2-ctl --list-devices                       # MJPG 1920x1080@30 が出るかも見る
v4l2-ctl -d /dev/videoN --list-formats-ext

# 機体マイク / スピーカー ── `card N:` の直後の短い名前が hw:CARD= に書く値
arecord -l          # 録音側
aplay -l            # 再生側
arecord -D hw:CARD=<名前>,DEV=0 --dump-hw-params   # 対応レートと ch 数

# 16ch アレイ（UMA16v2 と出れば OK）
arecord -l | grep -i uma

# Keigan モータの BLE アドレス
sudo hcitool lescan

# 台車（ALI）── broker に届くか、指令が流れるか
ping <ALI の IP>
nc -vz <ALI の IP> 9075
mosquitto_sub -h <ALI の IP> -p 9075 -t 'control/#' -v   # スティックを倒すと出る
```

`mosquitto_sub` だけ別パッケージ（`sudo apt install mosquitto-clients`）。
台車の経路を疑うときにしか使わないので、§2 の必須には入れていない。

**USB の口を分けること。** Xacti（MJPG 1080p30 で 24〜40 Mbps）も UMA16v2
（22.6 Mbps）も等時転送なので、同じコントローラにぶら下げると取り合って
コマ落ちと録音の途切れが出る。`lsusb -t` で別系統か確認する。

**実機かフェイクかは `../common/config.env` の `USE_FAKE_SOURCES` だけで決まる。**
起動コマンド（§4）はどちらでも同じで、何も足さない。

| 値 | 何が変わるか |
|---|---|
| `0`（既定） | 実デバイスを開く。**機体の上ではこれ。触らなくてよい** |
| `1` | カメラ・機体マイク・16ch マイクが testsrc に、スピーカーが `fakesink` に差し替わる。ハードを 1 つも繋がずに手元の机で経路だけ確認するとき |

差し替わるのは**デバイスの口だけ**で、その先（H.264 符号化・RTMP・OME・
ROS への publish・bag 収録・音響マップ生成）は `1` でも全部本物が走る。
**`1` でも OME（PC-C）は立てておくこと** ── フェイクにしてもネットワークが
要らなくなるわけではない。

`0` のままハードが繋がっていなければ、`v4l2src` / `alsasrc` がデバイスを
開けずに起動時に落ちる。**黙って偽物を掴むより落ちるほうがよい**ので既定を
`0` にしてある。

設定は `config.env` にしか無い。**コード側に既定値は無く、未設定なら起動時に
落ちる** ── 「動いているのに値が違う」より落ちるほうがよいため。

## 4. 起動

```bash
cd ~/p32/pc-b-robot
./run.sh                    # これだけ。収録も一緒に始まる
```

`run.sh` は `env.sh` を読んで `ros2 launch pcb.launch.py` を叩くだけの 1 枚。
`source` を別に打つ必要は無い。**前面で動くので、止めるのは Ctrl-C。**

**OME（PC-C）を先に立てること**（設計 §0.2）。

落ちたプロセスは `respawn` で上がり直す（収録だけは上げ直さない ── 黙って
再開すると抜けに気付けないため）。1 ノードだけ直しながら動かすときは
`./run.sh` を止めて、`source env.sh` してから
`python3 bridge/soundmap_bridge.py` のように直接叩く。

### 収録（既定で入っている）

**`./run.sh` は最初から録る。** 録り忘れた場面は取り返せないが、要らない bag は
後から消せる ── 非対称なので既定を「録る」側に倒してある。録りたくないときだけ:

```bash
./run.sh record:=false
```

置き場所は `RECORD_DIR/<起動時刻>/`（`RECORD_DIR` は `config.env`、既定
`~/p32/rosbags`）。セッション名は `./run.sh` を叩いた時刻:

```
~/p32/rosbags/20260816_212000/
├── metadata.yaml
├── 20260816_212000_0.mcap     BAG_SPLIT_MB（既定 2048）ごとに
└── 20260816_212000_1.mcap     _1, _2 … と割れていく
```

**起動時に `record.sh` が実際のパスと `df -h` を画面に出す**ので、毎回そこで
空きを確かめる。**10.2 GB/時**（16ch が 5.1、映像が 3.6、残りが音響マップと
指令）なので、収録予定時間ぶんの空きが要る（[../todo-list.md](../todo-list.md)）。

録る topic の一覧を持つのは `record.sh` だけ（launch もこれを呼ぶ）。

### 起動前の確認

初回と、依存を触ったあとに 1 回。`source env.sh` を済ませてから:

```bash
gst-inspect-1.0 nicesrc >/dev/null && echo "nice OK"
python3 -c "import numpy, scipy, gi, rclpy; import paho.mqtt.client; \
            from pykeigan import blecontroller; print('python OK')"
ros2 interface show p3_msgs/msg/ClockOffset >/dev/null && echo "p3_msgs OK"
ros2 interface show audio_common_msgs/msg/BoxieMotors >/dev/null && echo "audio_common_msgs OK"
```

**この 4 つは欠けても起動は通る。** `nicesrc` が無ければ操作者マイクだけ、
scipy が無ければ音響マップだけ、paho / pykeigan が無ければ台車と首だけが
黙って動かなくなる（各ノードが ImportError を握って「その機能は無し」で
続行する設計のため）。先にここで見ておくほうが早い。

### 動いているかの確認

10 秒ごとに各 bridge が件数を出す。`USE_FAKE_SOURCES=1` での期待値:

| ノード | 10 秒あたり |
|---|---|
| `cam_bridge` | video 300 |
| `onboard_mic_bridge` | audio 470 |
| `soundmap_bridge` | 16ch 1000・マップ 100 |

## 中身

| ノード | 送出先 | ROS へ出す topic |
|---|---|---|
| `bridge/cam_bridge.py` | OME (RTMP) | `camera/video` |
| `bridge/onboard_mic_bridge.py` | OME (RTMP) | `onboard_mic/audio` |
| `bridge/soundmap_bridge.py` | OME (RTMP) | `soundmap/raw`、`mic_array/audio` |
| `bridge/operator_mic_bridge.py` | 機体スピーカー | `operator_mic/audio` |
| `bridge/clock_node.py` | — | `record/clock_offset`（1 Hz） |
| `driver/rover_driver.py` | MQTT → ALI | 購読 `rover/twist` |
| `driver/head_driver.py` | BLE → Keigan ×5 | 購読 `head/command`・`arm/command`、`keigan_motor/status` |

`run.sh`（起動）→ `pcb.launch.py`（プロセス構成）→ `record.sh`（収録）の 3 枚。
`soundmap/` は 1-bit 生成器（同梱）。

## つまずきやすい点

- **`h264parse config-interval=-1` を外さない。** SPS/PPS が IDR ごとに入らないと、
  分割された 2 個目以降の mcap が単体で復号できない
- **`audio_common_msgs` は apt の 4.x を入れない。** 型定義が別物。`~/ros2_ws` の
  ものを使う
- **`gstreamer1.0-nice` が要る。** `operator_mic_bridge.py` が OME から WebRTC で
  受けるため。無いと `create-answer` が黙って失敗する（`gst-inspect-1.0 nicesrc`）
- **記録に残るのは指令だけ。** BLE の帯域を食うのでモータの読み戻しはしない。
  実際に向いた角度が要るなら、`head/command` に head_driver の可動域制限と
  smoothing を掛けて事後に再現する（パラメータは bag に入らない）。smoothing は
  **時間駆動**なので、再現には `smooth_alpha` と `smooth_hz` の両方が要る
- 収録を始めたら**手を叩く。** 映像・機体マイク・16ch に同時に入るので、
  時刻換算の検算になる
