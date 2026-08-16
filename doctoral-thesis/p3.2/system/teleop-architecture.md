# 遠隔操作システム 構成

リリアナさんの architecture を沿用し、こちらのハードウェア（Xacti カメラ・16ch マイクアレイ・Microphone-Speaker・頭部 yaw/pitch・Keigan ALI）に載せ替えた場合の構成。

## 0. インベントリ

**ホスト 4 台。**

| ホスト | 稼働物 |
|---|---|
| PC-A サイネージ端末 | 巡回表示画面 |
| PC-B ロボット miniPC | **[Publish] gst**<br>・Xacti Cam（1080×1080）<br>・機体マイク（1-2 ch）<br>・音響マップ（1-bit生成，64x64）<br>**[Receive] gst**<br>・スピーカー（操作者マイク）<br>**[Receive] ROS**<br>・頭部 yaw / pitch<br>・腕（ジェスチャ）<br>・Keigan ALI<br>※ Keigan モータ 5 個（yaw / pitch / roll / 両腕）。**roll は通電のみで指令を受けない** |
| PC-C 操作者端末 | OME サーバ・ROS 送出サーバ<br>**[Publish] ROS**<br>・Keigan ALI（ゲームパッド）<br>・頭部 yaw / pitch（PC-D から受けて載せ替え）<br>**[Receive]**<br>・操作 UI 画面（OME Player — 機体マイク＋Xacti Cam+音響マップ） |
| PC-D 高性能サーバ | 推論用の高性能 GPU を積むサーバ<br>**[Receive] gst**（OME 経由）<br>・Xacti Cam<br>・音響マップ<br>・機体マイク・スピーカー（操作者マイク）<br>**[Publish] TCP**（PC-C 経由で ROS 送出）<br>・頭部 yaw / pitch<br>・その他のロボット行動決定 |

### 0.1 4 台の置かれ方

**PC-A / PC-B / PC-C は現地の同じ LAN。PC-D（3090PC）だけ理研にあり、
実験当日もそのまま**（動かせない機械なので、遠隔で繋ぐのが本番構成そのもの）。

**PC-C がハブ**で、PC-D は PC-C としか話さない。両者は Tailscale の同じ
tailnet に正式メンバーとして入る（ノード共有では通らない）。PC-B は同じ LAN
に居るので Tailscale は要らない。実測で往復 75 ms・37〜46 Mbps。

### 0.2 起動順序

**OME（PC-C）を先に立ててから PC-B を起動する。** PC-B の送出は `rtmpsink` で、
接続先が居ないと待ち続け、音響マップの生成と収録まで巻き添えで止まる。
受信側（PC-B の操作者マイク・PC-D の 4 入力）は 5 s ごとに繋ぎ直すので、
**この 1 つを除けば起動順は問わない。**

### 0.3 いまの実装範囲

**最小 demo は遠隔操作まで**（操作者が走らせ、見て、話し、腕を振り、全部 bag に残す）。
この範囲は実装済み。**§4.2 の「誰に向くか」の判断（`decide()`）は未実装**で、
音響マップ上の位置から yaw/pitch への対応付けもまだ決めていない。
自律的に首を向ける demo はこれが決まってから。

## 1. PC-A サイネージ端末

Linux 1 台の Chrome 全画面で巡回表示。気象庁の情報（天気予報・ひまわり衛星画像・地震情報）を 10 秒ごとに切り替える。

PC-A はサイネージ画面の表示のみ。タッチパネルではなく、他 PC との接続も持たない。

## 2. PC-B ロボット miniPC

| 常駐物 | 内容 |
|---|---|
| gst 送出（Xacti映像） | Xacti 1080×1080 を OME サーバへ |
| gst 送出（音響マップ） | 16ch マイクアレイ → 1-bit 音響マップ → OME サーバへ |
| gst 送出（機体マイク） | 現場の音 1〜2ch を OME サーバへ |
| gst 受信（操作者マイク） | OME サーバからの操作者の音声を機体スピーカーへ |
| ROS 受信 | Keigan ALI（台車）、頭部動作（Yaw・Pitch）、ジェスチャ（腕） |

指令を実際にモータへ流す所には、設計の素の対応付けに加えて 3 つだけ足してある。
**どれも無線を越える構成だから要るもので、機体側に置かないと効かない。**

