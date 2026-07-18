# eighth-run 结果报告

只用 chat（3 个 bag，`train_ratio=0.9` 时间切分，后10%测试）做小规模对照，攻七轮
都没解决的核心问题——位置相关性低于朴素基线（见 CONTEXT.md "当前开放问题"）。两个
实验分开做、单一变量：
- **实验A**：loss 监督目标——`--loss mse`（像素重建，历轮都是这个）vs
  `--loss softargmax`（可导 soft-argmax 直接监督峰值位置，见 losses.py）。
- **实验B**：输入窗口长度——`--clip-len 10`（5s）vs `--clip-len 20`（10s）。

其余超参固定在 sixth-run 的 chat 最佳配方（lr=1e-3、noise_ratio 增强）。**早停监控
的是 test peak_dist（不是 test_loss）**，让不同 loss 的对照公平。

## 评价指标说明

同一套指标（peak_dist k=1、PSR_k5@5、位置相关性 Pearson r），同一套朴素连续性基线。

---
## 2026-07-16 08:16 A_mse_control

配置：loss=mse (control), clip_len=10。测试集：chat 3 个bag各自最后10%（608 个窗口）。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.37 | 6.32 | 6.64 | 6.14 |
| eighth-run (A_mse_control) | 5.35 | 6.17 | 6.35 | 6.87 | 6.19 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 79.61% | 73.85% | 73.68% | 72.37% | 74.88% |
| eighth-run (A_mse_control) | 78.95% | 75.00% | 73.85% | 71.05% | 74.71% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.374 | 0.285 | 0.306 | 0.288 | 0.313 |
| eighth-run (A_mse_control) | 0.332 | 0.311 | 0.302 | 0.248 | 0.298 |
| baseline (repeat-last) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |
| naive continuity (last-input pos) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |

---
## 2026-07-16 08:16 A_softargmax

配置：loss=softargmax, clip_len=10。测试集：chat 3 个bag各自最后10%（608 个窗口）。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.37 | 6.32 | 6.64 | 6.14 |
| eighth-run (A_softargmax) | 7.90 | 9.33 | 9.18 | 10.03 | 9.11 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 79.61% | 73.85% | 73.68% | 72.37% | 74.88% |
| eighth-run (A_softargmax) | 78.12% | 73.03% | 71.55% | 67.76% | 72.62% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.374 | 0.285 | 0.306 | 0.288 | 0.313 |
| eighth-run (A_softargmax) | 0.324 | 0.279 | 0.259 | 0.172 | 0.259 |
| baseline (repeat-last) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |
| naive continuity (last-input pos) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |

---
## 2026-07-16 08:39 A_softargmax_lr1e-4

配置：loss=softargmax, clip_len=10, lr=1e-4。测试集：chat 3 个bag各自最后10%（608 个窗口）。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.37 | 6.32 | 6.64 | 6.14 |
| eighth-run (A_softargmax_lr1e-4) | 6.29 | 6.97 | 7.87 | 8.28 | 7.36 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 79.61% | 73.85% | 73.68% | 72.37% | 74.88% |
| eighth-run (A_softargmax_lr1e-4) | 75.66% | 69.08% | 61.02% | 61.68% | 66.86% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.374 | 0.285 | 0.306 | 0.288 | 0.313 |
| eighth-run (A_softargmax_lr1e-4) | 0.295 | 0.290 | 0.260 | 0.229 | 0.269 |
| baseline (repeat-last) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |
| naive continuity (last-input pos) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |

---
## 2026-07-16 09:42 A_softargmax_lr1e-6

配置：loss=softargmax, clip_len=10, lr=1e-6。测试集：chat 3 个bag各自最后10%（608 个窗口）。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.37 | 6.32 | 6.64 | 6.14 |
| eighth-run (A_softargmax_lr1e-6) | 6.01 | 9.72 | 10.99 | 9.62 | 9.09 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 79.61% | 73.85% | 73.68% | 72.37% | 74.88% |
| eighth-run (A_softargmax_lr1e-6) | 23.68% | 16.94% | 22.20% | 17.93% | 20.19% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.374 | 0.285 | 0.306 | 0.288 | 0.313 |
| eighth-run (A_softargmax_lr1e-6) | 0.360 | 0.201 | 0.210 | 0.196 | 0.242 |
| baseline (repeat-last) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |
| naive continuity (last-input pos) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |

---
## 2026-07-16 10:27 B_clip20

