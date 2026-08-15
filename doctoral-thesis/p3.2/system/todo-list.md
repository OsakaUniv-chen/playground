# 現地で確認すること

ファイルごとにまとめてある。

**決まった項目はこの表から削除する。** 値を該当ファイルに書き込んだら、
チェックを付けて残すのではなく行ごと消す。ここに残っているのは常に
「まだ決まっていないもの」だけになり、このファイルの長さがそのまま
残作業の量になる。

`pc-b-robot/gst/probe_devices.sh` を最初に走らせると、デバイス名まわりは
一度に分かる。

---

## `common/config.env`

| 変数 | 既定値 | 確認方法 | 決まらないと |
|---|---|---|---|
| `PC_C_IP` | `192.168.10.12` | PC-C で `ip a` | 全経路が繋がらない。**PC-B から OME へ送る先はここだけ**（PC-B / PC-D のアドレスは誰も参照しないので config.env に無い） |
| `ONBOARD_MIC_CHANNELS` `ONBOARD_MIC_RATE` | 2ch / 48000 | `arecord -D <dev> --dump-hw-params` | デバイスが対応しない値だと caps 交渉に失敗してパイプラインが起動しない。用途は現場音を聞くことなので 16 kHz でも足りる |

`OPERATOR_MIC_RATE` は調整不要。記録時のレートで、伝送は Opus/RTP の
clock-rate 48000 固定（RFC 7587）。

映像パラメータ（1920×1080 / 30 fps）と 16ch アレイのパラメータは実機で確認済み。

---

## `pc-b-robot/config.env`

| 行 | 変数 | 既定値 | 確認方法 | 決まらないと |
|---|---|---|---|---|
| 8 | `CAM_DEVICE` | `/dev/video0` | `ls -l /dev/v4l/by-path/` | `/dev/videoN` は起動順で変わる。**by-path か by-id を使う**。あわせて Xacti が MJPG 1920×1080@30 を出せるかを `v4l2-ctl --list-formats-ext` で確認する |
| 22–23 | `ONBOARD_MIC_DEVICE` `SPEAKER_DEVICE` | `default` | `arecord -l` / `aplay -l` | 機体マイクとスピーカーが鳴らない |
| 26–27 | `ALI_MQTT_HOST` `ALI_MQTT_PORT` | `192.168.4.2:9075` | 下の rover_driver の節 | 台車が動かない |
| 30 | `RECORD_DIR` | `~/p32_bags` | `df -h` | **9.6 GB/時**（うち 16ch が 5.1 GB）。収録予定時間ぶんの空きが要る |

### 16ch の全 ch に信号が来るか（機体に載せた後にもう一度）

手元では 16 ch すべてに信号が来ることを確認済み。機体に組み付けた状態で
結線が生きているかを再確認する。`soundmap_bridge` のログに出る生データの
メッセージ数が期待値（100 msg/s）どおりか、音響マップにピークが立つかで見る。

---

## `pc-b-robot/driver/rover_driver.py`

**この節が今いちばん不確かなところ。** 沿用元 `blr/rover/twist2alimove.py` の
方式（Twist を 8 方向に量子化 → MQTT `control/joy`）をそのまま使っているが、
**実機で確認していない。**

| 場所 | 項目 | 確認すること |
|---|---|---|
| ファイル全体 | ALI の制御インタフェース | 沿用元の MQTT broker `192.168.4.2:9075` は indy 側の機械。こちらの ALI が同じ構成か。**ALI は市販品**（<https://www.keigan-ali.com/>）なので、公式 SDK で連続速度制御ができないか確認する。できるならそちらへ移す — 8 方向の離散指令は IRL の action ラベルとしては解像度が粗い |
| — | broker の在り処 | ALI 本体で動くのか、別途立てるのか |
| `declare_parameter("deadzone")` | しきい値 0.5 | 沿用元と同じ値。スティックの実際の出力を見て調整 |
| `declare_parameter("watchdog_sec")` | 0.5 s | 実機で通信を切って挙動を見る。短すぎるとカクつき、長すぎると危ない |
| — | 速度上限 | **ALI 側の設定で絞る。** 8 方向の離散指令なので、こちら側で値を掛けても速度には効かない。人通りのある場所を走らせるので必須 |

