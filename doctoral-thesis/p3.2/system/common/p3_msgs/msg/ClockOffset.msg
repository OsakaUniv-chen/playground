# MONOTONIC と REALTIME の対応。1 Hz で publish し、bag に残す。
# gst の PTS を UNIX 時間へ換算する際の基準。
std_msgs/Header header      # stamp = realtime
int64 monotonic_ns
int64 realtime_ns
int64 offset_ns             # realtime_ns - monotonic_ns
