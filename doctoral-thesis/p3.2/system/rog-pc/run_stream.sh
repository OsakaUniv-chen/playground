#!/bin/bash
# stream-server 的人物检测节点。
#
#   ./run_stream.sh            起（前台，Ctrl-C 停）
#   ./run_stream.sh test       不发 ROS，只打印（流水账照写，见 person_detect.py）
#   ./run_stream.sh -- <args>  发 ROS，其余参数原样传给 person_detect.py
#
# **OME 本身不在这里起** —— 它是 systemd 管的（ovenmediaengine.service）。
# tele-server 的启动脚本以后放在同一层，叫 run_tele.sh。
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

MODE="${1:-run}"
[ $# -gt 0 ] && shift

case "${MODE}" in
    test) EXTRA=(--print-only) ;;
    run)  EXTRA=() ;;
    --)   EXTRA=() ;;
    *)    echo "用法: $0 [run|test|-- <args>]" >&2; exit 1 ;;
esac

# 要发 ROS 的话必须先 source —— **rclpy 不在系统 python 的搜索路径里**，
# 不 source 就是启动时 ImportError。放在这里而不是让人手动做：现场少一步
# 就少一个忘掉的机会。
#
# **条件是「不是 test」，不是「等于 run」。** `-- <args>` 那条路同样会发 ROS
# （除非自己带了 --print-only），漏掉它就会在那条路上 ImportError。
if [ "${MODE}" != "test" ]; then
    if [ -f "${ROS_SETUP}" ]; then
        # setup.bash 内部会引用没定义的变量，set -u 下会当场退出，临时关掉。
        set +u
        source "${ROS_SETUP}"
        set -u
    elif [ "${MODE}" = "run" ]; then
        echo "[error] 找不到 ${ROS_SETUP}（config.env 的 ROS_SETUP）" >&2
        echo "        ROS 没装的话先用 ./run_stream.sh test 跑。" >&2
        exit 1
    fi
fi

# **不要去掉 -u。** 输出重定向到文件时，不加的话 Python 会块缓冲 stdout，
# 异常退出的进程会留下一个 0 字节的日志（旧实现的操作者端上踩过）。
exec "${ROG_PYTHON}" -u "${APP}/person_detect.py" "${EXTRA[@]}" "$@"
