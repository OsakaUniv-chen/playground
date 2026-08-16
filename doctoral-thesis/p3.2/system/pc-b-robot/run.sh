#!/bin/bash
# PC-B の起動はこれ 1 本。env.sh を読んで launch を叩くだけ。
#   ./run.sh                  収録込みで全プロセスを起動（既定）
#   ./run.sh record:=false    収録せずに動かす（経路の確認だけしたいとき）
#
# **前面で動く。止めるのは Ctrl-C。** PC-C の run.sh と違って
# start/stop/status を持たないのは、PC-B では ros2 launch 自身が
# プロセスの監視・respawn・後始末をやるため。二重に被せると、
# どちらが子を持っているのか分からなくなる。
#
# 収録先は RECORD_DIR/<session>（既定 ~/p32/rosbags/YYYYMMDD_HHMMSS）。
# 起動時に record.sh が実際のパスと df -h を出すので、そこで確かめる。
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/env.sh"
exec ros2 launch "$HERE/pcb.launch.py" "$@"
