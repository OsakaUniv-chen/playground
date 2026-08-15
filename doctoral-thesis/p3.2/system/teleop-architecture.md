# 遠隔操作システム 構成

リリアナさんの `teleop_interface` + `webrtc_singalling_proc` の architecture を沿用し、こちらのハードウェア（Xacti カメラ・16ch マイクアレイ・Microphone-Speaker・頭部 yaw/pitch・Keigan ALI）に載せ替えた場合の構成。

## 0. インベントリ

**ホスト 4 台／デバイス 10 個。**

| ホスト | 稼働物 | メモ |
|---|---|---|
| PC-A サイネージ端末 | Chrome 巡回スクリプトのみ | 他 PC と接続しない |
| PC-B ロボット miniPC | **[Publish] gst**<br>・Xacti Cam（1080×1080）<br>・機体マイク（1-2 ch）<br>・**音響マップ**（1-bit生成，64x64）<br>**[Receive] gst**<br>・スピーカー（操作者マイク）<br>**[Receive] ROS**<br>・頭部 yaw / pitch<br>・Keigan ALI | ip1 |
| PC-C 操作者端末 | OME サーバ<br>**[Publish] ROS**<br>・Keigan ALI（ゲームパッド）<br>**[Receive]**<br>・操作 UI 画面（OME Player — 機体マイク＋Xacti Cam+音響マップ） | ip2 |
| PC-D 高性能サーバ | 推論用の高性能 GPU を積む前提<br>**[Receive] gst**（すべて OME 経由）<br>・Xacti Cam<br>・音響マップ<br>・機体マイク／スピーカー（操作者マイク）<br>**[Publish] ROS**<br>・頭部 yaw / pitch<br>・その他のロボット行動決定 | ip3 |

## 1. PC-A サイネージ端末

Linux 1 台の Chrome 全画面で巡回表示。気象庁の情報（天気予報・ひまわり衛星画像・地震情報）を 10 秒ごとに切り替える。

PC-A はサイネージ画面の表示のみ。タッチパネルではなく、他 PC との接続も持たない。

## 2. PC-B ロボット miniPC

| 常駐物 | 内容 |
|---|---|
| gst 送出（Xacti映像） | Xacti 1080×1080 を OME サーバへ |
| gst 送出（音響マップ） | 16ch マイクアレイ → 1-bit 音響マップ → OME サーバへ |
| gst 送出（機体マイク） | 現場の音・発話の有無を拾う 1〜2ch を OME サーバへ |
| gst 受信（操作者マイク） | OME サーバからの操作者の音声を機体スピーカーへ |
| ROS 受信 | Keigan ALI（台車）、頭部動作（Yaw・Pitch）、ジェスチャ（腕） |

## 3. PC-C 操作者端末

| 稼働物 | 内容 |
|---|---|
| OME サーバ | 機体からのセンサー情報を受ける |
| 操作 UI サーバ（ROS 送出） | `app.py`（Flask + SocketIO + ROS 2 publisher for Keigan ALI）`:7779` |
| Chrome | `http://localhost:7779/` を開く。ゲームパッド入力と画面表示 |
| gst 送出（操作者マイク） | 操作者の音声を OME サーバへ |

## 4. PC-D 高性能サーバ

### 4.1 入力（gst 受信）

| 対象 | 用途 | 経路 |
|---|---|---|
| Xacti カメラ | 場面理解 | OME サーバから |
| 音響マップ | 誰が喋っているかの手がかり | OME サーバから |
| 機体マイク | 現場の音、発話の有無 | OME サーバから |
| 操作者の音声 | 操作者が何を言ったかの把握 | OME サーバから |

### 4.2 出力（ROS publish）

| 対象 | 内容 |
|---|---|
| 頭部 yaw / pitch | 「誰に向くか」の決定 |
| その他の行動決定 | 発話タイミング、姿勢など |


## 5. 記録

### 5.1 記録は PC-B に集約する

gst の各パイプラインを `tee` で分岐させ、片方を送出、片方を記録に回す。分岐した枝には必ず `queue` を挟む。