---

## `pc-b-robot/driver/head_driver.py`

| 場所 | パラメータ | 既定値 | 確認方法 |
|---|---|---|---|
| `declare_parameter("max_*")` | pitch ±30 / yaw ±60 / 腕 ±90 度 | 既存 `boxie_node` の値 | **Xacti と 16ch アレイを載せた状態**で、機体やケーブルに当たらない範囲を実測して詰める |
| `arm_up_deg` / `arm_down_deg` | 0 / 45 度 | 既存 `boxie_node` は 45 度を「下ろした位置」として初期化 | ボタンで実際に上げ下げして、見た目が「挙手」「下ろす」になる角度に合わせる |
| `declare_parameter("speed")` ほか | speed 20.0 / accel 200.0 / torque 0.2 | 同上 | カメラとアレイを載せると慣性が変わる |
| `declare_parameter("smooth_alpha")` | 0.6 | 同上 | VLM の出力が飛んだときの首の振れ方を見て調整 |

---

## `pc-b-robot/bridge/soundmap_bridge.py`

生成・記録・OME への送出まで実機で確認済み（16ch 44.1 kHz → 10 Hz、7.8 ms/枚、
push 失敗 0 回）。

| 項目 | 確認方法 |
|---|---|
| ブラウザで音響マップが見えるか | `ws://<PC-C>:3333/app/<robot>soundmap` を OvenPlayer で開く。表示側の UI は未実装 |
| **RTMP 先が居ないときに詰まる** | `rtmpsink` が接続先を待ち続け、生成も記録も巻き添えで止まる。**OME（PC-C）が立っていることを確認してから PC-B を起動する** |
| `SOUNDMAP_HZ` | 既定 10 Hz。VLM の推論周期に合わせる（N100 実測で最大 27 Hz） |

---

## OME 経由の受信（`common/ome_receiver.py`）

**メディアまで流れることを実測で確認済み。** PC-D の 4 入力と PC-B の
操作者マイク受信の両方が動く。以下は再調査しないこと。

| 確認したこと | 結果 |
|---|---|
| 映像 1 本 | 30 fps 定常。ICE が `completed`、PeerConnection `connected` |
| PC-D の 4 入力を 1 プロセスで並行受信 | 映像 30 fps・マップ 10 fps・音声 2 系統が同時に流れる |
| 操作者マイク PC-C → OME → PC-B | ROS へ 50 msg/s。48 kHz を受けて 16 kHz へ落として記録 |
| PTT | `volume` の mute で消音。ストリームは切れずに流れ続ける |
| 送出が後から立ち上がる場合 | 受信側が 5 s ごとに繋ぎ直して拾う。**起動順を気にしなくてよい** |

### ただし全部 1 台（loopback）での確認 ★

**PC-B / PC-C / PC-D を別々の機械にして試していない。** 現地で最初に見るのはここ。

| 項目 | 確認方法 | 駄目なとき |
|---|---|---|
| 機械をまたいだ ICE | PC-D から `python3 common/ome_receiver.py <stream> --host <PC-C>` | AP がクライアント間通信を塞いでいると UDP が通らない。`--turn` を付けると OME 内蔵 TURN(TCP) 経由になる |
| 遅延 | 操作画面と実物を並べて見る | 操作者マイクは AAC→Opus の変換ぶん遅れる。会話が成り立たないほどなら経路を見直す |

### OME が古い IP を配る ★

**OME は起動時に一度だけ NIC を列挙し、その住所を ICE candidate として配り続ける。**
後から DHCP で変わっても直らない。実際にこれで詰まった（SDP 交換は成功し、
OME 側にセッションも立つのに、メディアだけ永久に来ない）。

`ome_receiver.py` は候補のアドレスを signalling で繋いだ先に書き換えて
自衛しているので受信側は困らないが、**ブラウザ（OvenPlayer）は書き換えない。**
操作画面が映らないときはこれを疑う。

