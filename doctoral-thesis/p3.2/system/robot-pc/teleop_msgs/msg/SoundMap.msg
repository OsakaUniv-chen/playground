# 1-bit 声音图一帧。
#
# 存的是**生成器出来的原始值**，不是送去显示的那张黄斑图 —— 显示用的
# 归一化（exp 变换）和上色是不可逆的，事后要重算或者换算法就没得救了。
#
# 用自定义 msg 而不是 Float32MultiArray：后者没有 header，采样时刻无处安放。

std_msgs/Header header      # stamp = 这一帧所用音频窗的时刻
uint32 width
uint32 height
float32[] data              # 行优先，长度 = width * height
