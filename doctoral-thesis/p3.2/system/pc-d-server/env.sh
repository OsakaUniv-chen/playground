#!/bin/bash
# PC-D の共通環境。**この機械は ROS を使わない**（config.env の説明を参照）。
#   受信側（audio_send.py）  システムの python3 + gi(GStreamer)
#   文字起こし（asr.py）     $ASR_PYTHON（3.10 の venv）+ faster-whisper
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../common/config.env"
source "$HERE/config.env"
export PCD_DIR="$HERE"

# ---- faster-whisper が使う CUDA ライブラリの在り処 ----
# ctranslate2 は CUDA 12 を要求するが、この機械のシステム CUDA は 11.0/11.1。
# venv に入れた nvidia-* wheel（pip 版の CUDA ランタイム）を使うので、
# その lib を loader に見せる。**入れないと `libcublas.so.12 is not found`
# でモデルの読み込みだけ成功して推論で落ちる**（起動直後は正常に見える）。
#
# LD_LIBRARY_PATH はプロセス開始時にしか読まれないので、Python の中からでは
# 遅い。ここで組み立てて、run.sh が asr プロセスにだけ渡す
# （受信側は GStreamer なので、CUDA を loader に混ぜない）。
if [ -x "$ASR_PYTHON" ]; then
    _asr_site="$(dirname "$(dirname "$ASR_PYTHON")")/lib"
    _asr_libs="$(ls -d "$_asr_site"/python*/site-packages/nvidia/*/lib 2>/dev/null \
                 | tr '\n' ':' | sed 's/:$//')"
    [ -n "$_asr_libs" ] && export ASR_LD_LIBRARY_PATH="$_asr_libs"
    unset _asr_site _asr_libs
fi
