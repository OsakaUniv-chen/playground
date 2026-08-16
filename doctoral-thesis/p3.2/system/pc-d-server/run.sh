#!/bin/bash
# PC-D の常駐プロセスを起動・停止する。**この機械は ROS を使わない。**
#   ./run.sh            起動（ログは log/ に出る）
#   ./run.sh stop       停止
#   ./run.sh status     生きているか
#
# 動かすのは asr.py 1 本（OME からの受信も文字起こしもこの中）。
# 走らせる Python は $ASR_PYTHON ── PyGObject と faster-whisper の両方が
# 入った 3.10。作り方は README の「環境を作る」。
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/env.sh"

LOG="$HERE/log"
PIDFILE="$LOG/pids"
mkdir -p "$LOG"

stop_all() {
    # **死んだことを確かめてから「停止」と言う。** SIGTERM を送っただけで
    # 報告すると、落ちきらずにポートを握ったままの抜け殻が残ったときに嘘になる。
    [ -f "$PIDFILE" ] || { echo "起動していない"; return 0; }
    while read -r name pid; do
        kill -0 "$pid" 2>/dev/null || continue
        kill "$pid" 2>/dev/null || true
        for _ in $(seq 20); do                  # 最大 5 秒待つ
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.25
        done
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
            echo "  停止 $name (pid=$pid) ── SIGTERM に応じないので SIGKILL"
        else
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

[ -x "$ASR_PYTHON" ] || {
    echo "ASR_PYTHON が無い: $ASR_PYTHON"
    echo "README の「環境を作る」を先に済ませること。"
    exit 1
}

stop_all
: > "$PIDFILE"

# setsid で切り離す。親の shell を閉じても落ちないようにする。
# **`-u` を外さない。** 出力先がファイルなので、付けないと Python が
# stdout をブロックバッファリングし、異常終了したプロセスのログが
# まるごと 0 バイトで残る（PC-C で実際に踏んだ）。
start() {  # start <name> <command...>
    local name=$1; shift
    setsid "$@" > "$LOG/$name.log" 2>&1 < /dev/null &
    echo "$name $!" >> "$PIDFILE"
    echo "  $name  pid=$!  -> $LOG/$name.log"
}

# CUDA のライブラリの在り処は env.sh が組み立てる（無いとモデルの
# 読み込みだけ成功して推論で落ちる）。
start asr  env LD_LIBRARY_PATH="$ASR_LD_LIBRARY_PATH" "$ASR_PYTHON" -u "$HERE/asr.py"

echo
echo "書き起こしは $LOG/asr.log と $LOG/transcript.jsonl に出る"
echo "停止は ./run.sh stop、確認は ./run.sh status"
