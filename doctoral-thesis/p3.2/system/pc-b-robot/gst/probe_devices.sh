#!/bin/bash
# 現地で最初に走らせる。デバイス名と対応フォーマットを一覧する。
# ここで得た値を ../config.env の ★ 項目に書く。
source "$(dirname "$0")/../env.sh"

echo "================ V4L2 カメラ ================"
v4l2-ctl --list-devices 2>/dev/null || echo "v4l-utils が無い: sudo apt install v4l-utils"
for d in /dev/video*; do
    [ -e "$d" ] || continue
    echo "--- $d ---"
    v4l2-ctl -d "$d" --list-formats-ext 2>/dev/null | head -25
done
echo
echo "  安定した名前は /dev/v4l/by-path/ か by-id/ を使うこと（video0 は起動順で変わる）"
ls -l /dev/v4l/by-path/ 2>/dev/null

echo
echo "================ ALSA 録音デバイス ================"
arecord -l
echo
echo "--- PCM 名（hw:NAME 形式）---"
arecord -L | grep -E "^(hw|plughw):" | head -20
echo
echo "================ ALSA 再生デバイス ================"
aplay -l

echo
echo "================ 16ch アレイの確認 ================"
echo "  arecord -D \$MIC_ARRAY_DEVICE -f S16_LE -r \$MIC_ARRAY_RATE -c \$MIC_ARRAY_CHANNELS -d 3 /tmp/test16.wav"
echo "  で 3 秒録って、全 ch に信号が乗っているか確認する"

echo
echo "================ gst プラグイン ================"
for p in v4l2src alsasrc x264enc voaacenc flvmux rtmpsink appsink opusdec rtpopusdepay \
         webrtcbin nicesrc; do
    if gst-inspect-1.0 "$p" >/dev/null 2>&1; then echo "  OK   $p"; else echo "  無い $p"; fi
done
echo "  ※ nicesrc が無いと OME からの受信が黙って失敗する: sudo apt install gstreamer1.0-nice"
