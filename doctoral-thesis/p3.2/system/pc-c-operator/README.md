# PC-C 操作者端末

OME サーバ・操作 UI サーバ・ブラウザ・操作者マイクの送出。画面に関わるものは
すべてここに集約する。

## 起動

```bash
source env.sh

python3 app.py                    # 操作 UI サーバ（:7779）
python3 gst/operator_mic_send.py  # 操作者マイク -> PC-B（PTT 待ち）
./ome/run_ome.sh                  # OME（docker）
```

まとめて起動するなら（OME は systemd で常駐しているので含めない）：

```bash
./run.sh              # UI + マイク送出 + PC-D へのトンネル。ログは log/ に出る
./run.sh status       # 生きているか
./run.sh stop         # 停止
```

### PC-D（理研）へのトンネル

**PC-D は理研にあって同じ LAN に居ない。** PC-D 側にも PC-C 側にも着信ポートが
無いため、**PC-C から SSH で出て行く 1 本**で OME を PC-D の localhost に生やす。
`config.env` の `PCD_SSH_HOST` を設定しておくと `run.sh` が張る（空なら張らない）。

```bash
ssh -N -R 3333:localhost:3333 -R 3478:localhost:3478 3090PC
```

`-R` は「こちらのポートを向こうの localhost に生やす」向き。SSH は TCP しか
運べないので、PC-D 側は OME 内蔵の TURN(TCP) を使う（`OME_USE_TURN=1`）。
**切れたときに張り直すため `autossh` を入れておく**（無ければ ssh のまま動くが
再接続しない）。

起動したら **本機のブラウザで `http://localhost:7779/`** を開く。

### なぜ localhost でないといけないか

Gamepad API とマイク取得は secure context を要求する。`http://localhost` は
secure context として扱われるので、ブラウザと UI サーバが同一機なら証明書が要らない。
**別の機械から開くと secure context を失い、ゲームパッドが動かなくなる。**

## 中身

| 場所 | 内容 |
|---|---|
| `app.py` | Flask + SocketIO + ROS 2 publisher。ブラウザの入力を DDS へ流す |
| `templates/`, `static/` | 操作画面 |
| `gst/operator_mic_send.py` | マイク -> PC-B（RTP/UDP）。PTT で valve を開閉 |
| `ome/` | OME の起動と設定 |

### ブラウザからの操作

| 操作 | 送るもの | ROS topic |
|---|---|---|
| 左スティック | `twist` 10 Hz | `<robot>/rover/twist` |
| A ボタン（押下中） | `ptt` on/off | `<robot>/operator/ptt` |
| B / X / Y | `button_press` | `<robot>/operator/button` |
| 右スティック | **使わない** | — |

右スティックが空いているのは、頭部の指令元が PC-D の VLM だから（設計 §4.2）。
自律を上書きする手動介入に割り当てるかは未決。

**PTT は押下と離しの両方を送る。** 発話がテキストで残らないぶん、この区間が
「いつ喋ったか」のラベルになる（設計 §5.5）。ゲームパッドが無くても、画面の
録音ボタンをマウスで押している間だけ送話できる。

### 速度制限はここには無い

スティックの値はそのまま流す。スケーリングと停止は PC-B の `rover_driver.py` に
置いてある。指令が無線を越える構成では、リンクが切れた瞬間にブラウザ側の
「止まれ」は届かないため。

## センサが無い状態での確認

`USE_FAKE_SOURCES=1` なら `operator_mic_send.py` が `audiotestsrc` の正弦波を
送る。PTT を押している間だけ PC-B 側で音が出るかを見る。

OME が無い状態でも UI は開く（映像だけ出ない）。ゲームパッドの値が画面に出て、
`ros2 topic echo /<robot>/rover/twist` に流れていれば経路は通っている。

## ★ 現地で確認すること

- 操作者マイクの ALSA デバイス名（`config.env` の `OPERATOR_MIC_DEVICE`）
- OME の RTMP 入力が既定で有効か（`ome/README.md`）
- `static/vendor/` に socket.io と ovenplayer を置いたか。**CDN のままだと
  現地にインターネットが無い時に操作画面ごと落ちる**
