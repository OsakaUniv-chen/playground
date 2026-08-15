#!/bin/bash
# 収録後の確認。メッセージ数が期待値と合っているか、offset が飛んでいないか。
source "$(dirname "$0")/../env.sh"
BAG="${1:?usage: check_bag.sh <bag_dir>}"

echo "=== ros2 bag info ==="
ros2 bag info "$BAG"

echo
echo "=== 期待値（1 分あたり） ==="
echo "  camera/video      : ${CAM_FPS} fps        -> $(( CAM_FPS * 60 )) msg"
echo "  mic_array/audio   : $(( 1000 / MIC_ARRAY_MS_PER_MSG )) Hz -> $(( 1000 / MIC_ARRAY_MS_PER_MSG * 60 )) msg"
echo "  soundmap/raw      : ${SOUNDMAP_HZ} Hz         -> $(( SOUNDMAP_HZ * 60 )) msg"
echo "  record/clock_offset: 1 Hz          -> 60 msg"
echo
echo "  大きく足りない topic があれば、その経路で落ちている"
