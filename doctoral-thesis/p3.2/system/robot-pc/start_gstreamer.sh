#!/bin/bash
# robot-pc 的全部传感器推流，一个脚本管完。
#
#   fisheye    Xacti CX-MT500  1920x1080 MJPG -> 裁 1080x1080 -> H.264 ┐  cam.py
#   （音频）   AT-CSP1         -> AAC                                  ┴ 复用后送 OME
#   realsense  RealSense       color -> H.264                          -> OME  cam.py
#   navcam     导航相机        -> H.264                                -> OME  cam.py
#   soundmap   UMA16v2 16ch    -> soundmap.py -> H.264                 -> OME
#   speaker    OME -> 操作者语音 -> AT-CSP1                            <- OME（下行）
#   recorder   上面这些 -> 一个 bag（RECORD_ENABLE=1 时才起）
#
# **管线都在 Python 里（cam.py / soundmap.py / speaker.py），这个脚本只管
# 解析设备、起进程、看着它们。**
#
# 每条流由一个监视循环看着，退出就重起。**设备解析在循环里面** —— 每次重起都
# 重新按名字找设备，所以拔插之后设备号变了也能接回来（详见 __run 的注释）。
# 卡死另算：每个进程自己盯自己，写 .stalled 记号再退出（详见 __supervise）。
#
# 用法:
#   ./start_gstreamer.sh        起。会建一个 tmux session 并接进去
#                               窗口 0 = 状态面板，Ctrl-C 全停
#                               其他窗口 = 每条流的实时输出
#
set -u

SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
HERE="$(dirname "$SELF")"
source "${HERE}/config.env"
: "${OME_HOST:?未设置 —— 没读到 config.env}"

die()  { echo "[error] $*" >&2; exit 1; }
info() { echo "[info] $*"; }

# =====================================================================
# 工具
# =====================================================================

# SRT 的 URI。OME 靠 streamid 区分同一个端口上的多条流。
srt_uri() {
    echo "srt://${OME_HOST}:${OME_SRT_PORT}?mode=caller&latency=${SRT_LATENCY}&streamid=${OME_VHOST}/${OME_APP}/$1"
}

# 按名字解析 /dev/videoN。编号随 USB 枚举顺序变，不能写死。
#   $1 = 名字里的一段（部分匹配）
#   $2 = 可选，必须支持的 fourcc。RealSense 一个设备挂 4 个节点
#        （color / depth / IR / metadata）名字全一样，只能靠格式挑出 color。
resolve_video() {
    local want="$1" fourcc="${2:-}" dev name
    for sysdev in /sys/class/video4linux/video*; do
        [ -e "$sysdev/name" ] || continue
        name="$(cat "$sysdev/name")"
        case "$name" in *"$want"*) ;; *) continue ;; esac
        dev="/dev/$(basename "$sysdev")"
        if [ -n "$fourcc" ]; then
            v4l2-ctl -d "$dev" --list-formats 2>/dev/null | grep -q "'$fourcc'" || continue
        fi
        echo "$dev"; return 0
    done
    return 1
}

# 按名字解析 ALSA 卡，打印卡 id（拼起来就是 hw:CARD=<id>,DEV=0）。
# /proc/asound/cards 是两行一张卡：
#      2 [ATCSP1         ]: USB-Audio - AT-CSP1
#                           audio-technica AT-CSP1 at usb-0000:00:14.0-2, full speed
# 卡号会随插拔变，方括号里的 id 是内核把 USB 产品名的非字母数字去掉生成的，
# 和包装上印的不一定一样（AT-CSP1 -> ATCSP1），所以两行都拿来做部分匹配。
#
# **只查在不在，不试着按某个格式打开** —— 用录一秒来试的话，声道数或采样率
# 填错时也会失败，然后被当成"设备不存在"静默降级，而那恰恰是最该让它
# 报错的情况。
resolve_alsa() {
    local want="${1,,}" id="" blk="" line
    [ -n "$want" ] || return 1        # 名字空的话别去"随便挑一张卡"
    while IFS= read -r line; do
        if [[ "$line" =~ ^[[:space:]]*[0-9]+[[:space:]]\[([^]]*)\] ]]; then
            # 描述在第二行，所以上一张卡要等下一张卡开头了才判断
            [ -n "$id" ] && [[ "${blk,,}" == *"$want"* ]] && { echo "$id"; return 0; }
            id="${BASH_REMATCH[1]// /}"
            blk="$line"
        else
            blk+="$line"
        fi
    done < /proc/asound/cards
    [ -n "$id" ] && [[ "${blk,,}" == *"$want"* ]] && { echo "$id"; return 0; }
    return 1
}

