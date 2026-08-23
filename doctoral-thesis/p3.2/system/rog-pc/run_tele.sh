#!/bin/bash
# 操作 UI 服务。**只发网页，媒体流一点都不经过它**（架构 §4.1）——
# 页面里的 OvenPlayer 直接向 rog-server 的 OME 建 WebRTC 连接。
#
#   ./run_tele.sh            起（前台，Ctrl-C 停）
#   ./run_tele.sh -- <args>  其余参数原样传给 app.py
#
# 起来之后从 **tele-pc** 的浏览器打开 `http://rog-server.local:7779/`。
#
# **手柄插在 tele-pc 上，浏览器读得到** —— 实测 Chrome 127 在非 localhost 的
# http 源下 Gamepad API 照常可用（`isSecureContext` 是 false 也不影响）。
# 先前这里写过相反的话，那是错的，详见 tele-server/README.md。
#
# **★ 页面没有认证，而它能开动机体。** 所以 `UI_ROS_ENABLE` 默认是 0
# （只发页面，指令不发出去）—— 主要是因为**操作指令通路还没设计**，
# 机体那一侧还没接。要真的开车再打开它。
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
APP="${HERE}/tele-server"
source "${APP}/config.env"

: "${UI_PORT:?未设置 —— 没读到 config.env}"
mkdir -p "${LOG_DIR}"

[ -x "${TELE_PYTHON}" ] || {
    echo "[error] TELE_PYTHON 不存在: ${TELE_PYTHON}" >&2
    echo "        先按 tele-server/README.md 的「环境」建好 venv-tele，再回来。" >&2
    exit 1
}

# 要发 ROS 就必须先 source —— **rclpy 不在系统 python 的搜索路径里**，
# 不 source 就是启动时 ImportError。和 run_stream.sh 一样放在这里做，
# 现场少一步就少一个忘掉的机会。
if [ "${UI_ROS_ENABLE}" = "1" ]; then
    if [ -f "${ROS_SETUP}" ]; then
        # setup.bash 内部会引用没定义的变量，set -u 下会当场退出，临时关掉。
        set +u
        source "${ROS_SETUP}"
        set -u
    else
        echo "[error] 找不到 ${ROS_SETUP}（config.env 的 ROS_SETUP）" >&2
        echo "        只想看页面的话把 UI_ROS_ENABLE 设成 0。" >&2
        exit 1
    fi
fi

[ "${1:-}" = "--" ] && shift

# **不要去掉 -u。** 输出重定向到文件时，不加的话 Python 会块缓冲 stdout，
# 异常退出的进程会留下一个 0 字节的日志（旧实现的操作者端上踩过）。
exec "${TELE_PYTHON}" -u "${APP}/app.py" "$@"
