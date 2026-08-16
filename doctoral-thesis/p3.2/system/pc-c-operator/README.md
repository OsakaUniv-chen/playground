# PC-C 操作者端末

OME サーバ・操作 UI・操作者マイクの送出・PC-D からの頭部指令の中継。
画面に関わるものはすべてここ。設計は [../teleop-architecture.md](../teleop-architecture.md)。

## 起動

```bash
source env.sh
./run.sh              # UI + マイク送出 + 頭部指令の中継
./run.sh status       # 生きているか      ./run.sh stop で停止
```

ログは `log/` に出る。個別に動かすなら `python3 app.py` /
`python3 operator_mic_send.py` / `python3 head_relay.py`。

起動したら **本機のブラウザで `http://localhost:7779/`** を開く。

## 中身

| ファイル | 内容 |
|---|---|
| `app.py` | Flask + SocketIO + ROS publisher。ゲームパッド → `rover/twist`(10 Hz)・`arm/command` |
| `head_relay.py` | PC-D からの TCP/JSON → `head/command`。`HEAD_RELAY_BIND` は tailscale アドレス |
| `operator_mic_send.py` | マイク → OME(RTMP)。**常時オン**（PTT は無い） |
| `templates/`, `static/` | 操作画面。音響マップは screen 合成で映像に重なる（設計 §3.1） |

**右スティックは使わない**（頭部の指令元は PC-D）。速度制限もここには無い ──
スケーリングと停止は PC-B の `rover_driver.py` にある（設計 §2）。

## OME

この機械にネイティブで入っていて systemd で常駐する。docker は使わない。
起動は要らず、状態は `systemctl status ovenmediaengine`、設定は
`/usr/share/ovenmediaengine/conf/Server.xml`。

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
