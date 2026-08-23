# MONOTONIC 和 REALTIME 的对应关系。1 Hz 记进 bag。
#
# gst 的 PTS 是 MONOTONIC 基准的，换算成 UNIX 时间要加这个 offset。
# **整条序列都留着**：事后看它平滑就说明换算可信，有台阶就说明那里 NTP
# step 过，那一段的时刻要打个问号。

std_msgs/Header header      # stamp = realtime_ns
int64 monotonic_ns
int64 realtime_ns
int64 offset_ns             # realtime_ns - monotonic_ns
