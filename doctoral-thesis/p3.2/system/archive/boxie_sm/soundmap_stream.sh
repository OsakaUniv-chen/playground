#!/bin/bash
echo "16ch Mic Array Sound Map Gstreamer starts push stream"

# navcam / fisheye の押し出しと同じ出口（mpegtsmux alignment=7 ! udpsink）に
# 音響マップを 1 本足すもの。あちらが
#   v4l2src -> H.264 -> mpegtsmux -> udpsink
# なのに対し、こちらは
#   alsasrc(16ch) -> 1-bit 音響マップ(64x64, 10 Hz) -> H.264 -> mpegtsmux -> udpsink
# で、途中に生成が挟まるぶんだけ gst-launch 1 本では書けず、
# soundmap_stream.py が appsink と appsrc の間を繋ぐ。**設定はここに集める。**

# 16ch マイクアレイ（miniDSP UMA16v2）。カメラを by-id で指すのと同じ理由で、
# こちらもカード名で指す ── `hw:3` のような番号は USB の列挙順で変わり、
# 挿し直しただけで別のデバイスを開く（しかもエラーは出ない）。
#   名前の確認: cat /proc/asound/cards   /   arecord -l
mic_name=hw:CARD=UMA16v2,DEV=0

server_ip=stream-server.local
port_sending=9004

# 生成。周期（map_hz）と積分窓（window_ms）は別物で、窓は周期より長く取り
# 毎回ずらして使う。窓は S/N と空間分解能を決める一方、計算量もほぼ比例する
# （既定の 464 ms = 20480 sample/ch @44.1 kHz は word-wolf の実測条件）。
map_hz=10
window_ms=464
map_size=64                # 生成解像度。**このまま送る**（拡大は受け側で）
bitrate=500                # kbps。64x64 ならこれで十分すぎる

HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHON=${PYTHON:-python3}

# 引数はそのまま素通しする。よく使うのは
#   ./soundmap_stream.sh --fake            アレイを繋がずに経路だけ確かめる
#   ./soundmap_stream.sh --out-size 512    小さい画を嫌う受け手向けに拡大して送る
#   ./soundmap_stream.sh --host 192.168.1.100 --port 9005
#
# 受け側で確かめるとき（サーバの代わりに手元で受ける）:
#   gst-launch-1.0 -v udpsrc port=9004 ! tsdemux ! h264parse ! avdec_h264 ! \
#       videoconvert ! videoscale method=nearest-neighbour ! \
#       video/x-raw,width=512,height=512 ! autovideosink
exec "$PYTHON" "$HERE/soundmap_stream.py" \
    --device "$mic_name" \
    --host "$server_ip" \
    --port "$port_sending" \
    --hz "$map_hz" \
    --window-ms "$window_ms" \
    --size "$map_size" \
    --bitrate "$bitrate" \
    "$@"
