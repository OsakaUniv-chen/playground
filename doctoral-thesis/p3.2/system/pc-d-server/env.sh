#!/bin/bash
# PC-D の共通環境。**この機械は ROS を使わない**（config.env の説明を参照）。
# 必要なのは python3 + gi(GStreamer) + numpy だけ。
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../common/config.env"
source "$HERE/config.env"
export PCD_DIR="$HERE"
