#!/bin/bash
# vlm-server：收鱼眼和声音图，拉转写，喂 VLM。
#
#   ./run.sh                起。建一个 tmux session 并把你接进去（和别的机器一样）
#   ./run.sh check          查依赖（gst element / typelib / 到 stream-server 通不通）
#   ./run.sh recv           **只验收流** —— 先用这个确认两路视频都到了
#   ./run.sh transcript     只验转写那条链（从 stream-server 拉一次打出来）
#   ./run.sh fg             不用 tmux，主循环直接在前台跑（调试用）
#   ./run.sh fg -- <args>   其余参数原样传给 vlm.py
#
# **改了 config.env 要重起整个 session 才生效**：
#     tmux kill-session -t <TMUX_SESSION> && ./run.sh
# config.env 是建 session 时 source 一次的，**光在 vlm 窗口按 Ctrl-C 没用** ——
# 重起的那个进程继承的还是 session 当初的环境。代价很小：模型热加载 1.2 s
# （第一次 138 s 是在下载 1.5 GB 权重）。
#
# tmux 的窗口:
#   0 Status    状态面板（两路视频、缓冲、转写正不正常）。**Ctrl-C = 全停**
#   1 onboard   机体麦克风识别出来的文字（拉回来的，转写在 stream-server 上）
#   2 operator  操作者麦克风识别出来的文字（同上）
#   3 VLM       判断内容。**decide() 还是空的**，窗口先占着
#   4 vlm       进程本体的输出（报错都在这），外面套着重起循环
#
# **只有一个进程在干活**（vlm.py 收流 ＋ 拉转写 ＋ 判断）。上面 1/2/3 三个窗口
# 是 `tail -f` 它写出来的文件 —— 不是三个进程。这么分是因为现场要看的三件事
# 互不相干：现场说了什么、操作者说了什么、系统据此决定了什么。
#
# **转写不在这台机器上做**（架构 §3、§5.2）—— 24 GB 显存要整块留给 VLM。
# 窗口 1/2 的内容是 vlm.py 每轮从 stream-server 拉回来、按音源镜像到本地的。
#
# 设计见 ../system-architecture.md §5。
set -u

SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
HERE="$(dirname "$SELF")"
source "${HERE}/config.env"

: "${OME_HOST:?未设置 —— 没读到 config.env}"

die()  { echo "[error] $*" >&2; exit 1; }
info() { echo "[info] $*"; }

need_python() {
    [ -x "${VLM_PYTHON}" ] || die "VLM_PYTHON 不存在: ${VLM_PYTHON}
        先按 README 的「环境」建好，再回来。"
}

# =====================================================================
# 内部子命令。tmux 窗口里跑的，不用手敲。
# =====================================================================

# __vlm —— 窗口 4。**重起循环** —— 进程退了就重来。
#
# 网络断了、OME 重启了、显存被别的进程吃光了，都会让它退出；这里重起一次
# 就能接上（收流侧自己也有重连，退到这一层的是更硬的故障）。
# **拉不到转写不算故障** —— transcript.py 拉不到会返回 ok=False 而不是抛异常，
# 判断可以只凭画面和声音图继续。
#
# **「跑了一阵才退」和「起来就死」要分开对待。** 前者重起就好；后者是配置或
# 环境坏了（模型下不下来、CUDA 不可用、config.env 写错），重起多少次都一样，
# 只会把日志刷满、把真正的错误顶出屏幕。所以连着几次都活不过 MIN_UP_SEC 就
# 拉长退避，并且在窗口里留一行明确的话。
__vlm() {
    local log="${LOG_DIR}/vlm.log" n=0 fast=0 wait t0 up
    local MIN_UP_SEC=10 FAST_LIMIT=3 SLOW_SEC=60
    while true; do
        echo "[run] ===== $(date '+%F %T') =====" >> "$log"
        t0=$(date +%s)
        "${VLM_PYTHON}" -u "${HERE}/vlm.py" 2>&1 | tee -a "$log"
        up=$(( $(date +%s) - t0 ))
        n=$((n+1))
        rm -f "${LOG_DIR}/status.json"      # 别让面板显示死掉那一刻的旧状态

        if [ "$up" -lt "$MIN_UP_SEC" ]; then fast=$((fast+1)); else fast=0; fi
        wait="${RESTART_SEC}"
        if [ "$fast" -ge "$FAST_LIMIT" ]; then
            wait="${SLOW_SEC}"
            echo "[error] 连着 ${fast} 次都没活过 ${MIN_UP_SEC}s —— 这不是网络问题，" \
                 "是环境或配置。先在别处跑 ./run.sh check，看上面的报错。" | tee -a "$log"
        fi
        echo "[restart] vlm 第 ${n} 次重起（跑了 ${up}s，$(date '+%F %T')），${wait}s 后" \
            | tee -a "$log"
        sleep "${wait}"
    done
}

# __tail <文件> <抬头> —— 窗口 1/2/3。文件还不存在也要能等着。
__tail() {
    local f="$1"; shift
    printf '\033[1m%s\033[0m\n' "$*"
    printf '\033[2m%s\033[0m\n\n' "$f"
    touch "$f"
    exec tail -n 200 -f "$f"
}