| 足したもの | 何のため |
|---|---|
| 台車の watchdog（0.5 s） | リンクが切れると「止まれ」も届かない。指令が途絶えたら機体側で停止させる |
| 頭部の smoothing（EMA） | VLM の出力が飛んだときに首が急に振れないようにする |
| モータの再接続 | BLE は現地の電波で落ちる。落ちたまま黙って動かないより繋ぎ直す（結果は 10 秒ごとの status に出る） |

## 3. PC-C 操作者端末

| 稼働物 | 内容 |
|---|---|
| OME サーバ | 機体から届く映像・音声・音響マップを受け、操作画面と PC-D へ配る |
| 操作 UI サーバ | 操作画面を出す。ゲームパッドの入力を受けて、台車と腕の指令を ROS へ流す |
| Chrome | 操作画面を開く。映像と音響マップを見ながらゲームパッドで操作する |
| 頭部指令の中継 | PC-D から TCP で届く頭部指令を、ROS に載せ替えて PC-B へ渡す |
| gst 送出（操作者マイク） | 操作者の音声を OME サーバへ |

### 3.1 音響マップの見せ方

**映像と音響マップは別ストリームのまま重ねる**（PC-D が音響マップだけを
取り出せるようにするため）。PC-B は**黒地に黄色の斑点**の画として送り、
操作画面は screen 合成で映像に重ねる。黒い所は映像がそのまま素通りし、
黄色の斑点だけが乗る。強さは画面のスライダで変える。

正規化は `exp(値 - 最大値)`。QC 動画（`soundmap-generator/soundmap-video/
bag2video.py`）と同じ表現で、生成器の GAIN もこの変換を前提に較正されている。
min-max 正規化に替えると較正が外れ、静かな場面でも一面が光って
「どこが鳴っているか」が読めなくなる。

## 4. PC-D 高性能サーバ

### 4.1 入力（gst 受信）

| 対象 | 用途 | 経路 |
|---|---|---|
| Xacti カメラ | 場面理解 | OME サーバから |
| 音響マップ | 誰が喋っているかの手がかり | OME サーバから |
| 機体マイク | 現場の音、発話の有無 | OME サーバから |
| 操作者の音声 | 操作者が何を言ったかの把握 | OME サーバから |

### 4.2 出力

| 対象 | 内容 | 経路 |
|---|---|---|
| 頭部 yaw / pitch | 「誰に向くか」の決定 | TCP/JSON で PC-C へ。PC-C が ROS に載せ替える |
| その他の行動決定 | 発話タイミング、姿勢など | 同上 |

DDS は跨がせない。PC-D は理研にあって distro も違う（galactic / humble で
既定の RMW が別物）一方、跨ぐのは 10 Hz・整数 3 個の topic 1 本だけなので、
素の TCP のほうが確実で、**PC-D 側に ROS が要らなくなる。**

**判断そのもの（`decide()`）は未実装。** 入力を受ける所（4 系統）と、決めた値を
PC-B のモータまで届ける所は通してある。決めていないのは
「音響マップ上のどこが yaw / pitch の何度に当たるか」で、頭部の 0 度は
起動時の姿勢なので、この対応付けは実機で決める。

## 5. PC-B ロボット miniPC でのデータ記録

記録は PC-B に集約し、1 つの rosbag（mcap）に書く。gst 由来のデータも ROS
メッセージに載せるので、映像・音響・指令が同じファイルに入る。

**基準時計は PC-B だけ。** 保存するのは「PC-B が採取したもの」と「PC-B が
受け取ったもの」だけなので、他機との時刻同期（NTP）は要らない。

### 5.1 何が、どう届いて、記録されるか

**下の表では共通の接頭辞 `/<robot>/` を省いてある。** 実際の名前は
`/boxie/camera/video` のようになる。`<robot>` は `common/config.env` の
`ROBOT_NAME`（`boxie`）。

