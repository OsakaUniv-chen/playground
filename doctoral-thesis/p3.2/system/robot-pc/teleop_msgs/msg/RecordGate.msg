# 记录的门控状态。**状态本身也要进 bag** —— 事后才解释得了
# 「这段为什么是空的」：是没人所以没录，还是 trigger 断了在兜底连续录。

std_msgs/Header header

uint8 IDLE=0                # 没在录
uint8 RECORDING=1           # 正在录（trigger=true）
uint8 POSTROLL=2            # trigger 落下了，还在续录 POSTROLL_SEC
uint8 state

bool trigger                # 最后收到的 trigger 值
bool fail_open              # true = 收不到 trigger，正在兜底连续录
string session              # 当前 bag 的名字（空 = 没在录）
string note                 # 状态变化的原因，给人看的
