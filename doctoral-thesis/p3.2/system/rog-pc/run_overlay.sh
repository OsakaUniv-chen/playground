#!/bin/bash
# 鱼眼 ＋ 声音图的叠加图，推回 OME（stream key rgb_sm）。
#
#   ./run_overlay.sh            起（前台，Ctrl-C 停）
#   ./run_overlay.sh -- <args>  其余参数原样传给 soundmap_overlay.py
#
# **和 run_stream.sh 是两个进程。** 分开是因为职责不同：检测那条要保证 5 Hz
# 的判定不被拖慢，这条只是监视流，挂了不该连累它。
# 设计见 ../system-architecture.md §3。
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
APP="${HERE}/stream-server"
source "${APP}/config.env"

: "${OME_HOST:?未设置 —— 没读到 config.env}"
mkdir -p "${LOG_DIR}"

[ -x "${ROG_PYTHON}" ] || {
    echo "[error] ROG_PYTHON 不存在: ${ROG_PYTHON}" >&2
    echo "        先按 stream-server/README.md 的「环境」建好，再回来。" >&2
    exit 1
}

[ "${1:-}" = "--" ] && shift

# -u 的理由和 run_stream.sh 一样：重定向到文件时不要块缓冲。
exec "${ROG_PYTHON}" -u "${APP}/soundmap_overlay.py" "$@"
