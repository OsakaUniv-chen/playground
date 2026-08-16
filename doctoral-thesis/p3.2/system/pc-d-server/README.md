# PC-D 高性能サーバ

推論用の GPU を積む 1 台。PC-B は入出力と収録に徹し、重い処理はここに置く。
設計は [../teleop-architecture.md](../teleop-architecture.md)。

**この機械に ROS は要らない。** 受信は gi + numpy だけで動き、頭部指令は TCP で
PC-C へ投げて向こうで ROS に載せ替える（設計 §4.2）。理研にあり、PC-C とは
Tailscale で繋がる（§0.1）。

## 1. フォルダを置く

PC-D に置くのは **`common/` と `pc-d-server/` の 2 つ**。**必ず並べて置く。**
`env.sh` は `../common/config.env` を、`recv_ome.py` は `../common/ome_receiver.py`
を相対パスで引く（**受信の実体はこの `ome_receiver.py` のほう**で、
`pc-d-server/` だけ配ると何も受けられない）。

手元（この repo）の `system/` で:

```bash
rsync -a --exclude __pycache__ common pc-d-server <user>@<PC-D の tailscale アドレス>:~/p32/
```

```
~/p32/common/        config.env・ome_receiver.py（受信の実体）
~/p32/pc-d-server/   2 以降の作業はすべてこのディレクトリで行う
```

## 2. 依存（**導入済み。環境は変えない**）

**共有機なので環境は変えない。** Ubuntu 20.04 / Python 3.8 / GStreamer **1.16.3**
（PC-C の 1.20 より古く、`webrtcbin` の `latency` は 1.18 から ── `ome_receiver.py`
はプロパティの有無を見てから設定する）。ROS は galactic / foxy / noetic のみだが、
DDS を跨がせない構成なので問題にならない。

要るのはこれだけで、**すでに入っている**:

```bash
sudo apt install gir1.2-gst-plugins-bad-1.0 gstreamer1.0-nice   # 導入済み
```

入っているかの確認（こちらは何も変えないので、いつ実行してもよい）:

```bash
python3 -c "import gi, numpy; gi.require_version('Gst','1.0'); print('python OK')"
gst-inspect-1.0 nicesrc >/dev/null && echo "nice OK"
```

**`gstreamer1.0-nice` は OME からの受信に必須。** 無いと警告だけ出して
`create-answer` が黙って失敗する（理由は [../README.md](../README.md) §1.3）。

## 3. 設定を埋める

| ファイル | 埋めるもの |
|---|---|
| `config.env` | `OME_HOST` と `HEAD_RELAY_HOST` ── **どちらも PC-C の tailscale アドレス**（PC-C 側で `tailscale ip -4`） |

`../common/config.env` の `PC_C_IP` は **PC-C の LAN 側**アドレスなので、PC-D から
は届かない。`config.env` の `OME_HOST` がそれを上書きする形になっている。

**Tailscale はノード共有（Share device）では駄目。** 共有された側から相手の
tailnet へ発起できないため、signalling も頭部指令も通らない。正式メンバーとして
同じ tailnet に入れること（[../todo-list.md](../todo-list.md)）。

## 4. 起動

```bash
cd ~/p32/pc-d-server
source env.sh
python3 recv_ome.py                        # OME から 4 入力を受けて統計を出す
python3 recv_ome.py --only stream mic      # 一部だけ
```

**PC-B との起動順は気にしなくてよい。** 送出側が未起動でも 5 s ごとに繋ぎ直す。

`head_controller.py` は `decide()` が未実装なので単体では動かない
（[../teleop-architecture.md](../teleop-architecture.md) §0.3）。

1 本だけ確かめたいときは、`common/` の受信モジュールを直接叩ける:

```bash
python3 ../common/ome_receiver.py <stream_key> --host <PC-C の tailscale アドレス>   # -v で SDP
```

## 4 入力の使い方

```python
from recv_ome import OmeInputs

inp = OmeInputs()                   # 接続先は OME_HOST / OME_WS_PORT / OME_APP
inp.start()
f = inp.latest_video("stream")      # "stream" | "soundmap"
if f is not None and f.age_sec() < 0.5:
    rgb = f.array()                 # (h, w, 3) uint8
a = inp.latest_audio("mic")         # "mic" | "operator"
```

**持つのは「いま最新の 1 枚」だけで履歴は溜めない。** 推論が間に合わなければ
古い枚は黙って捨てられるので、`age_sec()` で鮮度を見てから使う。
**ここでは記録しない**（記録は PC-B の bag だけ）。

頭部指令は `HeadController.publish_goal(pitch, yaw)` で出す。**値が変わったときだけ
送る**（PC-B 側の smoothing が間を埋める）。可動域は PC-B が掛けるので、
こちらは制限を知らずに指令を出してよい。中継が落ちていても 5 秒ごとに繋ぎ直し、
送れなかったぶんは捨てる（古い指令で首が動かないように）。

音響マップは **64×64 のまま**届く。拡大が要るなら受け側の前処理でやる
（送出側で引き伸ばしても情報量は増えない ── 設計 §3.1）。
