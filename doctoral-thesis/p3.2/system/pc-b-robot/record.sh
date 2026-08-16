#!/bin/bash
# 全データを 1 つの bag（mcap）に落とす。
# gst 由来のデータも bridge が ROS メッセージにして流しているので、
# ここで録るだけで映像・音響・指令・関節角が同じファイルに入る。
source "$(dirname "$0")/env.sh"

R="/${ROBOT_NAME:?未設定。env.sh を読まずに起動している（common/config.env が設定する）}"
SESSION="${1:-$(date +%Y%m%d_%H%M%S)}"
OUT="${RECORD_DIR}/${SESSION}"
mkdir -p "${RECORD_DIR}"

TOPICS=(
    # --- gst 由来（bridge がタイムスタンプを UNIX 時間に換算して publish） ---
    "$R/camera/video"
    "$R/onboard_mic/audio"
    "$R/soundmap/raw"
    "$R/mic_array/audio"
    "$R/operator_mic/audio"
    # --- 指令（到着時刻 = mcap の log_time） ---
    "$R/rover/twist"
    "$R/head/command"
    "$R/arm/command"
    # --- モータの接続状態 ---
    "$R/keigan_motor/status"
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