| PC | 発生源 | 信号 | 記録までの経路 | topic | 型 |
|---|---|---|---|---|---|
| PC-B | Xacti カメラ | 1080x1080 映像 | gst の `tee` で送出から分岐 | `camera/video` | `foxglove_msgs/CompressedVideo` |
| PC-B | 機体マイク | 現場音声（1-2ch） | gst の `tee` で送出から分岐 | `onboard_mic/audio` | `AudioDataStamped` |
| PC-B | UMA16v2 | 生データ（16ch） | 送出しない。記録のみ(gst) | `mic_array/audio` | `AudioDataStamped` |
| PC-B | UMA16v2 | 1-bit音響マップ（64×64） | 生成値をそのまま(gst) | `soundmap/raw` | `std_msgs/Float32MultiArray` |
| PC-B | Keigan Motors (5) | モータの接続状態 | ROS (10 s ごと) | `keigan_motor/status` | `BoxieStatus` |
| PC-B | clock_node | 時計 offset | ROS (1 Hz) | `record/clock_offset` | `p3_msgs/ClockOffset` |
| PC-C | 操作者マイク | 音声（1ch） | OME → WebRTC で PC-B が受信 (gst) | `operator_mic/audio` | `AudioDataStamped` |
| PC-C | ゲームパッド | Keigan Ali指令 | ROS (10 Hz・操作中のみ) | `rover/twist` | `geometry_msgs/Twist` |
| PC-C | ゲームパッド | 腕（ジェスチャ）指令 | ROS（ボタン押下時） | `arm/command` | `BoxieMotors` |
| PC-D | VLM の判断 | 頭部指令 | TCP → PC-C の中継 → ROS（変化時のみ） | `head/command` | `BoxieMotors` |

**モータ 5 個は yaw・pitch・roll と両腕。5 個すべてに通電する**が、roll は
指令を受けず初期位置に保つ（通電しないとトルクが掛からず首が揺れる）。
状態は 10 秒ごとに `read_motor_measurement()` で生存を確かめて出す。

音声 3 系統は同じ型で topic だけが違う。**チャネル数とレートは bag に入らない**
ので、復号にはこの表と `common/config.env` が要る。

| topic | channels | sample_rate | sample_format | 1 メッセージ |
|---|---|---|---|---|
| `mic_array/audio` | 16 | 44100 | `S16LE`（interleaved） | 10 ms（441 sample/ch） |
| `onboard_mic/audio` | 2 | 48000 | `S16LE` | 10 ms |
| `operator_mic/audio` | 1 | 16000 | `S16LE` | 到着ごと |

### 5.2 時刻

| 項目 | 方針 |
|---|---|
| 単位 | UNIX 時間（ナノ秒）。ROS 2 のシステム時計はもともとこれなので、揃えるのは gst 側 |
| gst 由来（映像・音声） | パイプラインクロックは MONOTONIC のまま使う（REALTIME にすると NTP の step で PTS が飛ぶ）。`clock_offset` を足して**採取時刻**に換算する。**publish した瞬間の時刻は使わない** |
| **音響マップだけ例外** | `Float32MultiArray` は header を持たないので、**採取時刻を載せる場所が無い。** mcap の `log_time`（publish 時刻）で見る。生成は 7.8 ms/枚 なのでずれはその程度。厳密に要るようになったら自作 msg を足す |
| PC-B 自身の ROS topic | `keigan_motor/status` と `record/clock_offset` の 2 本だけ。`header.stamp` は publish 時刻 |
| **他機から届く topic** | ここだけ注意。`arm/command` と `head/command` の `header.stamp` は **PC-C の時計**で、PC-B とは同期していない。`rover/twist` はそもそも header を持たない。**この 3 本は `header.stamp` ではなく mcap の `log_time`（= PC-B の時計）で見る** |
| 検算 | 収録開始時に手を叩く。映像・機体マイク・16ch に同時に入るので換算の確認に使える |

**基準時計を PC-B だけにしている以上、他機が打った時刻は信用しない。**
mcap の `log_time` は `ros2 bag record`（PC-B）が打つので、跨いで来た
メッセージでも常に PC-B の時間軸に載る。

### 5.3 データ量

| 対象 | 内容 | ビットレート | 1 時間 |
|---|---|---|---|
| Xacti 映像 | H.264 1080×1080（1920×1080 から切り出し） | 約 8 Mbps | 3.6 GB |
| 機体マイク | PCM 48 kHz 16 bit **2ch で計算** | 1.5 Mbps | 0.7 GB |
| 16ch アレイ（生データ） | PCM 44.1 kHz 16 bit 16ch | 11.3 Mbps | 5.1 GB |
| 音響マップ | 64×64 float32 10 Hz | 1.31 Mbps | 0.59 GB |
| 操作者マイク | PCM 16 kHz 16 bit 1ch | 0.26 Mbps | 0.12 GB |
| ROS（指令・状態・offset） | — | — | 0.1 GB 未満 |
| **合計** | | **約 22.4 Mbps** | **約 10.2 GB** |

書き込みは 2.8 MB/s。制約は速度ではなく容量と回収の手間。
**16ch が全体の半分を占める**ので、収録時間の見積もりはここで決まる。

一方、**機体から外へ出る帯域は 10 Mbps 程度**（映像 8 + 音響マップ 2）。
16ch を送っていた頃の約半分になる。
