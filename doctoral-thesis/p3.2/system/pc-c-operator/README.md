# PC-C 操作者端末

OME サーバ・操作 UI・操作者マイクの送出・PC-D からの頭部指令の中継。
画面に関わるものはすべてここ。設計は [../teleop-architecture.md](../teleop-architecture.md)。

Ubuntu 22.04 + ROS 2 humble。**この系のハブ**で、PC-B の 3 本と PC-C 自身の
マイクが OME に集まり、PC-D は Tailscale 越しにここだけを見る。

以下 1 → 4 の順に一度やれば、あとは 4 だけを繰り返す。

## 1. フォルダを置く

PC-C に置くのは **`common/` と `pc-c-operator/` の 2 つ**。**必ず並べて置く。**
`env.sh` が `../common/config.env` を相対パスで引くので、`pc-c-operator/` だけ
配ると起動しない。

手元（この repo）の `system/` で:

```bash
rsync -a --exclude __pycache__ common pc-c-operator <user>@<PC-C の IP>:~/p32/
```

```
~/p32/common/          config.env（設定の唯一の出所）
~/p32/pc-c-operator/   2 以降の作業はすべてこのディレクトリで行う
```

## 2. 依存を入れる

```bash
# ROS 2（rover/twist・arm/command・head/command を publish する）
sudo apt install ros-humble-desktop

# 操作者マイクの送出に使う（voaacenc と rtmpsink が bad、flvmux と aacparse が good）
sudo apt install gstreamer1.0-tools gstreamer1.0-plugins-{base,good,bad} python3-gi

# 操作 UI
pip install flask flask-socketio
```

**`gstreamer1.0-nice` はここには要らない**（PC-B と PC-D だけ）。PC-C から出るのは
RTMP で、WebRTC を喋るのは OME 本体とブラウザ ── どちらも gst を使わない。

**`audio_common_msgs` は `~/ros2_ws` にある沿用版を使う。** `BoxieMotors` は
この版にしか無い。**apt の `ros-humble-audio-common-msgs`（4.x）は入れない**
（理由は [../README.md](../README.md) §1.3）。`p3_msgs` は PC-B だけが使うので
ここでは要らない。

### ブラウザ用の JS を手元に置く（vendor 化）

**CDN のままだと現地にインターネットが無い時に操作画面ごと落ちる。**
`static/vendor/` は空で配られるので、**接続がある場所で 1 回落としておく**:

```bash
cd ~/p32/pc-c-operator
mkdir -p static/vendor
curl -L -o static/vendor/socket.io.min.js https://cdn.socket.io/4.7.5/socket.io.min.js
curl -L -o static/vendor/ovenplayer.js    https://cdn.jsdelivr.net/npm/ovenplayer/dist/ovenplayer.js
```

socket.io は **4.x** を取る（サーバ側の Flask-SocketIO 5.x と対になる版）。

### OME

**この機械にネイティブで入っていて systemd で常駐する。docker は使わない。**
入れ直す必要は無いはずだが、状態だけは起動前に見る:

```bash
systemctl status ovenmediaengine        # 設定は /usr/share/ovenmediaengine/conf/Server.xml
```

## 3. 設定を埋める

★ の付いた項目を現地で確認して書く。確認方法は [../todo-list.md](../todo-list.md)。

| ファイル | 埋めるもの |
|---|---|
| `../common/config.env` | `PC_C_IP`（この機械の LAN アドレス。PC-B が RTMP を投げる先） |
| `config.env` | `OPERATOR_MIC_DEVICE`、`HEAD_RELAY_BIND`（PC-D から届く必要があるので `tailscale ip -4` の値） |

`UI_PORT`（7779）と `UI_SECRET` は `config.env` にある。

`../common/config.env` の `USE_FAKE_SOURCES` は PC-C にも効く ── `1` にすると
`operator_mic_send.py` がマイクの代わりに 440 Hz の正弦波を OME へ流す
（既定は `0` = 実マイク）。**PC-B と同じ 1 行で両方が切り替わる**ので、
机上で試すときは 2 台とも同じ値になっているか見ておく。

## 4. 起動

```bash
cd ~/p32/pc-c-operator
source env.sh
./run.sh              # UI + マイク送出 + 頭部指令の中継
./run.sh status       # 生きているか      ./run.sh stop で停止
```

**PC-B より先に立てる**（設計 §0.2）。OME は systemd で常駐しているので
`run.sh` は起動しない。

起動したら **本機のブラウザで `http://localhost:7779/`** を開く。

ログは `log/` に出る。個別に動かすなら `python3 app.py` /
`python3 operator_mic_send.py` / `python3 head_relay.py`。

### 起動前の確認

```bash
python3 -c "import flask, flask_socketio, gi, rclpy; print('python OK')"
ros2 interface show audio_common_msgs/msg/BoxieMotors >/dev/null && echo "audio_common_msgs OK"
ls static/vendor/socket.io.min.js static/vendor/ovenplayer.js   # 2 つとも要る
systemctl is-active ovenmediaengine
ip -4 -o a                                                      # PC_C_IP と一致するか
```

## 中身

| ファイル | 内容 |
|---|---|
| `app.py` | Flask + SocketIO + ROS publisher。ゲームパッド → `rover/twist`(10 Hz)・`arm/command` |
| `head_relay.py` | PC-D からの TCP/JSON → `head/command`。`HEAD_RELAY_BIND` は tailscale アドレス |
| `operator_mic_send.py` | マイク → OME(RTMP)。**常時オン**（PTT は無い） |
| `templates/`, `static/` | 操作画面。音響マップは screen 合成で映像に重なる（設計 §3.1） |

**右スティックは使わない**（頭部の指令元は PC-D）。速度制限もここには無い ──
スケーリングと停止は PC-B の `rover_driver.py` にある（設計 §2）。

### OME の stream key

| stream key | 向き | 中身 |
|---|---|---|
| `<robot>stream` / `<robot>mic` / `<robot>soundmap` | PC-B → | 映像・機体マイク・音響マップ |
| `operatormic` | PC-C → | 操作者マイク（PC-B と PC-D が受ける） |

## つまずきやすい点

- **操作画面は同一機の `localhost` から開くこと。** Gamepad API とマイク取得は
  secure context を要求し、`http://localhost` はそれを満たす。別の機械から
  開くと成立せず、HTTPS と wss（3334）と証明書が要る
- **ネットワークが確定してから OME を再起動する。** OME は起動時に一度だけ
  NIC を列挙し、その住所を ICE candidate として配り続ける。後から変わっても
  直らず、**SDP は成功するのにメディアだけ永久に来ない。**
  `ome_receiver.py` は自衛するが**ブラウザはしない** ── 操作画面だけ
  映らないときはまずこれを疑う（`ip -4 -o a` と突き合わせる）
- `static/vendor/` に `socket.io.min.js` と `ovenplayer.js` を置く。**CDN のままだと
  現地にインターネットが無い時に操作画面ごと落ちる**
