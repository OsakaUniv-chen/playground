# PC-C 操作者端末

OME サーバ・操作 UI・操作者マイクの送出・PC-D からの頭部指令の中継。
画面に関わるものはすべてここ。設計は [../teleop-architecture.md](../teleop-architecture.md)。

**この系のハブであり、開発機でもある。** PC-B の 3 本と PC-C 自身のマイクが
OME に集まり、PC-D は Tailscale 越しにここだけを見る。

**コードは repo の置き場所からそのまま動かす。** PC-B / PC-D のように
`~/p32` へ配る必要は無い ── 配る側がここなので。依存もこの機械で直に
試せるので、apt / pip の一覧はここには持たない（動かなければ log を見る）。

## 起動

```bash
./run.sh              # UI + マイク送出 + 頭部指令の中継
./run.sh status       # 生きているか      ./run.sh stop で停止
```

`run.sh` が中で `env.sh` を読むので `source` は要らない。**PC-B より先に
立てる**（設計 §0.2）。

**OME を起動する操作は要らない。** この機械で systemd 常駐かつ `enabled`
なので、電源を入れた時点で上がっている。`run.sh` も触らない。見るだけ:

```bash
systemctl is-active ovenmediaengine
```

`./run.sh` の後に、**本機のブラウザで `http://localhost:7779/`** を開き、
**ゲームパッドのボタンを 1 回押す。**

押すまで認識されないのはブラウザの仕様。指紋採取を防ぐため、Gamepad API は
ページに焦点がある状態で操作されるまでパッドを見せず、`gamepadconnected` も
飛ばない。**挿すだけでは駄目**（挿す順は前後どちらでもよい）。認識できたら
console に `Gamepad connected: <型番>` が出る。

つまり現地でやることは **`./run.sh` → ブラウザを開く → パッドを 1 回押す**
の 3 つで、その後に PC-B を起動する。

ログは `log/<名前>.log`（`ui` / `mic` / `relay`）。`./run.sh status` が
「死んでいる」と言ったらそこを見る。1 本だけ直しながら動かすなら
`source env.sh` してから `python3 app.py` のように直接叩く。

## 設定

`config.env` に `UI_PORT`(7779) / `UI_SECRET` / `OPERATOR_MIC_DEVICE` /
`HEAD_RELAY_BIND`。`../common/config.env` に `PC_C_IP`（この機械の LAN
アドレス。PC-B が RTMP を投げる先）。

- `HEAD_RELAY_BIND` は **tailscale のアドレス**（`tailscale ip -4`）。PC-D から
  届く必要がある
- `USE_FAKE_SOURCES` は PC-C にも効く。`1` にすると `operator_mic_send.py` が
  マイクの代わりに 440 Hz の正弦波を流す（既定 `0` = 実マイク）。**PC-B と
  同じ 1 行**なので、机上で試すときは 2 台で値が揃っているか見ておく

## 中身

| ファイル | 内容 |
|---|---|
| `app.py` | Flask + SocketIO + ROS publisher。ゲームパッド → `rover/twist`(10 Hz)・`arm/command` |
| `head_relay.py` | PC-D からの TCP/JSON → `head/command`。`HEAD_RELAY_BIND` は tailscale アドレス |
| `operator_mic_send.py` | マイク → OME(RTMP)。**常時オン**（PTT は無い） |
| `templates/`, `static/` | 操作画面。音響マップは screen 合成で映像に重なる（設計 §3.1） |

**右スティックは使わない**（頭部の指令元は PC-D）。速度制限もここには無い ──
スケーリングと停止は PC-B の `rover_driver.py` にある（設計 §2）。

### OME

この機械にネイティブで入っていて systemd で常駐する。docker は使わない。
設定は `/usr/share/ovenmediaengine/conf/Server.xml`。

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
- **`static/vendor/` の JS は repo に入れてある**（socket.io 4.7.5 /
  OvenPlayer 3.40.0）。CDN 参照に戻さないこと ── 現地にインターネットが
  無いと操作画面ごと落ちる