**16ch の生データは送出しないが、記録はする。** 音響マップは 16ch から作った
派生物なので、生データが無いと後から別の手法（acoular など）で作り直せない。
マップと生データの両方を残す。

| 経路 | 目的 |
|---|---|
| リアルタイム | 操作者が見る／PC-D が推論する。遅延の小ささが優先 |
| 記録 | 後の分析・学習。**送出前の生データ**を PC-B に落とす |

**OME 側（PC-C）では記録しない。** RTMP のタイムスタンプはストリーム開始からの相対値で、採取時刻の絶対基準が失われる。

**基準時計は PC-B だけ。** 記録するのは「PC-B が採取したもの」と「PC-B が受け取ったもの」のみで、すべて PC-B の時計で打つ。他機との時刻同期（NTP）は不要。

### 5.2 時刻

タイムスタンプは UNIX 時間（ナノ秒）に統一する。ROS 2 のシステム時計はもともと UNIX 時間なので、揃えるのは gst 側。

**gst のパイプラインクロックは MONOTONIC のまま使う**（REALTIME にすると NTP の step で PTS が飛ぶ）。変換は記録側で行う。

```
unix_ns = buffer.pts + pipeline.get_base_time() + offset
offset  = CLOCK_REALTIME - CLOCK_MONOTONIC
```

`offset` は 1 Hz で採り、同じ bag に流し込む。時計が動いた場合に事後補間で直せる。

| 信号 | `header.stamp` の意味 |
|---|---|
| Xacti 映像 | 撮影時刻 |
| 機体マイク・16ch アレイ | 採取時刻 |
| 操作者マイク | PC-B 到着時刻 |
| PC-C / PC-D からの ROS 指令 | PC-B 到着時刻 |
| PC-B ローカルの ROS（実関節角など） | 採取時刻 |

収録開始時に手を叩く。映像・機体マイク・16ch に同時に入るので、換算の検算に使える。

### 5.3 1 つの bag にまとめる

**gst 由来のデータも ROS メッセージに載せ、storage plugin に mcap を指定した `ros2 bag record` で 1 つの bag に書く。**

```
tee → queue → appsink → ブリッジノード → ROS publish → rosbag2
```

`appsink` のコールバックで `pull-sample` し、§5.2 の換算値を `header.stamp` に入れて publish する。**publish した瞬間の時刻を使わない。**

| 項目 | 方針 |
|---|---|
| `appsink` | `emit-signals=true`、`sync=false`、`max-buffers` を有限値、`drop=false` |
| 記録側の `queue` | 2 秒程度。水位が張り付いたら書き込みが追いついていないので、落として警告する |
| 送出側の `queue` | `leaky=downstream` |
| H.264 | `h264parse config-interval=-1`。SPS/PPS を IDR ごとに入れないと分割後のファイルが単体で復号できない |
| 分割 | `--max-bag-size`。出力は 1 ディレクトリで、論理的には 1 つの bag |
| 開始・停止 | ROS 2 topic を 1 本立てて揃える。セッション ID もここに載せる |

ブリッジノードは信号ごとに独立プロセスにする。1 本落ちても他が録り続ける。

| プロセス | パイプライン |
|---|---|
| `cam_bridge` | Xacti（＋機体マイク） → tee → RTMP ／ appsink |
| `soundmap_bridge` | 16ch alsasrc → tee → 音響マップ生成 → RTMP ／ appsink（生データも記録） |
| `operator_mic_bridge` | OME から WebRTC 受信 → スピーカー ／ ROS publish |
| `clock_node` | 1 Hz で offset を publish |

データ量：

| 対象 | 内容 | ビットレート | 1 時間 |
|---|---|---|---|
| Xacti 映像 | H.264 1080×1080（1920×1080 から切り出し） | 約 8 Mbps | 3.6 GB |
| 機体マイク | PCM 48 kHz 16 bit 2ch | 1.5 Mbps | 0.7 GB |
| 16ch アレイ（生データ） | PCM 44.1 kHz 16 bit 16ch | 11.3 Mbps | 5.1 GB |
| 音響マップ | 64×64 float32 10 Hz | 0.16 Mbps | 0.07 GB |
| 操作者マイク | PCM 16 kHz 16 bit 1ch | 0.26 Mbps | 0.1 GB |
| ROS（指令・関節角・VLM 出力・offset） | — | — | 0.1 GB 未満 |
| **合計** | | **約 21 Mbps** | **約 9.7 GB** |