```bash
ip -4 -o a                                  # 実際のアドレス
python3 common/ome_receiver.py <stream> -v  # OME が配っているアドレス
sudo systemctl restart ovenmediaengine      # ネットワークが上がった後に再起動する
```

現地では **PC-C のネットワークが確定してから OME を再起動する**運用にする。

---

## `pc-c-operator/config.env`

| 行 | 変数 | 既定値 | 確認方法 |
|---|---|---|---|
| 8 | `OPERATOR_MIC_DEVICE` | `default` | `arecord -l` |

### `pc-c-operator/static/vendor/`

`socket.io.min.js` と `ovenplayer.js` を置く。**CDN のままだと現地に
インターネットが無い時に操作画面ごと落ちる。** 現地前に済ませる。

---

## `pc-c-operator/ome/`

RTMP 入力が有効なこと、`app` が定義済みなこと、音声のみのストリームも
受け付けることは確認済み。機体から出るのは 3 本。

| stream key | 中身 |
|---|---|
| `<robot>stream` | Xacti 映像 |
| `<robot>mic` | 機体マイク |
| `<robot>soundmap` | 1-bit 音響マップ |

---

## PC-D だけ別の場所にある ★★

**PC-A / PC-B / PC-C は同じ LAN。PC-D は理研にあり、同じ LAN に居ない。**
設計（§4）は 4 台が同じ LAN に居る前提で書かれているので、ここは決め直しが要る。

PC-B ↔ PC-D をまたぐ依存は 2 本だけ。**16ch の生データを PC-D へ送る経路は
もう無い**（音響マップを PC-B で作るようになったため。関連する残骸は削除済み）。

| 方向 | 中身 | インターネット越しに |
|---|---|---|
| OME(PC-C) → PC-D | 映像・音響マップ・機体マイク・操作者マイクの 4 本 | 経路さえ作れば通る。OME は TURN(3478/TCP) を内蔵していて TCP だけでも運べる |
| PC-D → PC-B | `/<robot>/head/command`（`BoxieMotors`）を ROS 2 DDS で | **ここが問題** |

### DDS が越えられない

既定の discovery は UDP マルチキャストで、インターネットは越えない。
Discovery Server や peer list でユニキャストに固定しても、両端が NAT の内側に
居るので穴あけが要る。**DDS は既定で暗号化されない**ので、domain をそのまま
公網に晒すのも避けたい。

（この表の下にある「DDS が Wi-Fi を越えるか」は**同じ AP の中**の話で、
こちらとは別問題。）

### WebRTC 側の前提

PC-D から PC-C の 3333（signalling）・3478（TURN/TCP）・10000-10004/udp が
見えること。PC-C は家庭用ルータの内側（`192.168.1.7`）で **IPv4 の着信は無い。**

| 手 | 要ること |
|---|---|
| VPN | 下記。いちばん素直 |
| IPv6 直結 | PC-C は全球 IPv6 を持っている（`2400:2410:...`）。ただし OME の IPv6 ICE candidate は `Server.xml` で**コメントアウトされたまま**なので開ける必要がある。理研側も IPv6 が要る |
| ポート開放 | ルータの権限と固定 IP か DDNS |

### PC-D への唯一の経路は SSH（実際の構成）

```
Host Riken          HostName kuroko-gw.ad180.riken.jp   User grp
Host 3090PC         HostName 192.168.3.44                User chen   ProxyJump Riken
```

PC-D は理研の内側（`192.168.3.68`）で、**踏み台 SSH 以外の入口が無い。**

- この機械の `tun0` は `172.18/22/27/30.x` へ経路を持つが、**`192.168.3.0/24` は
  含まれない。** つまり既存 VPN では PC-D に届かない
- 踏み台 `Riken` 経由の鍵認証は**通るようになった**（実機で確認済み）
- PC-C も家庭用ルータの内側で、**IPv4 の着信が無い**

**つまり両側とも着信できない。** 唯一確実に通るのは
「PC-C から SSH で PC-D へ出て行く」方向だけ。

### 実機で試した結果（2026-08-15）★

