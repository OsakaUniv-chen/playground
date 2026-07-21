# 评测结果

## eval_32b_awq_2026-07-21.txt — Qwen2.5-VL-32B-Instruct-AWQ

远程 3090PC 上跑通 32B-AWQ，对 word-wolf trial-2 的 160 张带 GT 音图评测
(`eval_labeled.py`，与 3B 探针同 prompt，4 分类 Left/Right/Teleoperator/Others)。

**4 分类准确率: 110/160 = 68.8%**（随机基线 25%），0 未解析，~6.8s/张。

混淆矩阵 (行=gt, 列=pred):

| gt＼pred | Left | Right | Teleop | Others |
|---------|------|-------|--------|--------|
| Left | 35 | 0 | 5 | 0 |
| Right | 0 | 35 | 5 | 0 |
| Teleoperator | 0 | 0 | 40 | 0 |
| Others | 8 | 8 | 24 | 0 |

**要点**
- 方向类(Left/Right/Teleoperator)准确率 = 110/120 = **91.7%**，Teleoperator **40/40**。
  → 32B 能读出音源方向，不像 3B 那样塌缩成常数。
- 短板: **Others(安静/无明显声源) 0/40**，模型从不输出 Others、总硬选一个方向。
  大概率是 prompt/阈值问题(有热力图就挑最强区域), 可在 prompt 里强调"整体很弱→Others"
  或结合 vad_active 信号再试。
