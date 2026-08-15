# PC-D 高性能サーバ

推論用の GPU を積む前提の 1 台。PC-B は入出力と収録に徹し、重い処理はここに置く。

## 起動

```bash
source env.sh

python3 gst/recv_ome.py                    # OME から 4 入力を受ける
python3 gst/recv_ome.py --only stream soundmap --snapshot-dir /tmp/shots
python3 infer/head_controller.py --demo    # 頭部指令の経路確認（正弦波）
```

まとめて起動するなら `./run.sh`（`./run.sh stop` で停止、`status` で確認）。ログは `log/` に出る。

## 中身

| 場所 | 内容 |
|---|---|
| `gst/recv_ome.py` | **OME から 4 入力（映像・音響マップ・機体マイク・操作者マイク）を受ける。** 推論側はここから最新の 1 枚を取る |
| `infer/head_controller.py` | 「誰に向くか」を決めて PC-B へ publish する。**骨格のみ** |

### PC-C への繋ぎ方（理研から）

**PC-D は理研にあり、PC-A/B/C の LAN に居ない。** PC-C 側にも理研側にも着信
ポートが無いので、直接は繋がらない。唯一通るのは「PC-C から SSH で出て行く」
方向なので、**PC-C 側でトンネルを張り**、PC-D はそれを localhost として見る。

```bash
# PC-C 側（pc-c-operator/config.env の PCD_SSH_HOST を設定して ./run.sh すると自動）
ssh -N -R 3333:localhost:3333 -R 3478:localhost:3478 3090PC
```

PC-D 側は `config.env` で `OME_HOST=127.0.0.1` `OME_USE_TURN=1` にしてあるので、
`recv_ome.py` はそのまま繋がる。**`OME_USE_TURN=1` は必須**で、SSH は TCP しか
運べず ICE の UDP（10000-10004）が通らないため、OME 内蔵の TURN(TCP) に載せる。
`ome_receiver.py` は TURN のアドレスも signalling で繋いだ先に書き換えるので、
OME 側が「トンネルの向こうからどう見えるか」を知らなくてよい。

同じ LAN に移した場合は `config.env` の 2 行を消せば直接・UDP に戻る。

**4 本で約 10 Mbps を SSH 1 本に通す（TCP の中に TCP）。** ロスがあると
急に悪くなるので、まず `--only stream` の 1 本で測ること。

### OME からの 4 入力

```python
from recv_ome import OmeInputs

inp = OmeInputs()          # 接続先は PC_C_IP / OME_WS_PORT / OME_APP
inp.start()
f = inp.latest_video("stream")      # "stream" "soundmap"
if f is not None and f.age_sec() < 0.5:
    rgb = f.array()                 # (h, w, 3) uint8
a = inp.latest_audio("mic")         # "mic" "operator"
```

**持つのは「いま最新の 1 枚」だけで履歴は溜めない。** 推論が間に合わなければ
古い枚は黙って捨てられる。`age_sec()` で鮮度を見てから使う。

**ここでは記録しない**（設計 §5.1）。記録は PC-B の bag だけ。

4 本とも `common/ome_receiver.py` で受けている。送出側がまだ起動していなければ
5 s ごとに繋ぎ直すので、**PC-B と PC-D の起動順は気にしなくてよい。**
`gstreamer1.0-nice` が入っていないと動かない（`gst-inspect-1.0 nicesrc` で確認）。

### 頭部制御

`head_controller.py` が `<robot>/head/command`（`BoxieMotors`、`[pitch, yaw, roll]` を度で）
に publish し、PC-B の `head_driver.py` が可動域制限と smoothing を掛けてモータを回す。
**可動域は PC-B 側で掛かるので、こちらは制限を知らずに指令を出してよい。**
実際に適用された値は `<robot>/head/applied` に出る。

`--demo` は正弦波を出すだけ。VLM を繋ぐときは `decide()` を実装して
`publish_goal()` を呼ぶ。

## まだ無いもの

| 対象 | 状態 |
|---|---|
| VLM 推論 | `decide()` が `NotImplementedError`。入力は `OmeInputs` から取れる状態になっている |
| 音響マップ生成 | PC-B で生成して OME へ送る形になったので、こちらは受けるだけ |
| 理研からの疎通 | **未確認。** 確認は 1 台の loopback のみ。下の「PC-C への繋ぎ方」を現地で通す |

## 記録の観点

ここが出す指令は「モデルの判断」で、操作者がゲームパッドで出す台車指令とは
別 topic になる。bag の中で自然に区別できるので、**両方を同じ topic に混ぜないこと**
（設計 §5.5）。記録自体は PC-B 側で行うので、こちらに収録の仕組みは要らない。