# 设备实际支持的录音声道数（USB 声卡才有 stream0，读它不用打开设备）。
# **只拿来警告，不拿来改配置** —— 静默地换成别的值正是 config.env 一直在
# 避免的事；这里只负责让"配错了"在日志里一眼看得见。
alsa_capture_channels() {
    local f="/proc/asound/$1/stream0"
    [ -r "$f" ] || return 0
    awk '/^Capture:/{c=1} c && /Channels:/{print $2}' "$f" | sort -un | tr '\n' ' '
}

# 编码器。USE_HW_CODEC=1 且 vaapi 在，就用核显。
# **目的是腾 CPU 不是提速** —— 软解 MJPG + x264 会吃掉 1.5~2 个核，
# 而这台机器还要同时跑声音图生成和 bag 写入。
hw_ok() { [ "${USE_HW_CODEC}" = "1" ] && gst-inspect-1.0 vaapih264enc >/dev/null 2>&1; }

is_fake() { [ "${USE_FAKE_SOURCES}" = "1" ]; }

# 记录用的 ROS 环境。**在这里 source，不让人手动做** —— 忘了 source 的表现是
# 进程一起来就 ImportError，然后被重连循环无限重试。
ros_env() {
    [ "${RECORD_ENABLE}" = "1" ] || return 0
    [ -f "${ROS_SETUP}" ] || die "RECORD_ENABLE=1 但找不到 ROS_SETUP=${ROS_SETUP}"
    [ -f "${ROS_WS_SETUP}" ] || die "找不到 ROS_WS_SETUP=${ROS_WS_SETUP} —— 先跑一次 ./setup.sh 把 teleop_msgs build 出来"
    # **source 之前必须关掉 set -u。** ROS 的 setup.bash 会读一堆没设过的变量
    # （AMENT_TRACE_SETUP_FILES 之类），开着 nounset 就直接把脚本打死 ——
    # 表现是每条流都起不来、每 5 秒重试一次（踩过，日志里只有一行
    # "unbound variable"，很难往这上面想）。
    set +u
    # shellcheck disable=SC1090
    source "${ROS_SETUP}"
    source "${ROS_WS_SETUP}"
    set -u
}

# --publish：记录开着的时候，各进程同时把数据发给 recorder。
pub_flag() { [ "${RECORD_ENABLE}" = "1" ] && echo "--publish"; return 0; }

# =====================================================================
# __run <名字> —— 跑一条流，退出即返回。监视循环反复调它。
#
# **设备解析必须在这里，不能在外面。** 相机拔插之后 /dev/videoN 会变号，
# 麦克风也可能中途掉线；解析放在监视循环外面的话，重起时用的还是启动那一刻
# 的旧路径，会对着一个不存在的设备无限失败 —— 那正是按名字解析要避免的事。
# 放在这里，每次重起都重新找一遍。
# =====================================================================

# ---------------------------------------------------------------------
# 三路相机。管线在 cam.py 里，这里只按名字解析设备、把记录的开关传下去。
# ---------------------------------------------------------------------

