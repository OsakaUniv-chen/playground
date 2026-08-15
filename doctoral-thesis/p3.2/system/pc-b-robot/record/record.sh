#!/bin/bash
# 全データを 1 つの bag（mcap）に落とす（設計 §5.3）。
# gst 由来のデータも bridge が ROS メッセージにして流しているので、
# ここで録るだけで映像・音響・指令・関節角が同じファイルに入る。
source "$(dirname "$0")/../env.sh"

R="/${ROBOT_NAME}"
SESSION="${1:-$(date +%Y%m%d_%H%M%S)}"
OUT="${RECORD_DIR}/${SESSION}"
mkdir -p "${RECORD_DIR}"

TOPICS=(
    # --- gst 由来（bridge がタイムスタンプを UNIX 時間に換算して publish） ---
    "$R/camera/video"
    "$R/onboard_mic/audio"   "$R/onboard_mic/info"
    "$R/soundmap/raw"
    "$R/mic_array/audio"     "$R/mic_array/info"
    "$R/operator_mic/audio"  "$R/operator_mic/info"
    # --- 指令（到着時刻 = mcap の log_time） ---
    "$R/rover/twist"
    "$R/rover/action_sent"
    "$R/head/command"
    "$R/head/applied"
    "$R/arm/command"
    "$R/operator/ptt"
    "$R/operator/button"
    # --- 実測（設計 §5.5: 指令値ではなく実関節角を残す） ---
    "$R/head/current"
    "$R/arm/current"
    "$R/status"
    # --- 時刻の対応 ---
    "$R/record/clock_offset"
)

echo "=== 収録開始 ==="
echo "  session : ${SESSION}"
echo "  out     : ${OUT}"
echo "  topics  : ${#TOPICS[@]}"
df -h "${RECORD_DIR}" | tail -1
echo "  ※ 開始したら手を叩く（映像・機体マイク・16ch の同期確認用）"

exec ros2 bag record \
    -s mcap \
    -o "${OUT}" \
    --max-bag-size $(( BAG_SPLIT_MB * 1024 * 1024 )) \
    "${TOPICS[@]}"
