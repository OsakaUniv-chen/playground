#!/bin/bash
# 语音转文字：从本机 OME 拉两路音频，转成文字。
#
#   ./run_asr.sh            起（前台，Ctrl-C 停）
#   ./run_asr.sh -- <args>  其余参数原样传给 asr.py
#
# **只取音频，不碰视频**（`--no-video`）——「机体麦克风」复用在 fisheye 那条流里
# （架构 §1.1），所以那条流要连，但只接音频 pad，省掉一路 1080×1080 的 H.264 解码。
#
# **和 run_stream.sh 是两个进程、两个 venv**（ctranslate2 要 CUDA 12 的 cudnn，
# 而那边的 torch 是 cu130）。**和 YOLO 抢算力**，跟不上就把 ASR_MODEL 降到 small。
#
# 设计见 ../system-architecture.md §5。
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
APP="${HERE}/stream-server"
source "${APP}/config.env"

: "${OME_HOST:?未设置 —— 没读到 config.env}"
mkdir -p "${LOG_DIR}" "${ASR_MODEL_DIR}"

[ -x "${ASR_PYTHON}" ] || {
    echo "[error] ASR_PYTHON 不存在: ${ASR_PYTHON}" >&2
    echo "        先按 stream-server/README.md 的「环境」建好 venv-asr，再回来。" >&2
    exit 1
}

# **★ ctranslate2 找不到 CUDA 库，除非把 pip 装的那几个 nvidia 包指给它。**
# 踩过一次，而且是最难发现的那种：模型能加载、`nvidia-smi` 上看得到显存被占、
# `import faster_whisper` 也没事 —— **只有真的有人说话、编码器第一次跑的时候
# 才炸**（`Library libcublas.so.12 is not found`）。测试信号是静音，被
# `vad_filter` 挡在编码器之前，所以怎么测都测不出来。
#
# 这两个库来自 `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`，装在
# venv 的 site-packages 里，动态链接器默认不看那儿。
_sp="$("${ASR_PYTHON}" -c 'import site; print(site.getsitepackages()[0])')"
for d in "${_sp}/nvidia/cublas/lib" "${_sp}/nvidia/cudnn/lib"; do
    [ -d "$d" ] && LD_LIBRARY_PATH="${d}:${LD_LIBRARY_PATH:-}"
done
export LD_LIBRARY_PATH
[ -f "${_sp}/nvidia/cublas/lib/libcublas.so.12" ] || {
    echo "[error] 找不到 libcublas.so.12 —— venv-asr 里缺 nvidia 的库。" >&2
    echo "        ${ASR_PYTHON%/bin/python3}/bin/pip install nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'" >&2
    exit 1
}

[ "${1:-}" = "--" ] && shift

# **不要去掉 -u。** 输出重定向到文件时，不加的话 Python 会块缓冲 stdout，
# 异常退出的进程会留下一个 0 字节的日志（旧实现的操作者端上踩过）。
exec "${ASR_PYTHON}" -u "${APP}/asr.py" --no-video "$@"
