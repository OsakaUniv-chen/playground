# PC-B ロボット miniPC

センサを gst で送り出し、操作者の音声を鳴らし、ROS の指令でモータを回す。
記録もここに集約する。ヘッドレスで動く。設計は [../teleop-architecture.md](../teleop-architecture.md)。

## 起動

```bash
source env.sh
ros2 launch pcb.launch.py                  # 全プロセス
ros2 launch pcb.launch.py record:=true     # 収録も同時に
```

止めるのは Ctrl-C。落ちたプロセスは `respawn` で上がり直す（収録だけは
上げ直さない ── 黙って再開すると抜けに気付けないため）。1 ノードだけ
直しながら動かすときは launch を止めて `python3 bridge/soundmap_bridge.py`
のように直接叩く。**OME（PC-C）を先に立てること**（設計 §0.2）。

## 中身

| ノード | 送出先 | ROS へ出す topic |
|---|---|---|
| `bridge/cam_bridge.py` | OME (RTMP) | `camera/video`、`onboard_mic/audio` |
| `bridge/soundmap_bridge.py` | OME (RTMP) | `soundmap/raw`、`mic_array/audio` |
| `bridge/operator_mic_bridge.py` | 機体スピーカー | `operator_mic/audio` |
| `bridge/clock_node.py` | — | `record/clock_offset`（1 Hz） |
| `driver/rover_driver.py` | MQTT → ALI | 購読 `rover/twist` |
| `driver/head_driver.py` | BLE → Keigan ×5 | 購読 `head/command`・`arm/command`、`keigan_motor/status` |

`soundmap/` は 1-bit 生成器（同梱）、`record.sh` は収録する topic の一覧を持つ
唯一の場所（launch もこれを呼ぶ）。

`USE_FAKE_SOURCES=1`（既定）ならセンサ無しで経路を確認できる。10 秒ごとに
各 bridge が件数を出す ── cam_bridge は video 300、soundmap_bridge は
16ch 1000・マップ 100 が期待値。

## つまずきやすい点

- **`h264parse config-interval=-1` を外さない。** SPS/PPS が IDR ごとに入らないと、
  分割された 2 個目以降の mcap が単体で復号できない
- **`audio_common_msgs` は apt の 4.x を入れない。** 型定義が別物。`~/ros2_ws` の
  ものを使う
- **`gstreamer1.0-nice` が要る。** `operator_mic_bridge.py` が OME から WebRTC で
  受けるため。無いと `create-answer` が黙って失敗する（`gst-inspect-1.0 nicesrc`）
- **記録に残るのは指令だけ。** BLE の帯域を食うのでモータの読み戻しはしない。
  実際に向いた角度が要るなら、`head/command` に head_driver の可動域制限と
  smoothing を掛けて事後に再現する（パラメータは bag に入らない）
- 収録を始めたら**手を叩く。** 映像・機体マイク・16ch に同時に入るので、
  時刻換算の検算になる
