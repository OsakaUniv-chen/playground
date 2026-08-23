#!/bin/bash
# 操作 UI 服务。**只发网页，媒体流一点都不经过它**（架构 §4.1）——
# 页面里的 OvenPlayer 直接向 rog-server 的 OME 建 WebRTC 连接。
#
#   ./run_tele.sh            起（前台，Ctrl-C 停）
#   ./run_tele.sh -- <args>  其余参数原样传给 app.py
#
# 起来之后从 **tele-pc** 的浏览器打开 `http://rog-server.local:7779/`。
#
# **★ 手柄在 tele-pc 上用不了。** Gamepad API 只在 secure context 里可用，
# 而 `http://rog-server.local:7779` 不是（`http://localhost` 才是）。旧实现
# 靠「浏览器和 UI 同机」满足这个条件，架构 §4 把两者拆到两台机器之后这条没了。
# 详见 stream-server 隔壁 tele-server/README.md 的「两个还没解决的问题」。
#
# **★ 页面没有认证，而它能开动机体。** 所以 `UI_ROS_ENABLE` 默认是 0
# （只发页面，指令不发出去）。要真的开车再打开它，并且知道同一个 AP 上的人
# 都能打开这个地址。
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
