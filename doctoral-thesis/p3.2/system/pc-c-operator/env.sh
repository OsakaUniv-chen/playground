#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../common/config.env"
source "$HERE/config.env"
# .bashrc で既に source されていれば読み直さない（PATH が重複するだけなので）。
# systemd / cron から起動した場合はここで読む。
if [ -z "${ROS_DISTRO:-}" ]; then
    source "$ROS_DISTRO_SETUP" 2>/dev/null || echo "warn: $ROS_DISTRO_SETUP が無い"
fi
[ -f "$HOME/ros2_ws/install/setup.bash" ] && source "$HOME/ros2_ws/install/setup.bash"
export PCC_DIR="$HERE"
