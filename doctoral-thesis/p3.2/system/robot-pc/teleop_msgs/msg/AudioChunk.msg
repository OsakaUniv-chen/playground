# gst 来的一块音频。
#
# **自带解码需要的一切** —— 光靠 bag 就能解出来，不用外部表格。
# 旧实现用 audio_common_msgs/AudioDataStamped 吃过两个亏：不带 channels /
# rate，解码要另找一份记录；而且各发行版的类型定义还不一样。
#
# 现在三路都是 "S16LE"（原样保存）。16ch 阵列本来打算用 FLAC 无损压缩
# （11.3 -> 约 5.5 Mbps），但 **FLAC 最多 8 声道**，16ch 要先拆成两组各编一路，
# 解码端再按时刻拼回去 —— 那是给源数据加了一层要维护的约定，先不做。
# encoding 这个字段照留：事后离线转 FLAC 之后填 "FLAC" 就行，读的人不用改。

std_msgs/Header header      # stamp = 采集时刻（gst 的 PTS 换算成 UNIX 时间）
string encoding             # "S16LE" | "FLAC"
uint16 channels
uint32 sample_rate
uint32 samples              # **每声道**的样本数
uint8[] data
