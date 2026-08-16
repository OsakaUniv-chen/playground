#!/bin/bash
# PC-C の常駐プロセスを起動・停止する。OME は systemd で常駐しているので
# ここでは起動しない（`systemctl status ovenmediaengine` で見る）。
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
start mic   python3 "$HERE/operator_mic_send.py"
start relay python3 "$HERE/head_relay.py"      # PC-D からの頭部指令 -> ROS

echo
echo "ブラウザで http://localhost:${UI_PORT}/ を開く"
echo "  ※ 別の機械から開くと secure context を失い Gamepad API が動かない"
echo "停止は ./run.sh stop、確認は ./run.sh status"