配置：loss=mse, clip_len=20 (10s window, vs A_mse_control's 5s)。测试集：chat 3 个bag各自最后10%（576 个窗口）。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.38 | 6.29 | 6.66 | 6.14 |
| eighth-run (B_clip20) | 5.37 | 6.35 | 6.27 | 6.80 | 6.20 |
| baseline (repeat-last) | 5.73 | 7.11 | 7.64 | 8.08 | 7.14 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 79.69% | 73.78% | 73.78% | 72.22% | 74.87% |
| eighth-run (B_clip20) | 78.82% | 74.13% | 74.13% | 71.18% | 74.57% |
| baseline (repeat-last) | 77.78% | 71.18% | 68.92% | 66.32% | 71.05% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.382 | 0.290 | 0.323 | 0.294 | 0.322 |
| eighth-run (B_clip20) | 0.358 | 0.300 | 0.324 | 0.291 | 0.318 |
| baseline (repeat-last) | 0.325 | 0.212 | 0.206 | 0.176 | 0.230 |
| naive continuity (last-input pos) | 0.325 | 0.212 | 0.206 | 0.176 | 0.230 |

---
## 2026-07-16 12:57 C_g1g2_train_g3_test [chat]

配置：loss=mse, clip_len=20, train=chat+G1+G2, test=chat后10%+G3 game3-6。测试集：chat 3 个bag各自最后10%（训练集里的90%）（576 个窗口）。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.38 | 6.29 | 6.66 | 6.14 |
| eighth-run (C_g1g2_train_g3_test) | 5.33 | 6.36 | 6.50 | 7.15 | 6.34 |
| baseline (repeat-last) | 5.73 | 7.11 | 7.64 | 8.08 | 7.14 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 79.69% | 73.78% | 73.78% | 72.22% | 74.87% |
| eighth-run (C_g1g2_train_g3_test) | 78.82% | 73.96% | 73.26% | 69.79% | 73.96% |
| baseline (repeat-last) | 77.78% | 71.18% | 68.92% | 66.32% | 71.05% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.382 | 0.290 | 0.323 | 0.294 | 0.322 |
| eighth-run (C_g1g2_train_g3_test) | 0.369 | 0.294 | 0.295 | 0.265 | 0.306 |
| baseline (repeat-last) | 0.325 | 0.212 | 0.206 | 0.176 | 0.230 |
| naive continuity (last-input pos) | 0.325 | 0.212 | 0.206 | 0.176 | 0.230 |

---
## 2026-07-16 12:58 C_g1g2_train_g3_test [G3_game3-6]

配置：loss=mse, clip_len=20, train=chat+G1+G2, test=chat后10%+G3 game3-6。测试集：WordWolfExp G3 的 game3/4/5/6（完全held-out，训练集含chat+G1+G2）（1440 个窗口）。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 9.70 | 12.96 | 14.27 | 14.83 | 12.94 |
| eighth-run (C_g1g2_train_g3_test) | 9.06 | 10.97 | 11.42 | 11.39 | 10.71 |
| baseline (repeat-last) | 9.40 | 11.62 | 13.02 | 13.86 | 11.98 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 67.08% | 55.42% | 50.62% | 48.40% | 55.38% |
| eighth-run (C_g1g2_train_g3_test) | 69.65% | 62.57% | 61.04% | 61.60% | 63.72% |
| baseline (repeat-last) | 67.36% | 59.72% | 53.82% | 50.97% | 57.97% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.428 | 0.265 | 0.164 | 0.116 | 0.243 |
| eighth-run (C_g1g2_train_g3_test) | 0.431 | 0.188 | 0.061 | 0.044 | 0.181 |
| baseline (repeat-last) | 0.456 | 0.302 | 0.220 | 0.145 | 0.281 |
| naive continuity (last-input pos) | 0.456 | 0.302 | 0.220 | 0.145 | 0.281 |

---
## 2026-07-16 14:43 D_horizon1s [chat]

配置：loss=mse, clip_len=20, 只预测t+2(+1s), train=chat+G1+G2, test=chat后10%+G3 game3-6。测试集：chat 3 个bag各自最后10%（训练集里的90%）（576 个窗口）。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+2 | 平均 |
|---|---|---|
| old (exp4) | 6.25 | 6.25 |
| eighth-run (D_horizon1s) | 6.36 | 6.36 |
| baseline (repeat-last) | 7.11 | 7.11 |

### PSR_k5@5 (成功率, 越大越好)

| | t+2 | Aggregate |
|---|---|---|
| old (exp4) | 74.31% | 74.31% |
| eighth-run (D_horizon1s) | 73.96% | 73.96% |
| baseline (repeat-last) | 71.18% | 71.18% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+2 | 平均 |
|---|---|---|
| old (exp4) | 0.296 | 0.296 |
| eighth-run (D_horizon1s) | 0.297 | 0.297 |
| baseline (repeat-last) | 0.212 | 0.212 |
| naive continuity (last-input pos) | 0.212 | 0.212 |

---
## 2026-07-16 14:43 D_horizon1s [G3_game3-6]

配置：loss=mse, clip_len=20, 只预测t+2(+1s), train=chat+G1+G2, test=chat后10%+G3 game3-6。测试集：WordWolfExp G3 的 game3/4/5/6（完全held-out，训练集含chat+G1+G2）（1440 个窗口）。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+2 | 平均 |
|---|---|---|
| old (exp4) | 13.00 | 13.00 |
| eighth-run (D_horizon1s) | 11.02 | 11.02 |
| baseline (repeat-last) | 11.65 | 11.65 |

### PSR_k5@5 (成功率, 越大越好)

| | t+2 | Aggregate |
|---|---|---|
| old (exp4) | 55.28% | 55.28% |
| eighth-run (D_horizon1s) | 62.71% | 62.71% |
| baseline (repeat-last) | 59.65% | 59.65% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+2 | 平均 |
|---|---|---|
| old (exp4) | 0.262 | 0.262 |
| eighth-run (D_horizon1s) | 0.191 | 0.191 |
| baseline (repeat-last) | 0.303 | 0.303 |
| naive continuity (last-input pos) | 0.303 | 0.303 |

---
## 2026-07-16 16:44 train/test 分列复核（新指标口径：逐步不合并，重点t+2）

### A_mse_control (clip_len=10, dataset=chat, offsets=[1, 2, 3, 4])

#### TRAIN（5792 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| eighth-run (A_mse_control) | 5.38 | 6.62 | 7.18 | 7.65 | 6.71 |
| baseline (repeat-last) | 5.85 | 7.45 | 8.15 | 8.52 | 7.49 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| eighth-run (A_mse_control) | 78.11% | 72.13% | 69.63% | 67.49% | 71.84% |
| baseline (repeat-last) | 75.83% | 68.32% | 65.04% | 63.47% | 68.16% |

#### TEST [chat]（608 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| eighth-run (A_mse_control) | 5.35 | 6.17 | 6.35 | 6.87 | 6.19 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| eighth-run (A_mse_control) | 78.95% | 75.00% | 73.85% | 71.05% | 74.71% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### A_softargmax (clip_len=10, dataset=chat, offsets=[1, 2, 3, 4])

#### TRAIN（5792 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| eighth-run (A_softargmax) | 8.05 | 9.53 | 9.68 | 10.22 | 9.37 |
| baseline (repeat-last) | 5.85 | 7.45 | 8.15 | 8.52 | 7.49 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| eighth-run (A_softargmax) | 75.64% | 69.39% | 67.08% | 64.93% | 69.26% |
| baseline (repeat-last) | 75.83% | 68.32% | 65.04% | 63.47% | 68.16% |

#### TEST [chat]（608 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| eighth-run (A_softargmax) | 7.90 | 9.33 | 9.18 | 10.03 | 9.11 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| eighth-run (A_softargmax) | 78.12% | 73.03% | 71.55% | 67.76% | 72.62% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### A_softargmax_lr1e-4 (clip_len=10, dataset=chat, offsets=[1, 2, 3, 4])

#### TRAIN（5792 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| eighth-run (A_softargmax_lr1e-4) | 6.12 | 7.59 | 8.31 | 8.79 | 7.70 |
| baseline (repeat-last) | 5.85 | 7.45 | 8.15 | 8.52 | 7.49 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| eighth-run (A_softargmax_lr1e-4) | 75.98% | 66.44% | 56.77% | 61.43% | 65.15% |
| baseline (repeat-last) | 75.83% | 68.32% | 65.04% | 63.47% | 68.16% |

#### TEST [chat]（608 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| eighth-run (A_softargmax_lr1e-4) | 6.29 | 6.97 | 7.87 | 8.28 | 7.36 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| eighth-run (A_softargmax_lr1e-4) | 75.66% | 69.08% | 61.02% | 61.68% | 66.86% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### A_softargmax_lr1e-6 (clip_len=10, dataset=chat, offsets=[1, 2, 3, 4])

#### TRAIN（5792 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| eighth-run (A_softargmax_lr1e-6) | 6.14 | 9.80 | 10.76 | 10.05 | 9.19 |
| baseline (repeat-last) | 5.85 | 7.45 | 8.15 | 8.52 | 7.49 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| eighth-run (A_softargmax_lr1e-6) | 25.74% | 18.80% | 23.08% | 25.02% | 23.16% |
| baseline (repeat-last) | 75.83% | 68.32% | 65.04% | 63.47% | 68.16% |

#### TEST [chat]（608 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| eighth-run (A_softargmax_lr1e-6) | 6.01 | 9.72 | 10.99 | 9.62 | 9.09 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| eighth-run (A_softargmax_lr1e-6) | 23.68% | 16.94% | 22.20% | 17.93% | 20.19% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### B_clip20 (clip_len=20, dataset=chat, offsets=[1, 2, 3, 4])

#### TRAIN（5760 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| eighth-run (B_clip20) | 5.09 | 6.30 | 6.76 | 7.20 | 6.34 |
| baseline (repeat-last) | 5.84 | 7.43 | 8.14 | 8.50 | 7.48 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| eighth-run (B_clip20) | 78.99% | 73.68% | 71.61% | 69.53% | 73.45% |
| baseline (repeat-last) | 75.89% | 68.40% | 65.09% | 63.56% | 68.23% |

#### TEST [chat]（576 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| eighth-run (B_clip20) | 5.37 | 6.35 | 6.27 | 6.80 | 6.20 |
| baseline (repeat-last) | 5.73 | 7.11 | 7.64 | 8.08 | 7.14 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| eighth-run (B_clip20) | 78.82% | 74.13% | 74.13% | 71.18% | 74.57% |
| baseline (repeat-last) | 77.78% | 71.18% | 68.92% | 66.32% | 71.05% |

### C_g1g2_train_g3_test (clip_len=20, dataset=chat_g1g2_g3, offsets=[1, 2, 3, 4])

#### TRAIN（11936 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| eighth-run (C_g1g2_train_g3_test) | 7.98 | 9.27 | 9.88 | 10.24 | 9.34 |
| baseline (repeat-last) | 8.58 | 10.27 | 11.08 | 11.55 | 10.37 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| eighth-run (C_g1g2_train_g3_test) | 70.33% | 64.83% | 62.46% | 60.97% | 64.65% |
| baseline (repeat-last) | 67.74% | 60.31% | 57.00% | 55.01% | 60.02% |

#### TEST [chat]（576 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| eighth-run (C_g1g2_train_g3_test) | 5.33 | 6.36 | 6.50 | 7.15 | 6.34 |
| baseline (repeat-last) | 5.73 | 7.11 | 7.64 | 8.08 | 7.14 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| eighth-run (C_g1g2_train_g3_test) | 78.82% | 73.96% | 73.26% | 69.79% | 73.96% |
| baseline (repeat-last) | 77.78% | 71.18% | 68.92% | 66.32% | 71.05% |

#### TEST [G3_game3-6]（1440 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| eighth-run (C_g1g2_train_g3_test) | 9.06 | 10.97 | 11.42 | 11.39 | 10.71 |
| baseline (repeat-last) | 9.40 | 11.62 | 13.02 | 13.86 | 11.98 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| eighth-run (C_g1g2_train_g3_test) | 69.65% | 62.57% | 61.04% | 61.60% | 63.72% |
| baseline (repeat-last) | 67.36% | 59.72% | 53.82% | 50.97% | 57.97% |

### D_horizon1s (clip_len=20, dataset=chat_g1g2_g3, offsets=[2])

#### TRAIN（11968 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+2 | 平均(仅参考) |
|---|---|---|
| eighth-run (D_horizon1s) | 9.35 | 9.35 |
| baseline (repeat-last) | 10.27 | 10.27 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+2 | Aggregate(仅参考) |
|---|---|---|
| eighth-run (D_horizon1s) | 65.02% | 65.02% |
| baseline (repeat-last) | 60.35% | 60.35% |

#### TEST [chat]（576 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+2 | 平均(仅参考) |
|---|---|---|
| eighth-run (D_horizon1s) | 6.36 | 6.36 |
| baseline (repeat-last) | 7.11 | 7.11 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+2 | Aggregate(仅参考) |
|---|---|---|
| eighth-run (D_horizon1s) | 73.96% | 73.96% |
| baseline (repeat-last) | 71.18% | 71.18% |

#### TEST [G3_game3-6]（1440 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+2 | 平均(仅参考) |
|---|---|---|
| eighth-run (D_horizon1s) | 11.02 | 11.02 |
| baseline (repeat-last) | 11.65 | 11.65 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+2 | Aggregate(仅参考) |
|---|---|---|
| eighth-run (D_horizon1s) | 62.71% | 62.71% |
| baseline (repeat-last) | 59.65% | 59.65% |


---
