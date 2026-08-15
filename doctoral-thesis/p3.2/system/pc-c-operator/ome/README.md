# OME（PC-C で稼働）

映像を見るのは操作者なので、配信サーバは操作者端末に置く。

## 経路

| 向き | プロトコル | ポート |
|---|---|---|
| PC-B -> OME | RTMP | 1935 |
| OME -> ブラウザ | WebRTC (signalling) | 3333（平文） |

## TLS が要らない理由

ブラウザと UI サーバが同一機なので `http://localhost:7779` が secure context として
扱われ、Gamepad API とマイク取得の要件を満たす。ページが http なので signalling も
`ws://`（3333）で繋がり、OME 側の証明書設定が丸ごと不要になる。
WebRTC のメディア自体は DTLS/SRTP なので、平文で流れるわけではない。

**操作画面を別の機械から開くとこれは成立しない。** secure context を失うので
HTTPS と wss（3334）が必要になり、証明書が要る。

OME はこの機械にネイティブで入っており（`/usr/bin/OvenMediaEngine`）、
systemd で常駐している。docker は使わない。

## 確認

```bash
./run_ome.sh                      # 状態と設定の場所を表示
# 別端末から PC-B の送出を確認
gst-launch-1.0 -v videotestsrc ! x264enc ! flvmux ! rtmpsink \
  location="rtmp://localhost:1935/app/blr1stream live=true"
# ブラウザで http://localhost:7779/ を開き、映像が出るか
```

## ネットワークが変わったら OME を再起動する ★

**OME は起動時に一度だけ NIC を列挙し、その住所を ICE candidate として
配り続ける。** 後から DHCP でアドレスが変わっても直らない。こうなると
SDP の交換は成功し OME 側にセッションも立つのに、**メディアだけが永久に
来ない**（実際にこれで詰まった）。

```bash
ip -4 -o a                                  # 実際のアドレス
python3 ../../common/ome_receiver.py <stream> -v   # OME が配っているアドレス
sudo systemctl restart ovenmediaengine
```

`ome_receiver.py`（PC-B・PC-D）は候補のアドレスを signalling で繋いだ先に
書き換えて自衛するので受信側は困らないが、**ブラウザ（OvenPlayer）は
書き換えない。** 操作画面だけ映らないときはまずこれを疑う。

現地では **PC-C のネットワークが確定してから OME を再起動する。**

## ストリーム

機体から 3 本入り、操作者マイクが 1 本入る。

| stream key | 向き | 中身 |
|---|---|---|
| `<robot>stream` | PC-B → | Xacti 映像 |
| `<robot>mic` | PC-B → | 機体マイク |
| `<robot>soundmap` | PC-B → | 1-bit 音響マップ（PC-B で生成） |
| `operatormic` | PC-C → | 操作者マイク（PC-B と PC-D が受ける） |

RTMP 入力が既定で有効なこと、`app` が定義済みなこと、音声のみのストリームも
受け付けることは確認済み。

**存在しないストリームに `request_offer` を送っても OME は黙っている**
（エラーも返さない）。受信側はタイムアウトで繋ぎ直す作りにしてある。