PC-C から `-R 3333 / 3478 / 7997` のトンネルを張り、理研の PC-D から実行した。

| 経路 | 結果 |
|---|---|
| **頭部指令 PC-D → PC-C → ROS** | **通った。** 10 Hz で送って `head/command` に 99 msg/10s。純粋な TCP なのでトンネルに素直に乗る |
| **メディア OME → PC-D（WebRTC）** | **通らない。** ICE は `connected` まで行くがメディアが来ない |

メディアが来ない理由: **PC-D の GStreamer は 1.16.3**（PC-C は 1.20.3）で、
TURN の `?transport=tcp` が効かず **relay candidate が採れない**
（gathering が 0.3 秒で complete し、relay 候補が 1 つも出ない）。
SSH は TCP しか運べないので ICE の UDP（10000-10004）も通らず、
結果として使える経路が無い。

PC-D 側で分かったこと:

| 項目 | 値 |
|---|---|
| OS / Python | Ubuntu 20.04.6 / Python 3.8.10 |
| GStreamer | **1.16.3**（`latency` プロパティは 1.18 から。無い物を触ると全部止まるので存在確認してから設定するようにした） |
| `nicesrc` / `webrtcbin` | 導入済み |
| **`gir1.2-gst-plugins-bad-1.0`** | **未導入。** GstWebRTC の typelib が入っておらず `Namespace GstWebRTC not available` で落ちる。`sudo apt install gir1.2-gst-plugins-bad-1.0` |
| ROS | foxy / galactic / noetic（humble は無い。**使わないので問題にならない**） |

### → メディアは Tailscale にするのが早い ★

**PC-D には既に Tailscale が入って動いている**（`100.104.252.121`、
tailnet に他の機械も居る）。**PC-C に入れて同じ tailnet に入れれば、
UDP がそのまま通るので TURN も SSH トンネルも要らなくなる。**

- WebRTC は素の UDP で繋がる（`OME_USE_TURN=0`、`OME_HOST` は PC-C の
  tailscale アドレス）
- 頭部指令の中継も同じ経路で届く
- 古い GStreamer の TURN 実装に依存しなくなる

SSH トンネルは「頭部指令だけ通せばよい」場合の逃げ道として残す。

### 手 1: SSH のポート転送（メディアには足りない）

SSH の `-R`（リモート転送）で、**PC-C 側のポートを PC-D の localhost に生やす。**
PC-C から出て行く接続だけで両方向が作れるので、着信が無くても成立する。

```bash
# PC-C で張りっぱなしにする（3333=signalling, 3478=TURN/TCP）
ssh -N -R 3333:localhost:3333 -R 3478:localhost:3478 3090PC

# PC-D 側では OME が localhost に居るように見える
python3 common/ome_receiver.py <stream> --host 127.0.0.1 --turn
```

**`--turn` が必須。** SSH が転送できるのは TCP だけで、ICE の
UDP（10000-10004）は通らない。OME は `TcpForce=true` で TURN/TCP 経由の
再生を既に想定しているので、そこに乗せる。`ome_receiver.py` は
TURN のアドレスも signalling で繋いだ先（=`127.0.0.1`）に書き換えるので、
OME が「トンネルの向こうからどう見えるか」を知らなくても繋がる。

見積もっておくこと:

- 4 本で約 10 Mbps を SSH の 1 本に通す。**TCP の中に TCP** を通す形なので、
  ロスがあると急に悪くなる。まず 1 本（映像だけ）で測る
- 切れたときに張り直す仕組みが要る（`autossh`、または systemd で `Restart=always`）

### PC-D だけ ROS の distro が違う ★

**PC-B / PC-C は humble、PC-D は galactic。** ただし
**PC-D は ROS を使わない構成にしたので、この差は問題にならなくなった**
（頭部指令は下の TCP 中継、受信は rclpy 不要）。`common/config.env` は
`/opt/ros/*/setup.bash` を拾う形にしてある。

以下は「もし DDS を跨がせるなら」何が起きるかの記録:

