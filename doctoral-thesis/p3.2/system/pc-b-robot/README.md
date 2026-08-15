# PC-B ロボット miniPC

センサを繋いで gst で送り出し、操作者の音声を鳴らし、ROS 2 の指令でモータを回す。
記録もここに集約する（設計 §5.1）。画面は要らないのでヘッドレスで動く。

## 起動

```bash
source env.sh                              # 設定と ROS 環境を読む

ros2 launch launch/pcb.launch.py                  # 全プロセス起動
ros2 launch launch/pcb.launch.py record:=true     # 収録も同時に開始
```

止めるのは Ctrl-C。収録だけ後から始める場合は別端末で `./record/record.sh`。

### 起動方法

`ros2 launch launch/pcb.launch.py` だけ。プロセスが落ちても `respawn` で
上げ直し、ログは `~/.ros/log/` に残る。

1 つのノードだけ直しながら動かしたいときは、launch を止めてそのノードを
直接叩く（`python3 bridge/soundmap_bridge.py` など）。全部のログが 1 つに
混ざるのが読みにくい場合も同じ。

## 中身

| 場所 | 内容 |
|---|---|
| `gst/` | gst-launch で単体確認するためのスクリプト。実運用は使わない |
| `bridge/` | gst のパイプラインを持ち、`appsink` から ROS へ流すノード |
| `driver/` | ROS 指令 -> モータ |
| `soundmap/` | 1-bit 音響マップ生成器（同梱。外のディレクトリを参照しない） |
| `record/` | `ros2 bag record` と収録後の確認 |
| `launch/` | ros2 launch 版の起動 |

### bridge

| ノード | 送出先 | ROS へ出す topic |
|---|---|---|
| `cam_bridge.py` | OME (RTMP) | `camera/video`、`onboard_mic/audio` |
| `soundmap_bridge.py` | OME (RTMP) | `soundmap/raw`、`mic_array/audio` |
| `operator_mic_bridge.py` | 機体スピーカー | `operator_mic/audio` |
| `clock_node.py` | — | `record/clock_offset`（1 Hz） |

いずれも `tee` で送出と記録に分岐している。記録側の枝は `drop=false` なので
落とさないが、水位が張り付いたら書き込みが追いついていない。

**`operator_mic_bridge.py` は OME から WebRTC で受ける。** そのため
**この機械に `gstreamer1.0-nice` が要る**（`sudo apt install gstreamer1.0-nice`、
確認は `gst-inspect-1.0 nicesrc`）。無いと `create-answer` が黙って失敗して
音声が来ない。このノードだけは `tee` ではなく、受信コールバックから
ROS publish とスピーカー用 appsrc の両方へ渡している。

タイムスタンプは `segment.to_running_time() + base_time + offset` で UNIX 時間に
換算して `header.stamp` に入れる。**publish した瞬間の時刻は使わない。**

### driver

| ノード | 購読 | 備考 |
|---|---|---|
| `rover_driver.py` | `rover/twist` | 速度スケーリングと **watchdog**（0.5 s で自動停止） |
| `head_driver.py` | `head/command` (BoxieMotors) | Boxie 頭部 3 軸（pitch/yaw/roll、度）。BLE で Keigan モータへ。**可動域制限と smoothing** をここで掛け、実測値を 20 Hz で publish |

頭部は指令 / 実際に送った値 / 実測値を別 topic に分けてある。可動域に当たった
場面や smoothing の効きは、指令値だけでは分からないため（設計 §5.5）。

## センサが無い状態での確認

```bash
# common/config.env で USE_FAKE_SOURCES=1 にする（既定）
source env.sh
ros2 launch launch/pcb.launch.py record:=true
```

`videotestsrc` / `audiotestsrc` が実デバイスの代わりになる。10 秒ごとに
各 bridge がメッセージ数を出すので、期待値と合っているか見る。

| ノード | 10 秒あたりの期待値 |
|---|---|
| cam_bridge | video 300（30 fps） |
| soundmap_bridge | 16ch 1000（100 Hz）、マップ 100（10 Hz） |

## 現地での手順

```bash
./gst/probe_devices.sh          # 1. デバイス名を調べて config.env の ★ を埋める
./gst/cam_send.sh               # 2. カメラ単体で OME へ送れるか
python3 bridge/soundmap_bridge.py   # 3. 16ch から音響マップが立つか
source env.sh && ros2 launch launch/pcb.launch.py record:=true   # 4. 全部
./record/check_bag.sh ~/p32_bags/<session>                       # 5. 収録の確認
```

**収録を始めたら手を叩く。** 映像・機体マイク・16ch に同時に入るので、
換算後の時刻が揃っているかの検算に使える。

## つまずきやすい点

- **`h264parse config-interval=-1` を外さない。** SPS/PPS が IDR ごとに入らないと、
  分割された 2 個目以降の mcap が単体で復号できない
- **`audio_common_msgs` は apt の 4.x を入れない。** 型定義が別物。`~/ros2_ws` の
  ものか reference code のものを使う
- **OME を先に立ててから PC-B を起動する。** `rtmpsink` は接続先が居ないと
  待ち続け、生成も記録も巻き添えで止まる。OME は PC-C に systemd で常駐して
  いるので通常は問題にならないが、PC-C ごと落ちていると PC-B も止まる
