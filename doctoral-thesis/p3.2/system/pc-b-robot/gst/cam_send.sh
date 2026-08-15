#!/bin/bash
# Xacti カメラ + 機体マイク -> OME(RTMP)。記録は含まない単体確認用。
# 実運用は bridge/cam_bridge.py（tee で記録側も分岐する）。
source "$(dirname "$0")/../env.sh"

RTMP="rtmp://${PC_C_IP}:${OME_RTMP_PORT}/${OME_APP}/${STREAM_KEY_MAIN} live=true"
echo "-> $RTMP"

# 有効範囲だけ切り出す（左右／上下を均等に落とす）
CROP_LR=$(( (CAM_SRC_WIDTH - CAM_WIDTH) / 2 ))
CROP_TB=$(( (CAM_SRC_HEIGHT - CAM_HEIGHT) / 2 ))
if [ "$CROP_LR" -gt 0 ] || [ "$CROP_TB" -gt 0 ]; then
    CROP="videocrop left=${CROP_LR} right=$(( CAM_SRC_WIDTH - CAM_WIDTH - CROP_LR )) \
          top=${CROP_TB} bottom=$(( CAM_SRC_HEIGHT - CAM_HEIGHT - CROP_TB )) !"
else
    CROP=""
fi
# profile=baseline を付けないと、上流が I420 でないとき（USE_FAKE_SOURCES の
# videotestsrc など）x264enc が High 4:4:4 を選ぶ。ブラウザは復号できない。
ENC="x264enc tune=zerolatency speed-preset=ultrafast bitrate=${CAM_BITRATE} key-int-max=${CAM_FPS} ! video/x-h264,profile=baseline"

if [ "$USE_FAKE_SOURCES" = "1" ]; then
    VSRC="videotestsrc is-live=true pattern=ball \
        ! video/x-raw,width=${CAM_SRC_WIDTH},height=${CAM_SRC_HEIGHT},framerate=${CAM_FPS}/1 \
        ! timeoverlay ! ${CROP} videoconvert ! ${ENC}"
    ASRC="audiotestsrc is-live=true wave=pink-noise"
else
    VSRC="v4l2src device=${CAM_DEVICE} do-timestamp=true io-mode=2 \
        ! image/jpeg,width=${CAM_SRC_WIDTH},height=${CAM_SRC_HEIGHT},framerate=${CAM_FPS}/1 \
        ! jpegdec ! ${CROP} videoconvert ! ${ENC}"
    ASRC="alsasrc device=${ONBOARD_MIC_DEVICE} buffer-time=200000"
fi

# config-interval=-1: SPS/PPS を IDR ごとに入れる。分割記録に必須。
gst-launch-1.0 -v \
    $VSRC \
    ! h264parse config-interval=-1 \
    ! flvmux name=mux streamable=true \
    ! rtmpsink location="$RTMP" \
    $ASRC \
    ! audio/x-raw,format=S16LE,rate=${ONBOARD_MIC_RATE},channels=${ONBOARD_MIC_CHANNELS} \
    ! audioconvert ! audioresample \
    ! queue max-size-time=500000000 leaky=downstream \
    ! voaacenc bitrate=64000 ! aacparse ! mux.
