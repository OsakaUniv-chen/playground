#!/bin/bash
# =====================================================================
# 在 rog-server 上从源码装 OvenMediaEngine。
#
# **OME 没有 apt 源。** 官方（OvenMediaLabs，原 AirenSoft）只发 Docker 镜像
# 和源码，release 页面连 .deb 附件都没有。所以 apt 在这里只用来装编译依赖，
# OME 本体和它的十几个第三方库（ffmpeg / openssl / srt / opus / x264 …）
# 全是源码编译。
#
# 用法 —— **必须用 root 跑**：
#     sudo bash install_ome.sh
#
# 不能用普通用户 + sudo 提权：0.21.0 的 cmake 依赖脚本内部自己会调
# `sudo make install`，一次编译要调几十次，sudo 的 15 分钟时间戳撑不到
# 编译结束，会在半路某个库装到一半时突然停下来要密码。
#
# 22 核上大约 20~40 分钟，大头是 ffmpeg 7.1.5。中途失败直接重跑就行 ——
# 每一步都做了幂等处理，已经装好的库 cmake 会跳过。
# =====================================================================
set -euo pipefail

OME_VERSION="0.21.0"
# 源码包。事先下好了就用现成的，省得重下 12 MB。
TARBALL="${TARBALL:-/home/student/rog-pc/ome-v${OME_VERSION}.tar.gz}"
SRC_ROOT="/opt/src"
SRC_DIR="${SRC_ROOT}/OvenMediaEngine-${OME_VERSION}"
# 第三方依赖的安装前缀。**不装进 /usr/local** —— 这些是 OME 专用的特定版本
# （openssl 3.0.7、ffmpeg 7.1.5 …），混进系统库里迟早和别的东西打架。
DEP_PREFIX="/opt/ovenmediaengine"

info() { echo -e "\n\033[1;36m[install_ome] $*\033[0m"; }
die()  { echo -e "\033[1;31m[install_ome] 错误: $*\033[0m" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "要用 root 跑：sudo bash $0"

# ---------------------------------------------------------------------
# 1. 编译依赖
#
# cmake >= 3.24 是 OME 的硬要求（CMakeLists.txt 第一行）。24.04 的 3.28 够。
# 这几个包 cmake 的依赖脚本自己也会装，但 cmake 和 curl 得先有 ——
# 前者是用来跑脚本的，后者是脚本里下源码用的。
# ---------------------------------------------------------------------
info "1/6 装编译依赖"
apt-get update
apt-get install -y build-essential cmake ninja-build clang lld curl git \
    autoconf automake libtool pkg-config tclsh bc uuid-dev zlib1g-dev libgomp1

cmake_ver="$(cmake --version | head -1 | awk '{print $3}')"
printf '3.24\n%s\n' "$cmake_ver" | sort -V -C || die "cmake ${cmake_ver} 太旧，要 >= 3.24"
info "cmake ${cmake_ver} OK"

# ---------------------------------------------------------------------
# 2. 源码
# ---------------------------------------------------------------------
info "2/6 展开源码到 ${SRC_DIR}"
mkdir -p "${SRC_ROOT}"
if [ ! -f "${TARBALL}" ]; then
    info "本地没有 ${TARBALL}，现下"
    curl -sSLf -o "${TARBALL}" \
        "https://github.com/OvenMediaLabs/OvenMediaEngine/archive/refs/tags/v${OME_VERSION}.tar.gz" \
        || die "源码下载失败"
fi
# 重跑时不重复展开 —— build 目录在里面，展开会把编译中间产物冲掉。
if [ ! -f "${SRC_DIR}/CMakeLists.txt" ]; then
    tar xzf "${TARBALL}" -C "${SRC_ROOT}"
else
    info "源码已在，跳过"
fi
cd "${SRC_DIR}"

# ---------------------------------------------------------------------
# 3. configure
#
# 依赖库缺了或版本不对，这一步会自动去下载编译（装进 ${DEP_PREFIX}）。
# 所以这一步就要花掉大部分时间，不是卡住了。
#
# OME_HWACCEL_NVIDIA 保持 OFF：
#   ① 这台机器有 4070 但**没装 CUDA Toolkit**，开了会 fail-fast 直接报错；
#   ② 我们的用法是视频 H.264 bypass 转发，只有 AAC→Opus 这一路音频转码，
#      NVENC 根本用不上。
# 以后真要转码视频（比如给浏览器做 ABR），先装 CUDA Toolkit，再加
# -DOME_HWACCEL_NVIDIA=ON 重新 configure ＋ build。
# ---------------------------------------------------------------------
info "3/6 configure（依赖库会在这一步自动下载编译，慢是正常的）"
cmake -B build/Release -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DOME_DEP_PREFIX="${DEP_PREFIX}"

# ---------------------------------------------------------------------
# 4. 编译
# ---------------------------------------------------------------------
info "4/6 编译 OME 本体"
cmake --build build/Release

# ---------------------------------------------------------------------
# 5. 安装
#
#   /usr/share/ovenmediaengine/OvenMediaEngine   本体
#   /usr/bin/OvenMediaEngine                     符号链接
#   /usr/share/ovenmediaengine/conf/             配置（**已存在就不覆盖**）
#   /lib/systemd/system/ovenmediaengine.service  systemd unit
# ---------------------------------------------------------------------
info "5/6 安装"
cmake --install build/Release

# ---------------------------------------------------------------------
# 6. systemd
#
# 这里只 enable ＋ start 默认配置。端口的默认值本来就和我们的约定一致
# （SRT 9999 / WebRTC signalling 3333 / vhost default / app app），
# 所以先按默认起来验证通路，Server.xml 的调整留到下一步。
# ---------------------------------------------------------------------
info "6/6 起 systemd 服务"
systemctl daemon-reload
systemctl enable ovenmediaengine
systemctl restart ovenmediaengine
sleep 3

echo
if systemctl is-active --quiet ovenmediaengine; then
    info "✓ ovenmediaengine 起来了"
else
    echo "✗ 没起来。看日志：journalctl -u ovenmediaengine -n 50 --no-pager" >&2
fi
echo "--- 监听端口（应该看到 9999/udp SRT、3333 WebRTC、8081 API）---"
ss -tulnp 2>/dev/null | grep -E "OvenMedia|:9999|:3333|:8081|:1935|:9000" || echo "（一个都没有 —— 看 journalctl）"
echo
echo "版本: $(/usr/bin/OvenMediaEngine -v 2>&1 | head -1 || true)"
