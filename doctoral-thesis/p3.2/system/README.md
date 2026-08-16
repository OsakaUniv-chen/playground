# 遠隔操作システム 実装

設計は [teleop-architecture.md](teleop-architecture.md)。**未決の項目は
[todo-list.md](todo-list.md) にだけ書く。**

```
common/          全 PC 共通の設定と自作 msg・OME 受信モジュール
pc-a-signage/    サイネージ端末（他と接続しない・実装済み）
pc-b-robot/      ロボット miniPC — センサ送出・モータ駆動・収録
pc-c-operator/   操作者端末 — OME・操作 UI・マイク送出・頭部指令の中継
pc-d-server/     高性能サーバ — 推論（入力は揃っている・判断は未実装）
```

起動方法は PC ごとの README にある（PC-B は `ros2 launch`、PC-C は `./run.sh`）。
**OME が立ってから PC-B を起動する**という順序だけは守る（設計 §0.2）。

## 1. 準備

### 1.1 設定を埋める

`common/config.env` の **★**（PC-C の IP）と各 PC の `config.env` の **★**
（デバイス名）を現地で確認して書く。確認方法は [todo-list.md](todo-list.md)。

### 1.2 依存

```bash
# 全 PC
sudo apt install ros-humble-desktop \
    gstreamer1.0-tools gstreamer1.0-plugins-{base,good,bad,ugly} \
    gstreamer1.0-libav python3-gi v4l-utils

sudo apt install gstreamer1.0-nice          # PC-B と PC-D（OME から受ける側）

# PC-B
sudo apt install ros-humble-rosbag2-storage-mcap ros-humble-foxglove-msgs \
    gstreamer1.0-vaapi intel-media-va-driver
pip install paho-mqtt pykeigan_motor

# PC-C
pip install flask flask-socketio
```

**`gstreamer1.0-nice` は OME からの受信に必須。** `libnice10` だけでは足りない ──
webrtcbin は libnice を直接リンクするが、ICE の実体は `nicesrc`/`nicesink` という
別パッケージの gst エレメントで、無いと警告だけ出して `create-answer` が黙って
失敗する。確認は `gst-inspect-1.0 nicesrc`。要るのは **webrtcbin を動かす PC-B と
PC-D**（PC-C の OME は原生バイナリで gst を使わない）。

`audio_common_msgs` は `~/ros2_ws` にある。**apt の `ros-humble-audio-common-msgs`
（4.x）は型定義が別物なので入れない。**

```bash
cp -r common/p3_msgs ~/ros2_ws/src/ && cd ~/ros2_ws && colcon build --packages-select p3_msgs
```

PC-C は `pc-c-operator/static/vendor/` に `socket.io.min.js` と `ovenplayer.js` を
置く（CDN のままだと現地にインターネットが無い時に操作画面ごと落ちる）。

## 2. センサが無い状態での確認

`USE_FAKE_SOURCES=1`（既定）で `videotestsrc` / `audiotestsrc` に差し替わる。
以下は**実測で確認済み**。

| 確認したこと | 結果 |
|---|---|
| 3 つの bridge が gst から ROS へ流す | video 30 fps / 16ch 100 Hz / 機体マイク 47 Hz |
| 映像の切り出し | 実機 MJPG 1920×1080 → 1080×1080 の H.264 |
| 16ch の取り込みと帯域 | 実機 UMA16v2 で 16ch/44.1 kHz、全 ch に信号。11.31 Mbps（理論値と一致） |
| 音響マップの生成 | 16.7 ms/枚・10 Hz 定常。送出先が居なくても生成と記録は止まらない |
| 1 つの bag に全 topic が入る | gst 由来 3 系統と ROS 側が同じ bag に |
| タイムスタンプが UNIX 時間になる | `header.stamp` が `log_time` の 0.3〜5 ms 前 |
| 台車の watchdog | 指令停止から 0.54 s で action 0 |
| **OME からの WebRTC 受信** | 映像 30 fps 定常。ICE `completed` |
| **PC-D の 4 入力を 1 プロセスで並行受信** | 映像 30 fps・マップ 10 fps・音声 2 系統が同時 |
| **操作者マイク PC-C → OME → PC-B** | ROS へ 50 msg/s |
| **頭部指令 PC-D → PC-C → ROS** | `publish_goal(12.4,-33.6)` → `head/command [12,-34,0]`。同値は送らない |
| **送出が後から立ち上がる場合** | 受信側が 5 s ごとに繋ぎ直して拾う（起動順は不問） |
| **理研の PC-D との遠隔** | Tailscale 直結で往復 75 ms・37〜46 Mbps |

OME まわりの loopback 以外の確認は [todo-list.md](todo-list.md) にある。

```bash
python3 common/ome_receiver.py <stream_key> --host <PC-C>   # 1 本だけ確かめる（-v で SDP）
```

## 3. 実装で分かったこと

**x264enc は PTS に 3600000 秒の固定オフセットを載せる。** 負の DTS を避ける仕様で、
`buffer.pts` をそのまま使うと記録の時刻が 41 日ずれる（実際に踏んだ）。
`segment.to_running_time()` を通せば消える（`bridge/gst_ros_common.py`）。

**x264enc は上流が I420 でないと High 4:4:4 を選ぶ。** ブラウザはこれを復号できず、
しかも OME は bypass なので SDP には baseline（`42e01f`）と書いたまま配る。
**交渉は成功して映像だけ出ない**という形になるので、送出側は
`x264enc ! video/x-h264,profile=baseline` で固定してある。

**`import rclpy` すると libsoup の WebSocket でプロセスが落ちる。** GIO は既定の
proxy resolver として libproxy を呼び、libproxy は内部で C++ 例外を投げる。一方
rclpy が読み込む libunwind が `_Unwind_Resume` を乗っ取るため巻き戻しに失敗し、
`std::terminate` → abort になる。**単体では動くのに ROS ノードに載せた瞬間に落ちる**
ので原因が signalling 側にあるように見える。`ome_receiver.py` の冒頭で
`GIO_USE_PROXY_RESOLVER=dummy` を設定して回避してある。

**gst のノードは ROS の Python 環境で動かす**（`rclpy` と `gi` の両方が要る）。
`env.sh` がこれをやる。

## 4. 設計からの逸脱

| 箇所 | 設計 | 実装 | 理由 |
|---|---|---|---|
| 16ch の並び | `S16LE_PLANAR` | **interleaved 固定** | UMA16v2 が MMAP/RW_INTERLEAVED でしか開けない。ALSA 既定のまま変換が要らず wav にもそのまま書ける |
| 映像の符号化 | 指定なし | **iGPU（VA-API）優先・自動で software に戻る** | N100 の 4 コアで音響マップ生成と bag 書き込みも回すため。**未検証**（[todo-list.md](todo-list.md)） |
