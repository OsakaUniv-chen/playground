#!/bin/bash
# robot-pc 的一次性准备：装依赖、build 自定义消息、把该现场确认的都查一遍。
#
#   ./setup.sh          装 + build + 查（要 sudo 密码）
#   ./setup.sh --check  只查，什么都不装、不需要 sudo
#
# **查那一半可以随时重跑**，现场怀疑哪里不对就先跑一次 `--check`：
# 相机在不在、声卡叫什么、gst 的 element 缺没缺、ROS 那套齐不齐，一次看完。
#
# **代码和运行时数据都在 ~/robot-pc/ 下**，和 rog-server 上的 ~/rog-pc/ 对称：
#     ~/robot-pc/{cam.py,recorder.py,...}   代码（rsync 上来的）
#     ~/robot-pc/ws/                        teleop_msgs 的 colcon workspace
#     ~/robot-pc/bag/                       录下来的 bag（RECORD_DIR）
#     ~/robot-pc/log/                       日志（LOG_DIR）
# **同步代码时不要 --delete 整个 ~/robot-pc** —— ws / bag / log 就在同一层。
# 脚本按自己的位置找 config.env，所以代码放哪其实都能跑。
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
source "${HERE}/config.env"

WS="${HOME}/robot-pc/ws"            # teleop_msgs 的 colcon workspace
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m★\033[0m %s\n' "$*"; MISSING=$((MISSING+1)); }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }
MISSING=0

# ROS 的 distro。没 source 也能猜出来 —— 这个脚本本身要能在裸终端里跑。
: "${ROS_SETUP:=}"
if [ -z "${ROS_DISTRO:-}" ]; then
    d="$(ls -d /opt/ros/*/ 2>/dev/null | head -1)"
    [ -n "$d" ] && { ROS_DISTRO="$(basename "$d")"; ROS_SETUP="${d}setup.bash"; }
fi
[ -z "$ROS_SETUP" ] && [ -n "${ROS_DISTRO:-}" ] && ROS_SETUP="/opt/ros/${ROS_DISTRO}/setup.bash"

# =====================================================================
# 装
# =====================================================================

# 命令/element -> 包。**按"东西在不在"判断，不按包名** —— 发行版之间包名会变，
# 而"gst-inspect 找不找得到 srtsink"这个问题在哪都是同一个问题。
APT=()
need_cmd() { command -v "$1" >/dev/null 2>&1 || APT+=("$2"); }
need_el()  { gst-inspect-1.0 "$1" >/dev/null 2>&1 || APT+=("$2"); }

if [ "$CHECK_ONLY" = 0 ]; then
    head_ "1. 系统依赖"
    need_cmd tmux tmux
    need_cmd v4l2-ctl v4l-utils                 # 按名字解析 /dev/videoN 要它
    need_cmd arecord alsa-utils
    need_cmd colcon python3-colcon-common-extensions
    need_el  v4l2src   gstreamer1.0-plugins-good
    need_el  srtsink   gstreamer1.0-plugins-bad     # SRT 推流 + mpegtsmux + voaacenc
    need_el  x264enc   gstreamer1.0-plugins-ugly
    need_el  nicesrc   gstreamer1.0-nice            # ★ 缺了 WebRTC 收流静默失败
    need_el  vaapih264enc gstreamer1.0-vaapi        # 可选：核显编码，缺了自动退软件
    python3 -c "import numpy, scipy" 2>/dev/null || APT+=(python3-numpy python3-scipy)
    if [ -n "${ROS_DISTRO:-}" ]; then
        APT+=("ros-${ROS_DISTRO}-foxglove-msgs")        # CompressedVideo
        APT+=("ros-${ROS_DISTRO}-rosbag2-storage-mcap") # mcap 存储（Jazzy 起是默认）
    else
        bad "没找到 /opt/ros/*/ —— 先装 ROS 2 再跑这个脚本"
    fi
    # 已经装了的就别写进 apt 命令行，省得输出一屏 "already newest"。
    TODO=()
    for p in "${APT[@]}"; do
        dpkg -s "$p" >/dev/null 2>&1 || TODO+=("$p")
    done
    if [ ${#TODO[@]} -eq 0 ]; then
        ok "该装的都装了"
    else
        echo "  要装: ${TODO[*]}"
        sudo apt-get update -qq && sudo apt-get install -y "${TODO[@]}" \
            && ok "装完了" || bad "apt 装失败 —— 上面有原因"
    fi

    head_ "2. 自定义消息 teleop_msgs"
    mkdir -p "${WS}/src"
    # **先扫掉断掉的软链。** 包改过名（p3_msgs -> teleop_msgs）之后，ws/src 里
    # 会留一个指向已经不存在的目录的软链，colcon build 会当场失败。
    find "${WS}/src" -maxdepth 1 -xtype l -exec rm -f {} + 2>/dev/null
    ln -sfn "${HERE}/teleop_msgs" "${WS}/src/teleop_msgs"
    # **用软链不是拷贝**：改了 msg 只要重 build，不用记得同步两份。
    if (cd "$WS" && source "$ROS_SETUP" && colcon build --packages-select teleop_msgs >/tmp/teleop_msgs_build.log 2>&1); then
        ok "build 好了: ${WS}（source ${WS}/install/setup.bash 才用得上）"
    else
        bad "build 失败，看 /tmp/teleop_msgs_build.log"
    fi
fi

# =====================================================================
# 查
# =====================================================================

head_ "3. ROS 那一套"
if [ -z "${ROS_DISTRO:-}" ]; then
    bad "没有 ROS"
else
    ok "ROS ${ROS_DISTRO}"
    # 在 source 过 ROS + workspace 的子 shell 里查，免得污染当前环境。
    # **cd / 之后再 import。** 不然当前目录里那个 teleop_msgs/ 源码目录会被 Python
    # 当成 namespace package，`import teleop_msgs.msg` 照样成功 —— 明明没 build 也报 ✓
    # （踩过：在 robot-pc/ 目录里跑 --check 永远是绿的）。所以既换目录，
    # 又直接 import 生成出来的那个类，光有目录是过不了的。
    rosck() { bash -c "cd /; source '$ROS_SETUP' 2>/dev/null; [ -f '${WS}/install/setup.bash' ] && source '${WS}/install/setup.bash' 2>/dev/null; $1" 2>/dev/null; }
    rosck 'python3 -c "import rclpy"' && ok "rclpy" || bad "rclpy 没有"
    rosck 'python3 -c "import rosbag2_py"' && ok "rosbag2_py" || bad "rosbag2_py 没有"
    rosck 'python3 -c "from teleop_msgs.msg import AudioChunk, SoundMap, ClockOffset, RecordGate"' \
        && ok "teleop_msgs（记录用的自定义消息）" \
        || bad "teleop_msgs 没 build —— 跑一次不带 --check 的 setup.sh"
    rosck 'python3 -c "from foxglove_msgs.msg import CompressedVideo"' && ok "foxglove_msgs（视频消息）" \
        || bad "foxglove_msgs 没有 —— sudo apt install ros-${ROS_DISTRO}-foxglove-msgs"
    if [ "${BAG_STORAGE}" = mcap ]; then
        dpkg -s "ros-${ROS_DISTRO}-rosbag2-storage-mcap" >/dev/null 2>&1 \
            && ok "mcap 存储插件" \
            || bad "没有 mcap 插件（config.env 里 BAG_STORAGE=mcap）—— 装 ros-${ROS_DISTRO}-rosbag2-storage-mcap，或改成 sqlite3"
    fi
    echo "     ROS_DOMAIN_ID=${ROS_DOMAIN_ID}（**要和 rog-server 一致**，不然收不到 trigger）"
fi

head_ "4. GStreamer 的 element"
for e in v4l2src alsasrc jpegdec x264enc h264parse mpegtsmux srtsink voaacenc \
         videocrop videoflip appsink nicesrc; do
    gst-inspect-1.0 "$e" >/dev/null 2>&1 && ok "$e" || bad "$e 缺"
done
gst-inspect-1.0 vaapih264enc >/dev/null 2>&1 && ok "vaapih264enc（核显编码）" \
    || warn "没有 vaapih264enc —— 会退回软件 x264（多吃 1.5~2 个核，能跑）"

head_ "5. 设备"
for spec in "CAM_FISHEYE_NAME:${CAM_FISHEYE_NAME}" "CAM_REALSENSE_NAME:${CAM_REALSENSE_NAME}" \
            "CAM_NAVCAM_NAME:${CAM_NAVCAM_NAME}"; do
    key="${spec%%:*}"; want="${spec#*:}"; found=""
    for s in /sys/class/video4linux/video*; do
        [ -e "$s/name" ] || continue
        case "$(cat "$s/name")" in *"$want"*) found="${found} /dev/$(basename "$s")" ;; esac
    done
    [ -n "$found" ] && ok "${key}='${want}' ->${found}" || bad "${key}='${want}' 找不到"
done
for spec in "ONBOARD_MIC_NAME:${ONBOARD_MIC_NAME}" "SPEAKER_NAME:${SPEAKER_NAME}" "16ch 阵列:UMA16v2"; do
    key="${spec%%:*}"; want="${spec#*:}"
    if grep -qi "$want" /proc/asound/cards 2>/dev/null; then
        ok "${key}='${want}' -> $(grep -i -m1 "$want" /proc/asound/cards | sed 's/^ *//')"
    else
        bad "${key}='${want}' 在 /proc/asound/cards 里找不到"
    fi
done

head_ "6. 录制的落盘位置"
d="$(eval echo "${RECORD_DIR}")"
mkdir -p "$d" 2>/dev/null
if [ -w "$d" ]; then
    free=$(df -BG --output=avail "$d" | tail -1 | tr -dc '0-9')
    [ "${free:-0}" -ge "${RECORD_MIN_FREE_GB%.*}" ] \
        && ok "${d} 剩 ${free} GB（录制期间约 12 GB/小时）" \
        || bad "${d} 只剩 ${free} GB，低于 RECORD_MIN_FREE_GB=${RECORD_MIN_FREE_GB}"
else
    bad "${d} 写不了"
fi

head_ "7. USB 拓扑（等时传输会互相抢，尽量分在不同控制器上）"
command -v lsusb >/dev/null && lsusb -t 2>/dev/null | sed 's/^/  /' | head -20 \
    || warn "没有 lsusb（usbutils）"

printf '\n'
if [ "$MISSING" -eq 0 ]; then
    printf '\033[32m全部就绪。\033[0m 起动: ./start_gstreamer.sh\n'
else
    printf '\033[31m还有 %d 项没过（上面 ★ 的）。\033[0m\n' "$MISSING"
    exit 1
fi
