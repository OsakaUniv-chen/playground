# 遠隔操作システム 実装

設計は [teleop-architecture.md](teleop-architecture.md)。PC ごとにフォルダを分けてある。

```
common/          全 PC 共通の設定と自作 msg
pc-a-signage/    サイネージ端末（他と接続しない・実装済み）
pc-b-robot/      ロボット miniPC — センサ送出・モータ駆動・収録
pc-c-operator/   操作者端末 — OME・操作 UI・マイク送出
pc-d-server/     高性能サーバ — 推論
```

起動方法は PC ごとの README にある。PC-B は `ros2 launch`、
PC-C と PC-D は `./run.sh`（`stop` / `status` も取る）。

---

## 1. 準備

### 1.1 設定を埋める

`common/config.env` の **★ 印**（IP アドレス）と、各 PC の `config.env` の
**★ 印**（デバイス名）を現地で確認して書く。デバイス名は
`pc-b-robot/gst/probe_devices.sh` が一覧する。

### 1.2 依存

```bash
# 全 PC
sudo apt install ros-humble-desktop \
    gstreamer1.0-tools gstreamer1.0-plugins-{base,good,bad,ugly} \
    gstreamer1.0-libav python3-gi v4l-utils

# PC-B と PC-D（OME から WebRTC で受ける側）
sudo apt install gstreamer1.0-nice

# PC-B（収録）
sudo apt install ros-humble-rosbag2-storage-mcap ros-humble-foxglove-msgs
pip install paho-mqtt        # Keigan ALI の制御に使う

# PC-C（UI）
pip install flask flask-socketio
```

**`gstreamer1.0-nice` は OME からの受信に必須。** `libnice10`（ライブラリ本体）
だけでは足りず、`nicesrc`/`nicesink` エレメントが要る。無いと
`create-answer` が黙って失敗する。`gst-inspect-1.0 nicesrc` で確認できる。
要るのは **webrtcbin を動かす PC-B と PC-D**。PC-C は OME 自体が原生バイナリで
gst を使わないので実運用では不要（疎通確認に `ome_receiver.py` を使うなら要る）。
どの PC に要るかは [todo-list.md](todo-list.md) に表がある。

`audio_common_msgs` は `~/ros2_ws` に既にある。無い場合は
`reference code/indy-blr-.../blr/audio_common_msgs` をコピーしてビルドする。
**apt の `ros-humble-audio-common-msgs`（4.x）は型定義が別物なので入れないこと。**

### 1.3 自作 msg をビルド

```bash
cp -r common/p3_msgs ~/ros2_ws/src/
cd ~/ros2_ws && colcon build --packages-select p3_msgs
```

### 1.4 vendor 化（PC-C）

`socket.io.min.js` と `ovenplayer.js` を `pc-c-operator/static/vendor/` に置く。
CDN のままだと**現地にインターネットが無い時に操作画面ごと落ちる**。

---

## 2. センサが無い状態での確認

`common/config.env` の `USE_FAKE_SOURCES=1`（既定）で、実デバイスの代わりに
`videotestsrc` / `audiotestsrc` が使われる。この状態で以下は**実測で確認済み**。

| 確認したこと | 結果 |
|---|---|
| 3 つの bridge が gst から ROS へ流す | video 30 fps / 16ch 100 Hz / 機体マイク 47 Hz |
| 映像の切り出し | 実機 MJPG 1920×1080 → 1080×1080 の H.264 |
| 16ch の帯域 | 44.1 kHz で 11.31 Mbps（理論値 11.29 と一致） |
| 1 つの bag に全 topic が入る | 6 topic・AudioInfo は latched で 1 件ずつ |
| タイムスタンプが UNIX 時間になる | `header.stamp` が `log_time` の 0.3〜5 ms 前 |
| 16ch の取り込み | 実機 UMA16v2 で 16ch/44.1 kHz。全 ch に信号（音響マップは PC-B で生成するので外へは出さない） |
| 台車の watchdog | 指令停止から 0.54 s で action 0 を送出 |
| **OME からの WebRTC 受信** | 映像 30 fps 定常。ICE `completed` |
| **PC-D の 4 入力を 1 プロセスで並行受信** | 映像 30 fps・マップ 10 fps・音声 2 系統が同時 |
| **操作者マイク PC-C → OME → PC-B** | ROS へ 50 msg/s。PTT の消音でストリームは切れない |
| **送出が後から立ち上がる場合** | 受信側が 5 s ごとに繋ぎ直して拾う（起動順は不問） |