| 問題 | 中身 |
|---|---|
| 既定 RMW が違う | galactic は CycloneDDS、humble は FastDDS。**素のままでは互いを見つけられない** |
| distro 間通信は非対応 | galactic ↔ humble は公式にサポートされない組み合わせ。動く場合もあるが保証は無い |
| galactic は EOL | 2022-11 でサポート終了 |
| 自作 msg | `BoxieMotors` を PC-D 側でもビルドする必要がある |

### CycloneDDS で PC-D → PC-B を繋げるか

**RMW を揃える役には立つが、それだけでは繋がらない。**

- **効く点**: PC-B に `ros-humble-rmw-cyclonedds-cpp` を入れて両側
  `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` にすれば、RMW の不一致は消える
  （galactic 側は既定でこれ）
- **効かない点**: **CycloneDDS は NAT 越えをしない。** 穴あけも中継も持たない。
  PC-C も PC-D も着信ポートが無い以上、Cyclone を使っても経路は生えない。
  結局 SSH トンネルか VPN が別途要る
- Cyclone は peer 明示と TCP 転送に対応するので理屈の上ではトンネルに乗るが、
  discovery を単一の転送で通すのは面倒で壊れやすい
- galactic ↔ humble が非対応な点は Cyclone にしても消えない

**10 Hz・整数 3 個の topic 1 本のために背負う仕掛けとしては重すぎる。**

### → 素の TCP 中継にした（実装済み）

    PC-D `infer/head_controller.py` ── TCP/JSON ──>
    PC-C `head_relay.py` ── ROS ──> PC-B `head_driver.py`

NAT も distro 差も同時に消え、**PC-D に ROS が要らなくなった**
（受信側 `recv_ome.py` はもともと rclpy を使わない）。接続は SSH トンネルに
相乗りする（`HEAD_RELAY_PORT` を `-R` に足してある）。

1 台での実測: 10 Hz で `/boxie/head/command` に届き、中継を落とすと
5 秒ごとに再試行、戻すと自動で繋ぎ直す。**理研との間ではまだ未確認。**

### 手 2: VPN を 3 台に入れる

VPN は各機に**もう 1 枚仮想の NIC を生やして、共通の私有帯
（例 `10.x.x.x`）の住所を配る**もの。物理的に離れていても同じ LAN に
居るかのように直接指定できるようになり、**どちら側も着信ポートを開けなくてよい**
（各機が VPN のサービスへ外向きに繋ぎに行くだけ）。Tailscale なら
NAT 越えと中継まで面倒を見る。

- `pc-d-server/config.env` の `OME_HOST` に VPN の住所を書くだけで済む
- UDP がそのまま通るので WebRTC は TURN 無しで済み、SSH より素直
- **PC-B にも入れる**（頭部指令の宛先だから）
- 理研の機械に入れてよいかは要確認

### DDS をそのまま跨がせない

どちらの手でも、**ROS 2 の DDS を PC-D と PC-B の間でそのまま喋らせるのは
避けたほうがよい。** 跨ぐのは `/<robot>/head/command` の 1 本・10 Hz・
整数 3 個だけで、そのためにマルチキャスト discovery と動的ポートを
インターネット越しに通すのは割に合わない（SSH 転送では特に無理）。

素直なのは、**PC-D からは素の TCP か WebSocket で PC-C へ送り、
PC-C 側の小さな中継が ROS に publish し直す**形。PC-C は既に ROS の
publisher（台車の twist）を持っているので置き場所として自然で、
PC-B から見れば今までどおり同じ LAN の中の topic のまま。

### 通らなかった場合の逃げ道

**記録は全部 PC-B に集約されていて、PC-D は記録しない（設計 §5.1）。**
データを採るだけなら PC-D は後から bag を回して推論すれば済み、
リアルタイムの経路は要らない。**「その場で自律的に首を向ける」**を
見せる必要があるときだけ、上の経路が必須になる。
どちらを要求するのかを先に決めると、ネットワークにかける手間が変わる。

### 帯域と遅延

OME は機体から受けた約 10 Mbps を、そのまま PC-D へ**もう一度上りで**流す。
PC-C の上り帯域を確認する。遅延も 経路のぶん増えるので、
頭部指令が現場の動きに対して遅れないかは実測で見る。

