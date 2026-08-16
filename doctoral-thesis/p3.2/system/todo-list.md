# 現地で確認すること

**ここに残っているのは「まだ決まっていないもの」だけ。** 決まったら値を該当
ファイルに書き込んで、この行ごと消す。チェックを付けて残さない ── この
ファイルの長さがそのまま残作業の量になる。

確認済みのことは [README.md](README.md) §2、決定済みのことは
[teleop-architecture.md](teleop-architecture.md) にある。

---

## 埋めるべき設定

| ファイル | 変数 | 既定値 | 確認方法 | 決まらないと |
|---|---|---|---|---|
| `common/config.env` | `PC_C_IP` | `192.168.1.100` | PC-C で `ip a` | 全経路が繋がらない。**PC-B から OME へ送る先はここだけ** |
| 〃 | `ONBOARD_MIC_CHANNELS` `_RATE` | 2ch / 48000 | `arecord -D <dev> --dump-hw-params` | 対応しない値だと caps 交渉に失敗してパイプラインが起動しない。用途は現場音を聞くことなので 16 kHz でも足りる |
| `pc-b-robot/config.env` | `ONBOARD_MIC_DEVICE` `SPEAKER_DEVICE` | `default`（仮置き） | `arecord -l` / `aplay -l` | **機種が未定。** `default` はシステム既定のカードを指すので、USB を挿し替えると黙って別の機器に移る。決まったら `hw:CARD=<名前>` に固定する |
| 〃 | `ARM_UP_DEG` `ARM_DOWN_DEG` | 0 / 45 度 | ボタンで上げ下げして「挙手」「下ろす」に見える角度に合わせる | 腕が中途半端な位置で止まる |
| `pc-b-robot/config.env` | `ALI_MQTT_HOST` `_PORT` | `192.168.4.2:9075` | 下の rover_driver の節 | 台車が動かない |
| 〃 | `ADDR_HEAD_*` `ADDR_*_ARM` | boxie_node の実機値 | `sudo hcitool lescan` | 該当のモータに繋がらない |
| 〃 | `RECORD_DIR` | `~/p32/rosbags` | `df -h` | **10.2 GB/時**（16ch 5.1 + 映像 3.6）。収録予定時間ぶんの空きが要る |
| `pc-c-operator/config.env` | `OPERATOR_MIC_DEVICE` | `default`（仮置き） | `arecord -l` | **内蔵マイク（ALC285 Analog）が選ばれてしまう。** ヘッドセットの機種が決まったら `hw:CARD=<名前>` に固定する（PC-B の機体マイクと同じ理由） |

映像パラメータ（1920×1080 / 30 fps）と 16ch アレイのパラメータは実機で確認済み。
`OPERATOR_MIC_RATE` は調整不要（記録時のレート。伝送は `OPERATOR_MIC_SEND_RATE`
＝ Opus/RTP の 48000 固定）。

**設定の出所は `config.env` だけ。** 各スクリプトは既定値を持たず、未設定なら
起動時に落ちる。コード側に既定値を二重に書くと片方だけ直したときに黙って
食い違うため（実際 `SOUNDMAP_BITRATE` が 500 と 2000 で割れていた）。

### USB の口を分ける ★

Xacti は **USB 2.0**（MJPG 1080p30 で 24〜40 Mbps）、UMA16v2 は 16ch 44.1 kHz で
**22.6 Mbps**。機体マイクも別に 1 本挿さる。どれも等時転送なので、**同じ
コントローラにぶら下げると取り合う**（コマ落ち、録音の途切れ）。別々の口へ。
`lsusb -t` で確認。

### 文字起こしが現場の声を拾えるか ★（実際に喋って確かめる）

手元で確認できたのは経路まで（サンプル音声を TCP に流して書き起こしが出るところ、
PC-C のマイクが OME 経由で PC-D に 16 kHz 単声道で届くところ）。**人が実際に
喋った音では通していない。**

喋っているかの判定は暗騒音への相対値（直近の 10 パーセンタイルの 3 倍、
`asr.py` の `_SourceBuffer.floor_k`）で、会場が変わっても効くようにしてあるが、
**下限だけは絶対値**（RMS 50）で置いてある。現場で見るのは 2 つ:

| 見るところ | 駄目なとき |
|---|---|
| 普通の声量で `log/asr.log` に行が出るか | 出なければ `floor_k` を下げる。機体マイクは話者から遠いので、こちらが先に効かなくなる |
| 誰も喋っていないのに行が出続けないか | 出るなら `floor_k` を上げる。whisper は無音に対して幻聴（同じ語の繰り返し）を出しやすい |

`ASR_MIN_SPEECH_SEC`（0.4 秒）も、相槌（「はい」）が落ちるようなら下げる。

### 16ch の全 ch に信号が来るか（機体に載せた後にもう一度）

手元では確認済み。組み付けた状態で結線が生きているかを再確認する。
`soundmap_bridge` のログの生データ件数が 100 msg/s か、マップにピークが立つかで見る。