run_fisheye() {
    local dev="--fake" mic=""
    if ! is_fake; then
        local d
        d="$(resolve_video "${CAM_FISHEYE_NAME}")" \
            || die "找不到鱼眼相机（名字里含 '${CAM_FISHEYE_NAME}'）。cat /sys/class/video4linux/*/name 看看"
        echo "[dev] 鱼眼 ${d}" >&2
        dev="--device ${d}"

        # 机体麦克风复用进这条流，因为操作者要在一个浏览器页面里音画同步地收。
        #
        # **但麦克风不能连累视频。** 卡不在就不传 --mic，cam.py 换成静音源，
        # 视频照常送。这个判断每次重起都重做：掉线的下一轮退到静音、视频立刻
        # 回来；插回去之后的下一次重起自动用回真麦克风。
        local card="" ch
        if card="$(resolve_alsa "${ONBOARD_MIC_NAME}")"; then
            echo "[dev] 机体麦克风 hw:CARD=${card},DEV=0 (${ONBOARD_MIC_CHANNELS}ch ${ONBOARD_MIC_RATE}Hz)" >&2
            # 配置和设备对不上的话，管线会卡在协商失败上反复重起，而错误信息
            # （not-negotiated）根本不说是哪一项不对。这里先把设备自己报的
            # 声道数打出来，省得到现场一项一项试。
            ch="$(alsa_capture_channels "${card}")"
            case " ${ch}" in
                " ") ;;                                       # 不是 USB 声卡，没得查
                *" ${ONBOARD_MIC_CHANNELS} "*) ;;
                *) echo "[dev] ★ ONBOARD_MIC_CHANNELS=${ONBOARD_MIC_CHANNELS}，但设备只报 ${ch}声道 —— 管线八成起不来" >&2 ;;
            esac
            mic="--mic hw:CARD=${card},DEV=0"
        else
            echo "[dev] ★ 找不到机体麦克风（卡名里含 '${ONBOARD_MIC_NAME}'）—— 用静音顶上，视频照常。cat /proc/asound/cards 看看" >&2
        fi
    fi
    exec python3 "${HERE}/cam.py" fisheye ${dev} ${mic} $(pub_flag)
}

run_realsense() {
    # **只取 color。** depth 不打开 —— 原始 16-bit depth 有 195 Mbps，开了整个
    # 存储方案都得重做。RealSense 一个设备挂 4 个 video 节点（color / depth /
    # IR / metadata）名字全一样，只能靠 REALSENSE_FORMAT 把 color 挑出来。
    local dev="--fake"
    if ! is_fake; then
        local d
        d="$(resolve_video "${CAM_REALSENSE_NAME}" "${REALSENSE_FORMAT}")" \
            || die "找不到 RealSense 的 color 节点（名字含 '${CAM_REALSENSE_NAME}' 且支持 ${REALSENSE_FORMAT}）。v4l2-ctl --list-devices / --list-formats-ext 看看"
        echo "[dev] RealSense ${d} (color, ${REALSENSE_FORMAT})" >&2
        dev="--device ${d}"
    fi
    exec python3 "${HERE}/cam.py" realsense ${dev} $(pub_flag)
}

run_navcam() {
    local dev="--fake"
    if ! is_fake; then
        local d
        d="$(resolve_video "${CAM_NAVCAM_NAME}")" \
            || die "找不到导航相机（名字里含 '${CAM_NAVCAM_NAME}'）"
        echo "[dev] 导航 ${d}" >&2
        dev="--device ${d}"
    fi
    exec python3 "${HERE}/cam.py" navcam ${dev} $(pub_flag)
}

