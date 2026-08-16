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

## 1. 配って動かすまで

**手順は PC ごとの README に全部入っている**（フォルダを置く → 依存 → 設定 →
起動、の 4 段）。ここに置くのは全体の対応表と、複数の PC で共通に踏む罠だけ。

### 1.1 どのフォルダをどの PC に置くか

| PC | 置くフォルダ | 手順 |
|---|---|---|
| PC-A サイネージ | `pc-a-signage/` | [pc-a-signage/README.md](pc-a-signage/README.md) |
| PC-B ロボット | `common/` + `pc-b-robot/` | [pc-b-robot/README.md](pc-b-robot/README.md) |
| **PC-C 操作者** | **配らない（この repo の置き場所でそのまま動かす）** | [pc-c-operator/README.md](pc-c-operator/README.md) |
| PC-D サーバ | `common/` + `pc-d-server/` | [pc-d-server/README.md](pc-d-server/README.md) |

**PC-C は開発機そのもの**なので配る必要が無い（配る側がここ）。依存もこの
機械で直に試せるため、PC-C の README は apt / pip の一覧を持たない。

PC-B と PC-D には **`common/` を必ず一緒に配り、相対位置を変えない。**
`env.sh` は `../common/config.env` を、PC-B の `operator_mic_bridge.py` と
PC-D の `recv_ome.py` は `common/ome_receiver.py` を相対パスで引く。置き場所
自体は自由だが、下のように並べる:

```
~/p32/common/          ~/p32/pc-b-robot/        （PC-B）
~/p32/common/          ~/p32/pc-d-server/       （PC-D）
```

PC-A だけは他と繋がらないので `pc-a-signage/` 単体でよい。

### 1.2 設定を埋める

`common/config.env` の **★**（PC-C の IP）と各 PC の `config.env` の **★**
（デバイス名）を現地で確認して書く。確認方法は [todo-list.md](todo-list.md)。

**`config.env` が設定の唯一の出所。** 各スクリプトは既定値を持たず、未設定なら
起動時に落ちる。コード側に既定値を二重に書かないこと。

**ハードを繋ぐか繋がないかは `common/config.env` の `USE_FAKE_SOURCES` だけで
決まる**（既定 `0` = 実デバイス。起動コマンドは変えない。§2 参照）。

### 1.3 依存で踏む罠

**`gstreamer1.0-nice` は OME からの受信に必須。** `libnice10` だけでは足りない ──
webrtcbin は libnice を直接リンクするが、ICE の実体は `nicesrc`/`nicesink` という
別パッケージの gst エレメントで、無いと警告だけ出して `create-answer` が黙って
失敗する。確認は `gst-inspect-1.0 nicesrc`。要るのは **webrtcbin を動かす PC-B と
PC-D**（PC-C の OME は原生バイナリで gst を使わない）。

**`audio_common_msgs` は `~/ros2_ws` にある沿用版を使う。** `AudioDataStamped` /
`BoxieMotors` / `BoxieStatus` はこの版にしか無い。**apt の
`ros-humble-audio-common-msgs`（4.x）は型定義が別物なので入れない。** 要るのは
ROS を使う **PC-B と PC-C**。

**自作 msg `p3_msgs`（`ClockOffset`）を使うのは PC-B だけ**（`clock_node.py`）。
PC-C は ROS を使うが `p3_msgs` は要らない。

**PC-C の `static/vendor/` は空で配られる。** `socket.io.min.js` と
`ovenplayer.js` を接続のあるうちに落としておく（CDN のままだと現地に
インターネットが無い時に操作画面ごと落ちる）。手順は PC-C の README。

### 1.4 起動

各 PC とも、置いたディレクトリで 1 本叩くだけ（`source` は各スクリプトが中でやる）:

| PC | コマンド | 備考 |
|---|---|---|
| PC-C | `./run.sh` | **最初に立てる。** 背後に回るので `./run.sh status` / `stop` |
| PC-B | `./run.sh` | **収録込み**。前面で動くので Ctrl-C で止める |
| PC-D | `./run.sh` | 文字起こしが動く。順序は不問 |
| PC-A | `./signage.sh` | 他と繋がらないのでいつでも |

**OME が立ってから PC-B を起動する**（設計 §0.2）。つまり PC-C → PC-B。
PC-D は送出側が未起動でも 5 s ごとに繋ぎ直すので、順序を気にしなくてよい。

**PC-B は既定で収録する。** 出力は `RECORD_DIR/<起動時刻>/`（既定
`~/p32/rosbags/`）で **10.2 GB/時**。空きの確認は起動時に `record.sh` が出す
`df -h` で。要らないときは `./run.sh record:=false`。

## 2. センサが無い状態での確認

`common/config.env` の `USE_FAKE_SOURCES` を `1` にすると、カメラ・機体マイク・
16ch マイクが `videotestsrc` / `audiotestsrc` に、スピーカーが `fakesink` に
差し替わる。**差し替わるのはデバイスの口だけ**なので、符号化から先（RTMP・
OME・WebRTC・ROS・bag）は全部本物が走る ── だから下の表が測れる。逆に言うと
**`1` でも OME は立てておく必要がある。**

以下は**実測で確認済み**。

| 確認したこと | 結果 |
|---|---|
| bridge が gst から ROS へ流す | video 30 fps / 16ch 100 Hz / 機体マイク 47 Hz |
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

**上の実測は機体マイクを cam_bridge から切り出す前のもの。** 流れる topic と
レートは変えていないが、プロセス構成と `rtmpsink sync=false` は測り直しが要る
（todo-list.md の「機体マイクの分離」）。

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