---

## `driver/rover_driver.py` ── ここが今いちばん不確か

沿用元 `blr/rover/twist2alimove.py` の方式（Twist を 8 方向に量子化 → MQTT
`control/joy`）をそのまま使っているが、**実機で確認していない。**

| 項目 | 確認すること |
|---|---|
| ALI の制御インタフェース | 沿用元の broker `192.168.4.2:9075` は indy 側の機械。こちらの ALI が同じ構成か。**ALI は市販品**（<https://www.keigan-ali.com/>）なので公式 SDK で連続速度制御ができないか確認する。できるならそちらへ移す ── 8 方向の離散指令は IRL の action ラベルとしては粗い |
| broker の在り処 | ALI 本体で動くのか、別途立てるのか |
| `deadzone`（0.5） | 沿用元と同じ値。スティックの実際の出力を見て調整 |
| `watchdog_sec`（0.5 s） | 実機で通信を切って挙動を見る。短すぎるとカクつき、長すぎると危ない |
| 速度上限 | **ALI 側の設定で絞る。** 8 方向の離散指令なのでこちらで値を掛けても速度には効かない。人通りのある場所を走らせるので必須 |

## `driver/head_driver.py` ── 実機に載せてから詰める

| パラメータ | 既定値 | 確認方法 |
|---|---|---|
| `max_pitch` / `max_yaw` / `max_arm` | ±30 / ±60 / ±90 度 | **Xacti と 16ch アレイを載せた状態**で、機体やケーブルに当たらない範囲を実測 |
| `speed` / `acceleration` / `torque` | 20.0 / 200.0 / 0.2 | カメラとアレイを載せると慣性が変わる |
| `smooth_alpha` / `smooth_hz` | 0.25 / 10 Hz（tau=0.35 s） | VLM を繋いでから、首の振れ方を見て調整。**alpha は 1 tick あたり**なので、両方を見ないと効きが決まらない |

腕の上げ下げの角度（`ARM_UP_DEG` / `ARM_DOWN_DEG`）と BLE アドレス
（`ADDR_*`）は ROS パラメータではなく `config.env` にある。前者は指令元の
PC-C が持ち、head_driver は受けた絶対角を `max_arm` で丸めるだけ。

## 機体マイクの分離 ★（未検証）

機体マイクを `cam_bridge.py` から `onboard_mic_bridge.py` に切り出し、
全 `rtmpsink` を `sync=false` にした。流れる topic とレートは変えていないが、
**この構成での実測がまだ無い**。

| 見るところ | 期待 |
|---|---|
| `onboard_mic_bridge` が単独で件数を出すか | 10 秒ごとに audio 470 前後 |
| **マイクを抜いた状態でカメラが生きるか** | これが分離した理由。`onboard_mic_bridge` だけが落ちて `camera/video` は流れ続ける |
| `USE_FAKE_SOURCES=0` での往復 | 分離前は `alsasrc buffer-time=200000` が同じパイプラインに居た。**75 ms はフェイク源での値**なので実デバイスで測り直す |
| 操作画面でマップが映像とずれないか | 2 本とも `sync=false` に揃えてある |

## `bridge/cam_bridge.py` ── iGPU が効くか ★（未検証）

カメラは MJPG しか出さず RTMP は MJPG を運べないので H.264 への変換は必ず通る。
その復号と符号化を N100 の iGPU に寄せる実装が入っているが、**実機で
動かしていない**（手元に N100 も vaapi エレメントも無い）。software への
自動フォールバックは実測済みなので**駄目でも起動はする**（起動ログにどちらを
使ったか出る）。狙いは速度ではなく CPU ── software だと 1080×1080@30 で
1.5〜2 コア食い、音響マップ生成と bag 書き込みを圧迫する。

```bash
gst-inspect-1.0 vaapih264enc && vainfo && ls -l /dev/dri/renderD128 && id
```

| 見るところ | 駄目なとき |
|---|---|
| 起動ログに「iGPU（VA-API）で…」が出るか | 出なければ足りないエレメントが警告に並ぶ |
| **Xacti の MJPG を `vaapijpegdec` が復号できるか** | カメラによっては非標準の JPEG を吐く。`vaapijpegdec` だけ `jpegdec` に戻す |
| `constrained-baseline` が出せるか | `vainfo` に無ければ caps 交渉で止まる。`_transcode()` の profile を `main` に |
| **実際に CPU が減ったか** | `top` で software 時と比べる。減っていないなら `USE_HW_CODEC=0` |

---

## ネットワーク