run_soundmap() {
    # 生成全在 soundmap.py（采集参数、周期、窗长、麦克风几何、GAIN 都在那边）。
    # 这里只把它 stdout 出来的原始 BGR 帧编码送走，尺寸和帧率问它要。
    # 阵列用 hw:CARD= 引用，ALSA 在打开的那一刻解析，所以拔插不影响。
    #
    # 64x64 用软件 x264 就行 —— 太小了，走 VA-API 没意义，有时反而通不过。
    local caps w h fps fake=""
    caps="$(python3 "${HERE}/soundmap.py" --print-caps)" || die "soundmap.py 起不来（缺 numpy / scipy？）"
    w="$(sed 's/.*width=\([0-9]*\).*/\1/'         <<<"$caps")"
    h="$(sed 's/.*height=\([0-9]*\).*/\1/'        <<<"$caps")"
    fps="$(sed 's|.*framerate=\([0-9]*\)/1.*|\1|' <<<"$caps")"
    [ -n "$w" ] && [ -n "$h" ] && [ -n "$fps" ] || die "看不懂 soundmap.py 给的 caps: ${caps}"
    is_fake && fake="--fake"

    # 卡死由 soundmap.py 自己发现（见那边的 INPUT_TIMEOUT_MS）：
    #   阵列卡死 -> 它自己退 -> 这边 fdsrc 收到 EOF -> 整条重起
    #   下游卡死 -> 它写 stdout 堵住 -> 输入超时 -> 一样自己退
    set -o pipefail
    python3 "${HERE}/soundmap.py" ${fake} $(pub_flag) \
    | gst-launch-1.0 \
        fdsrc fd=0 do-timestamp=true \
        ! rawvideoparse format=bgr width="${w}" height="${h}" framerate="${fps}/1" \
        ! queue max-size-buffers=3 leaky=downstream \
        ! videoconvert ! video/x-raw,format=I420 \
        ! x264enc tune=zerolatency speed-preset=ultrafast bitrate="${SOUNDMAP_BITRATE}" key-int-max="${fps}" \
        ! video/x-h264,profile=baseline \
        ! h264parse config-interval=-1 \
        ! mpegtsmux alignment=7 \
        ! srtsink uri="$(srt_uri "${KEY_SOUNDMAP}")" sync=false
}

run_speaker() {
    # **唯一一条下行流**：从 OME 收操作者语音放到机体扬声器。收流实现在
    # speaker.py（用隔壁 stream-server/ome_receiver.py），这里只负责把设备
    # 解析好传进去 —— 和别的流一样，**每次重起都重新解析**，AT-CSP1 拔了
    # 再插也能接回来。
    #
    # 找不到扬声器就直接退出、让监视循环 5 秒后再试（不像机体麦克风那样
    # 退到静音顶上：那边是怕连累视频，这边本来就只有放音这一件事）。
    local dev="--fake"
    if ! is_fake; then
        local card
        card="$(resolve_alsa "${SPEAKER_NAME}")" \
            || die "找不到机体扬声器（卡名里含 '${SPEAKER_NAME}'）。cat /proc/asound/cards 看看"
        echo "[dev] 机体扬声器 hw:CARD=${card},DEV=0" >&2
        dev="--device hw:CARD=${card},DEV=0"
    fi
    exec python3 "${HERE}/speaker.py" ${dev} $(pub_flag)
}

run_recorder() {
    # 记录。**不是流** —— 它订上面那些进程发出来的 topic，写进一个 bag。
    # 只在 RECORD_ENABLE=1 时进 STREAMS（见下），所以这里不用再判断一次。
    exec python3 "${HERE}/recorder.py"
}

STREAMS=(fisheye realsense navcam soundmap speaker)
# 记录开着的时候多一个窗口。放进 STREAMS 是为了白拿监视循环和面板那一行。
[ "${RECORD_ENABLE}" = "1" ] && STREAMS+=(recorder)

mkdir -p "${LOG_DIR}"

# =====================================================================
# 内部子命令。都是 tmux 窗口里跑的，不用手敲。
# =====================================================================

