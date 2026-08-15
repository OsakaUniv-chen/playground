#!/bin/bash
# PC-D の常駐プロセスを起動・停止する。
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

start ome_recv  python3 "$HERE/gst/recv_ome.py"
start head_ctl  python3 "$HERE/infer/head_controller.py"

echo
echo "停止は ./run.sh stop、確認は ./run.sh status"
