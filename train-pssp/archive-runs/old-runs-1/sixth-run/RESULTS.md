# sixth-run 结果报告

只用 chat（3 个 bag，每个按 `train_ratio=0.9` 做时间切分，前90%训练/后10%测试，和
exp4 自己的切分方式完全一致）做小规模消融，逐一验证 fifth-run 深挖 exp4 训练代码
后找到的三个真实训练管线差异（见 CONTEXT.md 的 fifth-run 一节）：lr、滑窗起点密度、
数据增强。测试集在所有消融里保持不变（sparse/2Hz，每个 bag 最后10%），只变训练时
的那一个变量，结果才能直接比较。

## 评价指标说明

见 third-run/fifth-run RESULTS.md，同一套指标（peak_dist k=1、PSR_k5@5、位置相关性
Pearson r），同一套朴素连续性基线。

---
## 2026-07-14 14:42 ablation1_sparse_oldlr

配置：消融1: sparse windows, lr=1e-6 (老模型真实lr), 无数据增强。测试集：chat 3 个bag各自最后10%（608 个窗口）。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.37 | 6.32 | 6.64 | 6.14 |
| sixth-run (ablation1_sparse_oldlr) | 5.61 | 6.40 | 6.83 | 7.30 | 6.53 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 79.61% | 73.85% | 73.68% | 72.37% | 74.88% |
| sixth-run (ablation1_sparse_oldlr) | 78.45% | 74.18% | 71.55% | 68.75% | 73.23% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.374 | 0.285 | 0.306 | 0.288 | 0.313 |
| sixth-run (ablation1_sparse_oldlr) | 0.350 | 0.268 | 0.289 | 0.244 | 0.288 |
| baseline (repeat-last) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |
| naive continuity (last-input pos) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |

---
## 2026-07-14 14:42 ablation1_dense_oldlr

配置：消融1: dense windows (~30Hz起点密度), lr=1e-6 (老模型真实lr), 无数据增强, 手动在epoch5停止(平台期已稳定, 60epoch要17小时)。测试集：chat 3 个bag各自最后10%（608 个窗口）。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.37 | 6.32 | 6.64 | 6.14 |
| sixth-run (ablation1_dense_oldlr) | 5.52 | 6.32 | 6.78 | 7.11 | 6.43 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 79.61% | 73.85% | 73.68% | 72.37% | 74.88% |
| sixth-run (ablation1_dense_oldlr) | 78.78% | 74.34% | 71.55% | 69.57% | 73.56% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.374 | 0.285 | 0.306 | 0.288 | 0.313 |
| sixth-run (ablation1_dense_oldlr) | 0.344 | 0.289 | 0.273 | 0.282 | 0.297 |
| baseline (repeat-last) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |
| naive continuity (last-input pos) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |

---
## 2026-07-14 16:20 ablation2_dense_newlr

配置：消融2: dense windows, lr=1e-3 (第三/四/五run用的lr), 无数据增强, epoch1最优后过拟合, 手动在epoch5停止。测试集：chat 3 个bag各自最后10%（608 个窗口）。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.37 | 6.32 | 6.64 | 6.14 |
| sixth-run (ablation2_dense_newlr) | 5.28 | 6.30 | 6.62 | 7.15 | 6.34 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 79.61% | 73.85% | 73.68% | 72.37% | 74.88% |
| sixth-run (ablation2_dense_newlr) | 79.11% | 74.01% | 71.71% | 69.90% | 73.68% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.374 | 0.285 | 0.306 | 0.288 | 0.313 |
| sixth-run (ablation2_dense_newlr) | 0.335 | 0.291 | 0.293 | 0.255 | 0.294 |
| baseline (repeat-last) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |
| naive continuity (last-input pos) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |

---
## 2026-07-14 17:34 ablation3_dense_newlr_aug

配置：消融3: dense windows, lr=1e-3, 数据增强(复刻exp4的flip+random-crop), epoch1最优后过拟合, 手动在epoch4停止。测试集：chat 3 个bag各自最后10%（608 个窗口）。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.37 | 6.32 | 6.64 | 6.14 |
| sixth-run (ablation3_dense_newlr_aug) | 5.28 | 6.11 | 6.36 | 6.59 | 6.08 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 79.61% | 73.85% | 73.68% | 72.37% | 74.88% |
| sixth-run (ablation3_dense_newlr_aug) | 79.28% | 74.67% | 73.52% | 72.37% | 74.96% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.374 | 0.285 | 0.306 | 0.288 | 0.313 |
| sixth-run (ablation3_dense_newlr_aug) | 0.352 | 0.293 | 0.299 | 0.281 | 0.306 |
| baseline (repeat-last) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |
| naive continuity (last-input pos) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |

---
## 2026-07-14 18:14 extra_sparse_newlr_aug

配置：额外测试: sparse windows, lr=1e-3, 数据增强(复刻exp4)——隔离dense滑窗的贡献，看lr+增强单独在sparse数据上能到什么水平。测试集：chat 3 个bag各自最后10%（608 个窗口）。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.37 | 6.32 | 6.64 | 6.14 |
| sixth-run (extra_sparse_newlr_aug) | 5.32 | 6.22 | 6.42 | 6.59 | 6.14 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 79.61% | 73.85% | 73.68% | 72.37% | 74.88% |
| sixth-run (extra_sparse_newlr_aug) | 79.11% | 74.67% | 73.85% | 71.88% | 74.88% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.374 | 0.285 | 0.306 | 0.288 | 0.313 |
| sixth-run (extra_sparse_newlr_aug) | 0.359 | 0.311 | 0.301 | 0.304 | 0.319 |
| baseline (repeat-last) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |
| naive continuity (last-input pos) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |

---
## 2026-07-14 18:54 extra_sparse_newlr_noiseratio

配置：额外测试2: sparse windows, lr=1e-3, 新数据增强(高斯噪声+sm_ratio抖动，不用旋转)——和flipcrop直接对比。测试集：chat 3 个bag各自最后10%（608 个窗口）。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.37 | 6.32 | 6.64 | 6.14 |
| sixth-run (extra_sparse_newlr_noiseratio) | 5.38 | 6.14 | 6.20 | 6.72 | 6.11 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 79.61% | 73.85% | 73.68% | 72.37% | 74.88% |
| sixth-run (extra_sparse_newlr_noiseratio) | 78.62% | 75.00% | 74.01% | 72.04% | 74.92% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.374 | 0.285 | 0.306 | 0.288 | 0.313 |
| sixth-run (extra_sparse_newlr_noiseratio) | 0.353 | 0.297 | 0.328 | 0.287 | 0.316 |
| baseline (repeat-last) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |
| naive continuity (last-input pos) | 0.349 | 0.231 | 0.199 | 0.174 | 0.238 |

---
## 2026-07-16 16:23 train/test 分列复核（新指标口径：逐步不合并，重点t+2）

6 个 checkpoint 各自的训练集密度不同（sparse=2Hz / dense=~30Hz原生起点），见脚本 RUN_DENSITY，train 指标用各自实际训练时用的那份数据算。测试集统一是 sparse 版 chat 3 个bag各自最后10%。exp4 不在训练集上评估（训练数据边界不同）。

### ablation1_sparse_oldlr (train density: sparse)

#### TRAIN[sparse]（5760 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| sixth-run (ablation1_sparse_oldlr) | 5.62 | 7.03 | 7.58 | 8.07 | 7.07 |
| baseline (repeat-last) | 5.86 | 7.45 | 8.15 | 8.51 | 7.49 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| sixth-run (ablation1_sparse_oldlr) | 77.24% | 70.80% | 68.07% | 66.22% | 70.58% |
| baseline (repeat-last) | 75.80% | 68.32% | 65.05% | 63.52% | 68.17% |

#### TEST（608 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.37 | 6.32 | 6.64 | 6.14 |
| sixth-run (ablation1_sparse_oldlr) | 5.61 | 6.40 | 6.83 | 7.30 | 6.53 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 79.61% | 73.85% | 73.68% | 72.37% | 74.88% |
| sixth-run (ablation1_sparse_oldlr) | 78.45% | 74.18% | 71.55% | 68.75% | 73.23% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### ablation1_dense_oldlr (train density: dense)

#### TRAIN[dense]（等距抽样自 87559 个窗口）（29184 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| sixth-run (ablation1_dense_oldlr) | 5.64 | 7.01 | 7.61 | 7.99 | 7.06 |
| baseline (repeat-last) | 5.93 | 7.55 | 8.27 | 8.64 | 7.60 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| sixth-run (ablation1_dense_oldlr) | 77.11% | 70.48% | 67.77% | 66.10% | 70.37% |
| baseline (repeat-last) | 75.37% | 67.80% | 64.41% | 62.77% | 67.59% |