# __supervise <名字> —— 一条流的监视循环，占一个 tmux 窗口。
#
# **每一轮都重新调 __run**，所以设备解析、麦克风在不在、用不用核显，
# 全部在每次重起时重新决定（见 __run 那一段的注释）。
#
# 顺带分出这一轮是**自己退的**（多半是 OME / 网络）还是**卡死被打死的**
# （USB 或驱动），状态面板分两列显示。卡死由各进程自己发现并写
# <名字>.stalled 记号，这里只负责记账。
__supervise() {
    local name="$1" state="${LOG_DIR}/$1.state" log="${LOG_DIR}/$1.log"
    local n=0 stall=0 rc why
    while true; do
        printf 'up %s %s %s\n' "$(date +%s)" "$n" "$stall" > "$state"
        echo "[run] ===== $(date '+%F %T') =====" >> "$log"
        rm -f "${LOG_DIR}/${name}.stalled"

        "$SELF" __run "$name" 2>&1 | tee -a "$log"
        rc=${PIPESTATUS[0]}

        n=$((n+1))
        why="rc=${rc}"
        if [ -f "${LOG_DIR}/${name}.stalled" ]; then
            stall=$((stall+1)); why="卡死第 ${stall} 次"
            rm -f "${LOG_DIR}/${name}.stalled"
        fi
        printf 'down %s %s %s\n' "$(date +%s)" "$n" "$stall" > "$state"
        echo "[restart] ${name} 第 $n 次重连（${why}, $(date '+%F %T')），${RESTART_SEC}s 后重试" | tee -a "$log"
        sleep "${RESTART_SEC}"
    done
}

# __status —— 窗口 0 的状态面板。**Ctrl-C 在这里 = 全停。**
__status() {
    trap 'echo; echo "  全停中……"; tmux kill-session -t "${TMUX_SESSION}" 2>/dev/null; exit 0' INT TERM

    local now st since n stall dt hh mm ss sc extra line
    while true; do
        printf '\033[H\033[2J'
        now=$(date +%s)
        printf '  \033[1mrobot-pc 流状态\033[0m                %s\n' "$(date '+%F %T')"
        printf '  OME  %s:%s  (%s/%s/*)\n' "${OME_HOST}" "${OME_SRT_PORT}" "${OME_VHOST}" "${OME_APP}"
        printf '  编码 %s\n' "$(hw_ok && echo '核显 VA-API' || echo '软件 x264')"
        if [ "${STALL_CHECK_SEC}" = "0" ]; then
            printf '  看门狗 \033[31m★ 关着 —— 卡死了没人发现\033[0m\n'
        else
            printf '  看门狗 %ss 没数据就重起\n' "$(( STALL_CHECK_SEC * STALL_MISSES ))"
        fi
        if [ "${RECORD_ENABLE}" = "1" ]; then
            printf '  记录 开  %s\n\n' "${RECORD_DIR}"
        else
            printf '  记录 关（RECORD_ENABLE=0，只推流）\n\n'
        fi
        # 表头手写空格对齐：printf 的宽度按字节补齐，中文占 2 个显示列会错位。
        printf '  进程        状态        已运行     重连    卡死     备注\n'
        printf '  %s\n' '--------------------------------------------------------------------------'

        for name in "${STREAMS[@]}"; do
            st=down; since=$now; n=0; stall=0
            [ -f "${LOG_DIR}/${name}.state" ] && read -r st since n stall < "${LOG_DIR}/${name}.state"
            stall=${stall:-0}
            dt=$(( now - since )); [ "$dt" -lt 0 ] && dt=0
            hh=$(( dt / 3600 )); mm=$(( dt % 3600 / 60 )); ss=$(( dt % 60 ))

            # soundmap.py / speaker.py 每 10 秒各打一行，直接借来当备注。
            # **每个进程都每 10 秒打一行 `[10s] ...`**（cam.py / soundmap.py /
            # speaker.py / recorder.py 都是），这里直接借最后一行当备注 ——
            # 面板不需要认识每条流各自的格式。
            extra=""
            if [ "$st" = up ]; then
                line="$(grep '\[10s\]' "${LOG_DIR}/${name}.log" 2>/dev/null | tail -1)"
                extra="$(sed 's/.*\[10s\] //' <<<"$line")"
                extra="${extra:0:44}"
                # ★ = 跟不上 / 出问题；静默 = 操作者没在说话，**不是故障**。
                case "$line" in
                    *★*)   extra="\033[31m${extra}\033[0m" ;;
                    *静默*) extra="\033[33m${extra}\033[0m" ;;
                esac
            fi

            # 颜色码占字节不占列，所以先按宽度补好空格再套颜色，不然会错位。
            sc="$(printf '%-7s' "$stall")"
            [ "$stall" -gt 0 ] && sc="\033[33m${sc}\033[0m"

            if [ "$st" = up ]; then
                printf '  %-11s \033[32m● 运行中\033[0m   %02d:%02d:%02d   %-6s %b %b\n' "$name" "$hh" "$mm" "$ss" "$n" "$sc" "$extra"
            else
                printf '  %-11s \033[31m○ 重连中\033[0m   %-10s %-6s %b %b\n' "$name" "-" "$n" "$sc" "上次退出 $(date -d "@${since}" '+%H:%M:%S' 2>/dev/null)"
            fi
        done

        printf '\n  \033[2m重连 = 进程自己退了（多半是 OME / 网络）  |  卡死 = 看门狗打的（USB / 驱动）\033[0m\n'
        printf '  \033[2m窗口: Ctrl-b 数字切换（1-%d 是各条流的实时输出）\033[0m\n' "${#STREAMS[@]}"
        printf '  \033[2m全停: 在这个窗口按 Ctrl-C   |   离开但不停: Ctrl-b d\033[0m\n'
        sleep "${STATUS_INTERVAL}"
    done
}

