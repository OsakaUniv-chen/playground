#!/bin/bash
# PC-C の常駐プロセスを起動・停止する。OME は systemd で常駐しているので
# ここでは起動しない（状態を見るのは ome/run_ome.sh）。
#   ./run.sh            起動（ログは log/ に出る）
#   ./run.sh stop       停止
#   ./run.sh status     生きているか
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/env.sh"

LOG="$HERE/log"
PIDFILE="$LOG/pids"
mkdir -p "$LOG"

stop_all() {
    [ -f "$PIDFILE" ] || { echo "起動していない"; return 0; }
    while read -r name pid; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            echo "  停止 $name (pid=$pid)"
        fi
    done < "$PIDFILE"
    rm -f "$PIDFILE"
}

status_all() {
    [ -f "$PIDFILE" ] || { echo "起動していない"; return 0; }
    while read -r name pid; do
        if kill -0 "$pid" 2>/dev/null; then
            echo "  生きている $name (pid=$pid)"
        else
            echo "  死んでいる $name  -> $LOG/$name.log を見る"
        fi
    done < "$PIDFILE"
}

case "${1:-start}" in
    stop)   stop_all; exit 0 ;;
    status) status_all; exit 0 ;;
esac

stop_all
: > "$PIDFILE"

# setsid で切り離す。親の shell を閉じても落ちないようにする。
start() {  # start <name> <command...>
    local name=$1; shift
    setsid "$@" > "$LOG/$name.log" 2>&1 < /dev/null &
    echo "$name $!" >> "$PIDFILE"
    echo "  $name  pid=$!  -> $LOG/$name.log"
}

start ui    python3 "$HERE/app.py"
start mic   python3 "$HERE/gst/operator_mic_send.py"
start relay python3 "$HERE/head_relay.py"      # PC-D からの頭部指令 -> ROS

# PC-D（理研）への SSH トンネル。PC-D 側にも PC-C 側にも着信ポートが無いので、
# **PC-C から出て行く 1 本**で OME の signalling と TURN を PC-D の localhost に
# 生やす。-R がその向き。UDP は運べないので PC-D は TURN(TCP) を使う
# （pc-d-server/config.env の OME_USE_TURN=1）。頭部指令の中継も同じ
# トンネルに相乗りする（HEAD_RELAY_PORT）。
# PCD_SSH_HOST が空なら張らない（PC-D が同じ LAN に居る場合）。
if [ -n "${PCD_SSH_HOST:-}" ]; then
    SSH_BIN=$(command -v autossh || command -v ssh)
    [ "$(basename "$SSH_BIN")" = "ssh" ] && \
        echo "  ※ autossh が無いので切れても張り直さない（sudo apt install autossh）"
    start tunnel "$SSH_BIN" -N \
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
        -R "${OME_WS_PORT}:localhost:${OME_WS_PORT}" \
        -R "${OME_TURN_PORT}:localhost:${OME_TURN_PORT}" \
        -R "${HEAD_RELAY_PORT}:localhost:${HEAD_RELAY_PORT}" \
        "$PCD_SSH_HOST"
fi

echo
echo "ブラウザで http://localhost:${UI_PORT}/ を開く"
echo "  ※ 別の機械から開くと secure context を失い Gamepad API が動かない"
echo "停止は ./run.sh stop、確認は ./run.sh status"