| 項目 | 確認方法 | 駄目なとき |
|---|---|---|
| **機械をまたいだ ICE** ★ | OME まわりは**全部 1 台（loopback）でしか確認していない。** PC-D から `python3 common/ome_receiver.py <stream> --host <PC-C>` | AP がクライアント間通信を塞いでいると UDP が通らない |
| **会場の上り帯域** ★★ | **未測定。** 4 本で約 10 Mbps を会場から理研へ流し続ける。`iperf3` で測る | 映像の解像度／ビットレートを落とすか、送る本数を減らす |
| **DDS が Wi-Fi を越えるか** | PC-C から `ros2 topic list` して PC-B の topic が見えるか | 既定の discovery は UDP マルチキャスト。**AP によっては通らない。** Discovery Server か peer list（`CYCLONEDDS_URI`）でユニキャスト固定に |
| 遅延の体感 | 操作画面と実物を並べて見る | 操作者マイクは AAC→Opus の変換ぶん遅れる |

### 運用時に守ること（理研の PC-D と繋ぐ）

| 項目 | 中身 |
|---|---|
| **PC-C の F5 VPN は切っておく** | `tun0` が理研の公網段 `134.160.0.0/16` を丸ごと掴むため、繋いだままだと Tailscale が直結できず DERP 中継に落ちる（帯域が半分、ばらつきが増える） |
| **本番の数分前に PC-D ↔ PC-C を通信させておく** | Tailscale はまず DERP で繋ぎ、裏で穴あけしてから無音で直結へ昇格する。`tailscale status` が `direct` になってから始める（`tailscale ping -c 3` では昇格前で「直結していない」ように見える） |
| **PC-D への入り方** | `ssh chen@100.104.252.121`（Tailscale 経由）。VPN を切ると踏み台 `kuroko-gw` は使えない |
| VPN を切った後に DNS が壊れたら | F5 VPN は切断時に `/etc/resolv.conf` を戻さないことがある。`/run/systemd/resolve/stub-resolv.conf` への symlink に戻っているか見る |

通らなかった場合の逃げ道: **映像と音声そのものは全部 PC-B の bag にある。**
データを採るだけなら後から bag を回して推論すれば済む。リアルタイム経路が必須なのは
「その場で自律的に首を向ける」を見せるときだけ。

（PC-D も `log/transcript.jsonl` に書き起こしだけは残すようになった。
音そのものは持たないので、bag の代わりにはならない。）

---

## 現地前に入れておくもの

| 対象 | 無いと |
|---|---|
| **`gstreamer1.0-nice`**<br>**残るは PC-B**（C/D は導入済み）★ | **OME からの受信が全部動かない。** 確認は `gst-inspect-1.0 nicesrc` |
| `p3_msgs`（colcon build） | `clock_node` が動かない |
| `foxglove_msgs` | `sensor_msgs/CompressedImage` に退避する（動きはする） |
| `rosbag2_storage_mcap` | `-s mcap` が使えない |
| `gstreamer1.0-vaapi`（PC-B） | 符号化が software のまま。動きはするが CPU を 1.5〜2 コア食う |
| `pykeigan_motor` | 頭部が模擬モードのまま |
| `paho-mqtt` | 台車の指令が MQTT に出ない |
| `flask` `flask-socketio`（PC-C） | 操作画面が出ない |

## 収録の確認（現地で 1 回通す）

| 項目 | 確認方法 |
|---|---|
| 打板マーカーが全系統に入るか | 開始時に手を叩き、映像・機体マイク・16ch を見る。時刻換算の検算になる |
| メッセージ数が理論値と合うか | `ros2 bag info`。30 fps / 100 Hz / 10 Hz / 1 Hz |
| `clock_offset` 系列が滑らかか | 段差があれば NTP が step した時刻。その前後は補正が要る |
| ブリッジと bag record の CPU | `top`。MJPG の復号と H.264 符号化が主な負荷 |
| 書き込みが追いつくか | 収録中に `iostat`（2.8 MB/s） |
| データの回収手順と所要時間 | 実際に 1 回やってみる |

---

## まだ書いていないもの

| 対象 | 状態 |
|---|---|
| **VLM の判断** | `pc-d-server/head_controller.py` の `decide()`。入力は揃っている ── 映像と音響マップは `OmeInputs`、発話は `asr.py` の `Transcriber.text()`（直近 `ASR_CONTEXT_SEC` 秒）。**あわせて「音響マップ上の位置 → yaw/pitch 何度」の対応付けを決める**（頭部の 0 度は起動時の姿勢） |
| **VLM を繋いだあとの文脈窓** | `ASR_CONTEXT_SEC`（既定 15 秒）を実際の会話で詰める。切り出し側（silence / max_segment）は音の切れ目の話なので触らない ── 理由は pc-d-server/README.md §5 |
| **回線が切れたときの頭部の挙動** | **未実装。** 台車には watchdog があるが頭部には無い。リンクが落ちたら現在姿勢を保つのか正面に戻すのかを決める |
| 収録の一括開始・停止 | ROS topic を 1 本立てて開始・停止とセッション ID を揃える。今は PC-B 単独（`./run.sh` が既定で録る） |
| ジェスチャの割り当て | いまは腕の上げ／下げだけ |
| 足元カメラ | UI は 2 系統置ける作りだが 2 本目は未接続 |
