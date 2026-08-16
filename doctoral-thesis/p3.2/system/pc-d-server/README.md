# PC-D 高性能サーバ

推論用の GPU を積む 1 台。PC-B は入出力と収録に徹し、重い処理はここに置く。
設計は [../teleop-architecture.md](../teleop-architecture.md)。

**この機械に ROS は要らない。** 受信は gi + numpy だけで動き、頭部指令は TCP で
PC-C へ投げて向こうで ROS に載せ替える（設計 §4.2）。理研にあり、PC-C とは
Tailscale で繋がる（§0.1）。

## 起動

```bash
source env.sh
python3 recv_ome.py                       # OME から 4 入力を受けて統計を出す
python3 recv_ome.py --only stream mic      # 一部だけ
```

`head_controller.py` は `decide()` が未実装なので単体では動かない
（[../teleop-architecture.md](../teleop-architecture.md) §0.3）。

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
**ここでは記録しない**（記録は PC-B の bag だけ）。送出側が未起動でも 5 s ごとに
繋ぎ直すので、**PC-B との起動順は気にしなくてよい。**

頭部指令は `HeadController.publish_goal(pitch, yaw)` で出す。**値が変わったときだけ
送る**（PC-B 側の smoothing が間を埋める）。可動域は PC-B が掛けるので、
こちらは制限を知らずに指令を出してよい。中継が落ちていても 5 秒ごとに繋ぎ直し、
送れなかったぶんは捨てる（古い指令で首が動かないように）。

## この機械の事情（触らない）

**共有機なので環境は変えない。** Ubuntu 20.04 / Python 3.8 / GStreamer **1.16.3**
（PC-C の 1.20 より古く、`webrtcbin` の `latency` は 1.18 から ── `ome_receiver.py`
はプロパティの有無を見てから設定する）。ROS は galactic / foxy / noetic のみだが、
DDS を跨がせない構成なので問題にならない。

```bash
sudo apt install gir1.2-gst-plugins-bad-1.0 gstreamer1.0-nice   # 導入済み
```