OME まわりは**全部 1 台（loopback）での確認**で、機械をまたいだ ICE は未確認。
現地で最初に見るところとして [todo-list.md](todo-list.md) に書いてある。

```bash
# PC-D: OME から 4 本受ける
cd pc-d-server && source env.sh && python3 gst/recv_ome.py --seconds 20
# 1 本だけ確かめる（-v で SDP まで出る）
python3 common/ome_receiver.py <stream_key> --host <PC-C>
```

```bash
# PC-B: 送出 + 収録
cd pc-b-robot && source env.sh && ros2 launch launch/pcb.launch.py record:=true

# 収録の確認
./pc-b-robot/record/check_bag.sh ~/p32_bags/<session>
```

---

## 3. 現地で確認すること

実機が無いと決められない項目は **[todo-list.md](todo-list.md)** にまとめてある
（デバイス名・サンプリングレート・ALI の制御方式・BLE アドレス・DDS の疎通など）。
確認方法と、決まっていないと何が困るかを項目ごとに書いてある。

---

## 4. まだ書いていないもの

VLM 推論（`pc-d-server/infer/head_controller.py` の `decide()`）・ジェスチャの
割り当て・収録の一括開始停止。[todo-list.md](todo-list.md) を参照。
OME からの受信は入力として揃っているので、`decide()` は
`OmeInputs.latest_video("stream")` などから取れる。

---

## 5. 設計からの逸脱

| 箇所 | 設計 | 実装 | 理由 |
|---|---|---|---|
| 16ch の並び | `S16LE_PLANAR` | **interleaved 固定** | UMA16v2 が MMAP/RW_INTERLEAVED でしか開けず、planar は取れない。ALSA 既定のまま変換が要らず wav にもそのまま書ける |

---

## 6. 実装で分かったこと

**x264enc は PTS に 3600000 秒の固定オフセットを載せる。** 負の DTS を避ける
ための仕様で、`buffer.pts` をそのまま使うと記録の時刻が 41 日ずれる（実際に
踏んで確認した）。`segment.to_running_time()` を通せば消える
（`bridge/gst_ros_common.py`）。

**gst のノードは ROS の Python 環境で動かす。** `rclpy` と `gi` の両方が要るため、
`wolf` の virtualenv ではなく `source /opt/ros/humble/setup.bash` した環境で走らせる。
`env.sh` がこれをやる。

**`import rclpy` すると libsoup の WebSocket でプロセスが落ちる。**
GIO は既定の proxy resolver として libproxy を呼ぶ。libproxy は内部で C++ 例外を
投げるが、rclpy が読み込む libunwind が `_Unwind_Resume` を乗っ取るため巻き戻しに
失敗し、`std::terminate` → abort（または SIGSEGV）になる。**単体では動くのに
ROS ノードに載せた瞬間に落ちる**ので、原因が signalling 側にあるように見える。
`ome_receiver.py` の冒頭で `GIO_USE_PROXY_RESOLVER=dummy` を設定して回避してある。
繋ぎ先は LAN か loopback なので proxy はもともと要らない。

**x264enc は上流が I420 でないと High 4:4:4 を選ぶ。** ブラウザはこの
プロファイルを復号できない。しかも OME は bypass なので SDP には baseline
（`42e01f`）と書いたまま配る。**交渉は成功して映像だけ出ない**という形になる。
音響マップは appsrc から BGR を流すので、放っておくと必ずこれを踏む。
送出側は `x264enc ! video/x-h264,profile=baseline` で固定してある。

**OME は起動時の NIC の住所を ICE candidate として配り続ける。**
後から DHCP で変わっても直らない。`ome_receiver.py` は候補のアドレスを
signalling で繋いだ先に書き換えて自衛するが、ブラウザはしない。
現地では PC-C のネットワークが確定してから OME を再起動する。
