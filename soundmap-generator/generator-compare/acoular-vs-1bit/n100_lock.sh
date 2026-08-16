#!/usr/bin/env bash
# Clamp the emulated-N100 cores to a fixed clock (the one part of n100.py that
# needs root). Sets scaling_min_freq == scaling_max_freq, not just the maximum:
# clamping only the ceiling would let the governor drop below 3.4 GHz under load
# and make the emulated N100 look slower than the real part.
#
#   sudo bash n100_lock.sh lock       # pin cpu12-15 to 3.4 GHz
#   bash n100_lock.sh status          # show current limits (no root needed)
#   sudo bash n100_lock.sh unlock     # restore the hardware defaults
#
# CORES / KHZ override the defaults, e.g.
#   sudo CORES='12 13 14 15' KHZ=3400000 bash n100_lock.sh lock
set -euo pipefail

CORES=${CORES:-"12 13 14 15"}
KHZ=${KHZ:-3400000}
CMD=${1:-status}

cpufreq() { echo "/sys/devices/system/cpu/cpu$1/cpufreq/$2"; }

need_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "error: '$CMD' writes to /sys/devices/system/cpu/*/cpufreq -- re-run with sudo." >&2
        exit 1
    fi
}

case "$CMD" in
lock)
    need_root
    for c in $CORES; do
        [ -d "$(dirname "$(cpufreq "$c" scaling_max_freq)")" ] || { echo "no cpufreq for cpu$c" >&2; exit 1; }
        # raise the ceiling first, then the floor: writing a floor above the
        # current ceiling is rejected by the driver.
        echo "$KHZ" > "$(cpufreq "$c" scaling_max_freq)"
        echo "$KHZ" > "$(cpufreq "$c" scaling_min_freq)"
    done
    echo "locked cpus [$CORES] to $((KHZ / 1000)) MHz"
    ;;
unlock)
    need_root
    for c in $CORES; do
        echo "$(cat "$(cpufreq "$c" cpuinfo_min_freq)")" > "$(cpufreq "$c" scaling_min_freq)"
        echo "$(cat "$(cpufreq "$c" cpuinfo_max_freq)")" > "$(cpufreq "$c" scaling_max_freq)"
    done
    echo "restored hardware defaults on cpus [$CORES]"
    ;;
status)
    printf '%-6s %10s %10s %10s %10s\n' cpu min max cur governor
    for c in $CORES; do
        printf '%-6s %10s %10s %10s %10s\n' "cpu$c" \
            "$(cat "$(cpufreq "$c" scaling_min_freq)")" \
            "$(cat "$(cpufreq "$c" scaling_max_freq)")" \
            "$(cat "$(cpufreq "$c" scaling_cur_freq)")" \
            "$(cat "$(cpufreq "$c" scaling_governor)")"
    done
    ;;
*)
    echo "usage: [sudo] bash n100_lock.sh {lock|unlock|status}" >&2
    exit 2
    ;;
esac