case "${1:-}" in
    __run)
        [ $# -ge 2 ] || die "__run 要一个流名字: ${STREAMS[*]}"
        case "$2" in
            fisheye|realsense|navcam|soundmap|speaker|recorder)
                ros_env            # RECORD_ENABLE=1 时 source ROS 和 workspace
                "run_$2" ;;
            *) die "不认识的流 '$2'。有的是: ${STREAMS[*]}" ;;
        esac
        exit $? ;;
    __supervise) __supervise "$2"; exit $? ;;
    __status)    __status;         exit $? ;;
esac

# =====================================================================
# 主流程：建 tmux session 并接进去
# =====================================================================

command -v tmux >/dev/null || die "没装 tmux（sudo apt install tmux）"

if tmux has-session -t "${TMUX_SESSION}" 2>/dev/null; then
    info "session '${TMUX_SESSION}' 已经在跑了，接回去"
    exec tmux attach -t "${TMUX_SESSION}"
fi

info "OME  : ${OME_HOST}:${OME_SRT_PORT}  (${OME_VHOST}/${OME_APP}/*)"
info "编码 : $(hw_ok && echo '核显 VA-API' || echo '软件 x264')"
is_fake && info "★ USE_FAKE_SOURCES=1 —— 用的是测试源，不是真设备"

# 状态文件留着旧的会让面板显示上一次的运行时长。
rm -f "${LOG_DIR}"/*.state

# 窗口 0 = 状态面板（Ctrl-C 在这里全停），窗口 1.. = 每条流
tmux new-session -d -s "${TMUX_SESSION}" -n Status "'${SELF}' __status"
for name in "${STREAMS[@]}"; do
    tmux new-window -t "${TMUX_SESSION}" -n "${name}" "'${SELF}' __supervise '${name}'"
done
# 某条监视循环万一自己死了，把窗口留着看错误，别让它凭空消失。
# **必须逐个窗口设。** remain-on-exit 是**窗口**选项 ——
# `set-option -t <session>` 设不上去（tmux 3.2a 实测：不报错，也不生效），
# 加 `-w` 也只作用于当前那一个窗口。
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