---

## ネットワーク全体

| 項目 | 確認方法 | 決まらないと |
|---|---|---|
| **DDS が Wi-Fi を越えるか** | PC-C から `ros2 topic list` して PC-B の topic が見えるか | 既定の discovery は UDP マルチキャスト。**AP によっては通らない。** 通らなければ Discovery Server か peer list（`CYCLONEDDS_URI`）でユニキャスト固定にする |
| AP の実効帯域 | `iperf3` | 機体から出るのは映像 8 Mbps + 音響マップ + 機体マイクで 10 Mbps 程度。16ch の生データは機体内に留まる |

---

## 収録の確認（現地で 1 回通す）

| 項目 | 確認方法 |
|---|---|
| 打板マーカーが全系統に入るか | 収録開始時に手を叩き、映像・機体マイク・16ch を見る。時刻換算の検算になる |
| メッセージ数が理論値と合うか | `pc-b-robot/record/check_bag.sh <bag>` |
| `clock_offset` 系列が滑らかか | bag から読む。段差があれば NTP が step した時刻。その前後は補正が要る |
| ブリッジと bag record の CPU | `top`。MJPG の復号と H.264 符号化が主な負荷 |
| 書き込みが追いつくか | 収録中に `iostat` |
| データの回収手順と所要時間 | 実際に 1 回やってみる |

---

## どの PC に `gstreamer1.0-nice` が要るか

判断は「`webrtcbin` を動かすか」だけ。**PC-C と PC-D は導入済み。残るは PC-B。**

| 機械 | 要否 | 理由 |
|---|---|---|
| **PC-B** | **要る（未導入 ★）** | `operator_mic_bridge.py` が OME から操作者音声を WebRTC で受ける |
| PC-D | 要る・**導入済み** | `recv_ome.py` の 4 入力すべてが webrtcbin |
| PC-C | 実運用では不要 | OME は原生バイナリで自前の WebRTC を持ち、gst は使わない。操作者マイクの送出は RTMP。画面は Chrome 側の WebRTC。**`ome_receiver.py` で疎通を見るときだけ要る**（導入済み） |
| PC-A | 不要 | Chrome の巡回表示のみ |

```bash
gst-inspect-1.0 nicesrc      # 入っていれば Factory Details が出る
```

---

## 現地前に入れておくもの

| 対象 | コマンド | 無いと |
|---|---|---|
| **`gstreamer1.0-nice`**<br>**残るは PC-B**（C/D 導入済み） | `sudo apt install gstreamer1.0-nice` | **OME からの受信が全部動かない。** `libnice10` だけでは足りない。webrtcbin は libnice を直接リンクしているが、ICE の実体は `nicesrc`/`nicesink` という別パッケージの gst エレメント。無いと `libnice elements are not available` の警告だけ出て `create-answer` が黙って失敗する。確認は `gst-inspect-1.0 nicesrc` |
| `p3_msgs` | `cp -r common/p3_msgs ~/ros2_ws/src/ && colcon build --packages-select p3_msgs` | `clock_node` が動かない |
| `foxglove_msgs` | `sudo apt install ros-humble-foxglove-msgs` | `sensor_msgs/CompressedImage` に退避する（動きはする） |
| `rosbag2_storage_mcap` | `sudo apt install ros-humble-rosbag2-storage-mcap` | `-s mcap` が使えない |
| `pykeigan` | `pip install pykeigan_motor` | 頭部が模擬モードのまま |
| `paho-mqtt` | `pip install paho-mqtt` | 台車の指令が MQTT に出ない |

---

## まだ書いていないもの

| 対象 | 場所 |
|---|---|
| VLM 推論 | `pc-d-server/infer/head_controller.py` の `decide()`。入力は `pc-d-server/gst/recv_ome.py` の `OmeInputs` から取れる状態になっている |
| 収録の一括開始・停止 | 設計 §5.3 の「ROS topic で揃える」。今は PC-B 単独 |
| 足元カメラ | UI は 2 系統置ける作りだが 2 本目は未接続 |