case "${1:-}" in
    __vlm)    __vlm; exit $? ;;
    __status) need_python; exec "${VLM_PYTHON}" -u "${HERE}/status.py" ;;
    __tail)   shift; __tail "$@"; exit $? ;;
esac

# =====================================================================
# 外面敲的
# =====================================================================

mkdir -p "${LOG_DIR}"


case "${1:-run}" in
    check)
        # **缺任何一样的表现都是「连上了但没有数据」，不报错**，所以一次查完。
        need_python
        for e in webrtcbin nicesrc avdec_h264 videoconvert; do
            printf '  %-16s ' "$e"
            gst-inspect-1.0 "$e" >/dev/null 2>&1 && echo OK || echo 缺
        done
        command -v tmux >/dev/null && echo "  tmux             OK" || echo "  tmux             缺"
        printf '  %-16s ' "OME(WebRTC)"
        timeout 3 bash -c "echo > /dev/tcp/${OME_HOST}/${OME_WS_PORT}" 2>/dev/null \
            && echo "OK  ${OME_HOST}:${OME_WS_PORT}" || echo "不通 ${OME_HOST}:${OME_WS_PORT}"
        # 转写那条链单独查 —— 它和媒体走的是两条路，一条通不代表另一条通。
        printf '  %-16s ' "转写 HTTP"
        "${VLM_PYTHON}" -c "
import sys; sys.path.insert(0,'${HERE}')
from transcript import TranscriptClient
r = TranscriptClient().fetch(5)
print(('OK  最近5s ' + str(len(r['utterances'])) + ' 句') if r['ok']
      else ('不通 ' + str(r.get('error'))))"
        exec "${VLM_PYTHON}" -c "
import gi
for n in ('Gst','GstWebRTC','GstSdp'): gi.require_version(n,'1.0')
from gi.repository import Gst
import numpy
print('  typelib          OK | numpy', numpy.__version__)"
        ;;
    recv)
        need_python; shift
        [ "${1:-}" = "--" ] && shift
        exec "${VLM_PYTHON}" -u "${HERE}/recv.py" "$@"
        ;;
    fg)
        need_python; shift
        [ "${1:-}" = "--" ] && shift
        exec "${VLM_PYTHON}" -u "${HERE}/vlm.py" "$@"
        ;;
    transcript)
        need_python; shift
        [ "${1:-}" = "--" ] && shift
        exec "${VLM_PYTHON}" -u "${HERE}/transcript.py" "$@"
        ;;
    run) ;;
    *)   die "用法: $0 [run|check|recv|transcript|fg]" ;;
esac

need_python
command -v tmux >/dev/null || die "没装 tmux（sudo apt install tmux）"

if tmux has-session -t "${TMUX_SESSION}" 2>/dev/null; then
    info "session '${TMUX_SESSION}' 已经在跑了，接回去"
    exec tmux attach -t "${TMUX_SESSION}"
fi

info "OME   : ${OME_HOST}:${OME_WS_PORT}  (${OME_APP}/*)  —— tailnet"
info "取帧  : ${VLM_FRAMES} 帧 / 跨 ${VLM_SPAN}s，每 ${DECIDE_INTERVAL}s 一轮（★ 都未定）"
info "缓冲  : ${BUFFER_SEC}s @ ${BUFFER_FPS}fps"
info "转写  : ${TRANSCRIPT_URL}（窗 ${TRANSCRIPT_SECONDS}s）"

# 上一次跑剩下的状态会让面板显示一个早就停了的运行时长。
rm -f "${LOG_DIR}/status.json"

tmux new-session  -d -s "${TMUX_SESSION}" -n Status   "'${SELF}' __status"
tmux new-window   -t "${TMUX_SESSION}" -n onboard  "'${SELF}' __tail '${LOG_DIR}/onboard.txt'  '机体麦克风（现场说了什么）'"
tmux new-window   -t "${TMUX_SESSION}" -n operator "'${SELF}' __tail '${LOG_DIR}/operator.txt' '操作者麦克风（操作者说了什么）'"
tmux new-window   -t "${TMUX_SESSION}" -n VLM      "'${SELF}' __tail '${LOG_DIR}/vlm.txt'      'VLM 的判断 —— 尚未实现，窗口先占着（写这个文件就会出现在这里）'"
tmux new-window   -t "${TMUX_SESSION}" -n vlm      "'${SELF}' __vlm"

# 某个窗口万一自己死了，把它留着看错误，别让它凭空消失。
# **必须逐个窗口设。** remain-on-exit 是**窗口**选项 ——
# `set-option -t <session>` 设不上去（tmux 3.2a 实测：不报错，也不生效），
# 加 `-w` 也只作用于当前那一个窗口。表现是窗口一死就消失，错误看不到。
for i in $(tmux list-windows -t "${TMUX_SESSION}" -F '#{window_index}'); do
    tmux setw -t "${TMUX_SESSION}:${i}" remain-on-exit on >/dev/null
done

tmux select-window -t "${TMUX_SESSION}:Status"
info "session '${TMUX_SESSION}' 起来了。别的机器: tmux attach -t ${TMUX_SESSION}"

if [ -t 1 ]; then
    exec tmux attach -t "${TMUX_SESSION}"
else
    info "（不是终端，没有自动 attach。tmux attach -t ${TMUX_SESSION} 手动接）"
fi