書き込みは 2.6 MB/s。制約は速度ではなく容量と回収の手間。
**16ch が全体の半分を占める。** 収録時間の見積もりはここで決まる。

音響マップは生成値（64×64 の float）で残す。OME へ送るのは画像に起こした
ものだが、そちらは生成値から作り直せるので記録しない。

一方、**機体から外へ出る帯域は 10 Mbps 程度**（映像 8 + 音響マップ 2）。
16ch を送っていた頃の約半分になる。

### 5.4 メッセージ型と topic

| 信号 | 型 | topic |
|---|---|---|
| Xacti 映像 | `foxglove_msgs/CompressedVideo` | `/<robot>/camera/video` |
| 機体マイク | `audio_common_msgs/AudioDataStamped` | `/<robot>/onboard_mic/audio` |
| 16ch アレイ（生データ） | `audio_common_msgs/AudioDataStamped` | `/<robot>/mic_array/audio` |
| 音響マップ（生成値） | `std_msgs/Float32MultiArray` | `/<robot>/soundmap/raw` |
| 操作者マイク | `audio_common_msgs/AudioDataStamped` | `/<robot>/operator_mic/audio` |
| 台車指令 | `geometry_msgs/Twist` | `/<robot>/rover/twist` |
| 頭部指令 | `audio_common_msgs/BoxieMotors` | `/<robot>/head/command` |
| 腕（ジェスチャ）指令 | `audio_common_msgs/BoxieMotors` | `/<robot>/arm/command` |
| 実関節角（頭部・腕） | `audio_common_msgs/BoxieMotors` | `/<robot>/head/current`、`/<robot>/arm/current` |
| 時計 offset | 自作 msg | `/<robot>/record/clock_offset` |

映像は符号化したまま入れる（`sensor_msgs/Image` に展開すると数十倍になる）。**16ch は符号化しない。**

音声 3 系統は同じ型を使い、topic で区別する。チャネル数・サンプリングレート・並び順は 1 メッセージごとには持たせず、`audio_common_msgs/AudioInfo` を各系統 1 回、`transient_local`（latched）で流す。これも bag に残るので、後から設定資料を探さずに済む。

| topic | channels | sample_rate | sample_format | 1 メッセージ |
|---|---|---|---|---|
| `/<robot>/mic_array/audio` | 16 | 44100 | `S16LE` | 10 ms（441 sample/ch） |
| `/<robot>/onboard_mic/audio` | 2 | 48000 | `S16LE` | 10 ms |
| `/<robot>/operator_mic/audio` | 1 | 16000 | `S16LE` | 到着ごと |

16ch は UMA16v2 が interleaved でしか開けないため `S16LE`（並びは interleaved）。
`sample_format` は自由文字列なので、並びが変わる場合はここに書く。

### 5.5 記録内容への要求

- **指令値ではなく実関節角を残す。** 「どこを向いていたか」の記録源はこちら
- **頭部（PC-D の判断）と台車（操作者の判断）を区別できる形で残す。** 記録が「人の行動」なのか「モデルの出力」なのかで、分析の意味が変わる
- **操作者の発話はテキストで残らない。** 送話区間（プッシュトゥトーク押下）を topic として残し、書き起こしの索引にする

### 5.6 現地で確認する項目

- 打板マーカーが映像・機体マイク・16ch のすべてに入り、換算後の時刻が一致するか
- 記録した `clock_offset` 系列が滑らかか
- 収録トリガが届くか
- 各 topic のメッセージ数が理論値と合うか（落ちていないか）
- ブリッジノードと `ros2 bag record` の CPU 使用率
- PC-B のディスク残量、収録データの回収手順と所要時間