#### TEST（608 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.37 | 6.32 | 6.64 | 6.14 |
| sixth-run (ablation1_dense_oldlr) | 5.52 | 6.32 | 6.78 | 7.11 | 6.43 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 79.61% | 73.85% | 73.68% | 72.37% | 74.88% |
| sixth-run (ablation1_dense_oldlr) | 78.78% | 74.34% | 71.55% | 69.57% | 73.56% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### ablation2_dense_newlr (train density: dense)

#### TRAIN[dense]（等距抽样自 87559 个窗口）（29184 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| sixth-run (ablation2_dense_newlr) | 5.30 | 6.42 | 6.81 | 7.21 | 6.43 |
| baseline (repeat-last) | 5.93 | 7.55 | 8.27 | 8.64 | 7.60 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| sixth-run (ablation2_dense_newlr) | 78.31% | 72.93% | 71.07% | 69.28% | 72.90% |
| baseline (repeat-last) | 75.37% | 67.80% | 64.41% | 62.77% | 67.59% |

#### TEST（608 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.37 | 6.32 | 6.64 | 6.14 |
| sixth-run (ablation2_dense_newlr) | 5.28 | 6.30 | 6.62 | 7.15 | 6.34 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 79.61% | 73.85% | 73.68% | 72.37% | 74.88% |
| sixth-run (ablation2_dense_newlr) | 79.11% | 74.01% | 71.71% | 69.90% | 73.68% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### ablation3_dense_newlr_aug (train density: dense)

#### TRAIN[dense]（等距抽样自 87559 个窗口）（29184 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| sixth-run (ablation3_dense_newlr_aug) | 5.45 | 6.74 | 7.24 | 7.64 | 6.77 |
| baseline (repeat-last) | 5.93 | 7.55 | 8.27 | 8.64 | 7.60 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| sixth-run (ablation3_dense_newlr_aug) | 77.68% | 71.58% | 69.22% | 67.26% | 71.43% |
| baseline (repeat-last) | 75.37% | 67.80% | 64.41% | 62.77% | 67.59% |

#### TEST（608 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.37 | 6.32 | 6.64 | 6.14 |
| sixth-run (ablation3_dense_newlr_aug) | 5.28 | 6.11 | 6.36 | 6.59 | 6.08 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 79.61% | 73.85% | 73.68% | 72.37% | 74.88% |
| sixth-run (ablation3_dense_newlr_aug) | 79.28% | 74.67% | 73.52% | 72.37% | 74.96% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### extra_sparse_newlr_aug (train density: sparse)

#### TRAIN[sparse]（5760 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| sixth-run (extra_sparse_newlr_aug) | 5.30 | 6.56 | 7.11 | 7.50 | 6.61 |
| baseline (repeat-last) | 5.86 | 7.45 | 8.15 | 8.51 | 7.49 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| sixth-run (extra_sparse_newlr_aug) | 78.30% | 72.24% | 69.93% | 68.02% | 72.12% |
| baseline (repeat-last) | 75.80% | 68.32% | 65.05% | 63.52% | 68.17% |

#### TEST（608 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.37 | 6.32 | 6.64 | 6.14 |
| sixth-run (extra_sparse_newlr_aug) | 5.32 | 6.22 | 6.42 | 6.59 | 6.14 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 79.61% | 73.85% | 73.68% | 72.37% | 74.88% |
| sixth-run (extra_sparse_newlr_aug) | 79.11% | 74.67% | 73.85% | 71.88% | 74.88% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |

### extra_sparse_newlr_noiseratio (train density: sparse)

#### TRAIN[sparse]（5760 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| sixth-run (extra_sparse_newlr_noiseratio) | 5.26 | 6.56 | 7.06 | 7.53 | 6.61 |
| baseline (repeat-last) | 5.86 | 7.45 | 8.15 | 8.51 | 7.49 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| sixth-run (extra_sparse_newlr_noiseratio) | 78.42% | 72.38% | 70.07% | 68.07% | 72.24% |
| baseline (repeat-last) | 75.80% | 68.32% | 65.05% | 63.52% | 68.17% |

#### TEST（608 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 5.24 | 6.37 | 6.32 | 6.64 | 6.14 |
| sixth-run (extra_sparse_newlr_noiseratio) | 5.38 | 6.14 | 6.20 | 6.72 | 6.11 |
| baseline (repeat-last) | 5.70 | 7.09 | 7.69 | 8.07 | 7.14 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 79.61% | 73.85% | 73.68% | 72.37% | 74.88% |
| sixth-run (extra_sparse_newlr_noiseratio) | 78.62% | 75.00% | 74.01% | 72.04% | 74.92% |
| baseline (repeat-last) | 77.96% | 71.05% | 68.59% | 66.28% | 70.97% |


---
