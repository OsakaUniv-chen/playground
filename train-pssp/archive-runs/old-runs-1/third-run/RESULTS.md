# third-run 结果报告

third-run 的单一变量是数据规模：训练集从 first-run/second-run 的 16 个 WordWolfExp bag
（G1/G3/G4）扩到全部 78 个 WordWolfExp bag + chat 的前两段辩论（chat_debate_exp1_topic1/2），
测试集换成 chat 第三段辩论（chat_debate_exp1_topic3），不再是 WordWolfExp 内部 holdout。
loss 函数固定用 first-run 的方案（plain MSE），不重新引入 second-run 已经排除掉的变量。
旧模型（access-model exp4）在同一个新测试集上重新跑一遍，不用它原来的历史数字，保证
公平对比。脚本每次运行会在下面追加一节，不覆盖历史记录。

## 评价指标说明

- **peak_dist (k=1)**：预测声图和真值声图各自找最大值位置，算欧氏距离（64x64 网格
  的格子数）。数值越小越好。
- **PSR_k@n**：k x k 局部均值滤波后再算 peak_dist，看这个距离小于阈值 n 的样本占多少
  比例——是**成功率**，不是平均距离。数值越大越好。这里统一用 k=5, n=5（PSR_k5@5）。
- **位置相关性（Pearson r）**：预测峰值位置（行/列）和真值峰值位置的皮尔逊相关系数
  （行、列分别算完取平均）。这是 third-run 真正想推动的指标——first/second-run 的
  诊断发现所有模型都低于"直接用输入最后一帧的位置预测下一帧位置"这个朴素连续性
  基线的相关性（也就是说模型根本没学会跟着输入变化去跟踪位置，不只是跟踪得不准）。
  这里同时报告朴素连续性基线本身的相关性作为参照天花板。
- **baseline（重复最后一帧）**：把输入历史的最后一帧原样当作未来 4 帧的预测，不用
  模型。任何真正学到时序动态的模型都应该明显超过这个基线。

---
## 2026-07-10 22:17 对比运行

测试集：`['chat_debate_exp1_topic3']`（2112 个窗口）。训练集：80 个 bag（78 WordWolfExp + chat_topic1/2）。checkpoint：旧 = `access-model/weights/config_simvp_exp4.pt`，新 = `train/runs/baseline/best_model.pt`。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 6.75 | 8.59 | 9.07 | 9.49 | 8.48 |
| new (third-run) | 7.12 | 9.12 | 9.66 | 10.22 | 9.03 |
| baseline (repeat-last) | 7.11 | 9.27 | 10.06 | 10.53 | 9.24 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 71.97% | 63.16% | 60.89% | 58.81% | 63.71% |
| new (third-run) | 71.92% | 62.78% | 59.56% | 57.20% | 62.87% |
| baseline (repeat-last) | 70.31% | 60.46% | 56.68% | 54.69% | 60.54% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.280 | 0.177 | 0.141 | 0.124 | 0.181 |
| new (third-run) | 0.244 | 0.142 | 0.110 | 0.084 | 0.145 |
| baseline (repeat-last) | 0.282 | 0.146 | 0.113 | 0.120 | 0.165 |
| naive continuity (last-input pos) | 0.282 | 0.146 | 0.113 | 0.120 | 0.165 |

**注意**：旧模型是在这个新测试集（chat_debate_exp1_topic3）上重新评的，不是它原来 WordWolfExp holdout 上的历史数字，两者不能直接比较。

---
## 2026-07-11 03:47 对比运行

测试集：`['chat_debate_exp1_topic3']`（2112 个窗口）。训练集：80 个 bag（78 WordWolfExp + chat_topic1/2）。checkpoint：旧 = `access-model/weights/config_simvp_exp4.pt`，新 = `train/runs/baseline/best_model.pt`。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 6.75 | 8.60 | 9.06 | 9.49 | 8.48 |
| new (third-run) | 6.98 | 9.26 | 10.01 | 10.41 | 9.17 |
| baseline (repeat-last) | 7.11 | 9.27 | 10.06 | 10.53 | 9.24 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 71.92% | 63.16% | 60.84% | 58.81% | 63.68% |
| new (third-run) | 71.45% | 61.74% | 57.77% | 55.97% | 61.73% |
| baseline (repeat-last) | 70.31% | 60.46% | 56.68% | 54.69% | 60.54% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.280 | 0.177 | 0.142 | 0.124 | 0.181 |
| new (third-run) | 0.250 | 0.161 | 0.129 | 0.103 | 0.161 |
| baseline (repeat-last) | 0.282 | 0.146 | 0.113 | 0.120 | 0.165 |
| naive continuity (last-input pos) | 0.282 | 0.146 | 0.113 | 0.120 | 0.165 |

**注意**：旧模型是在这个新测试集（chat_debate_exp1_topic3）上重新评的，不是它原来 WordWolfExp holdout 上的历史数字，两者不能直接比较。

---
## 2026-07-11 12:23 对比运行

测试集：`['chat_debate_exp1_topic3']`（2112 个窗口）。训练集：80 个 bag（78 WordWolfExp + chat_topic1/2）。checkpoint：旧 = `access-model/weights/config_simvp_exp4.pt`，新 = `train/runs/lr1e-6/best_model.pt`。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 6.75 | 8.59 | 9.07 | 9.49 | 8.48 |
| new (lr1e-6) | 7.06 | 9.09 | 9.73 | 10.40 | 9.07 |
| baseline (repeat-last) | 7.11 | 9.27 | 10.06 | 10.53 | 9.24 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 71.97% | 63.16% | 60.89% | 58.81% | 63.71% |
| new (lr1e-6) | 71.40% | 62.55% | 58.57% | 56.25% | 62.19% |
| baseline (repeat-last) | 70.31% | 60.46% | 56.68% | 54.69% | 60.54% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.280 | 0.177 | 0.141 | 0.124 | 0.181 |
| new (lr1e-6) | 0.240 | 0.170 | 0.111 | 0.089 | 0.153 |
| baseline (repeat-last) | 0.282 | 0.146 | 0.113 | 0.120 | 0.165 |
| naive continuity (last-input pos) | 0.282 | 0.146 | 0.113 | 0.120 | 0.165 |

**注意**：旧模型是在这个新测试集（chat_debate_exp1_topic3）上重新评的，不是它原来 WordWolfExp holdout 上的历史数字，两者不能直接比较。

---
## 2026-07-11 13:20 对比运行

测试集：`['chat_debate_exp1_topic3']`（2112 个窗口）。训练集：80 个 bag（78 WordWolfExp + chat_topic1/2）。checkpoint：旧 = `access-model/weights/config_simvp_exp4.pt`，新 = `train/runs/lr1e-6/best_model.pt`。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 6.75 | 8.60 | 9.06 | 9.49 | 8.48 |
| new (lr1e-6) | 7.04 | 9.07 | 9.69 | 10.30 | 9.02 |
| baseline (repeat-last) | 7.11 | 9.27 | 10.06 | 10.53 | 9.24 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 71.92% | 63.16% | 60.84% | 58.81% | 63.68% |
| new (lr1e-6) | 71.50% | 62.45% | 58.85% | 56.77% | 62.39% |
| baseline (repeat-last) | 70.31% | 60.46% | 56.68% | 54.69% | 60.54% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.280 | 0.177 | 0.142 | 0.124 | 0.181 |
| new (lr1e-6) | 0.242 | 0.165 | 0.101 | 0.092 | 0.150 |
| baseline (repeat-last) | 0.282 | 0.146 | 0.113 | 0.120 | 0.165 |
| naive continuity (last-input pos) | 0.282 | 0.146 | 0.113 | 0.120 | 0.165 |

**注意**：旧模型是在这个新测试集（chat_debate_exp1_topic3）上重新评的，不是它原来 WordWolfExp holdout 上的历史数字，两者不能直接比较。

---
## 2026-07-11 14:53 对比运行

测试集：`['chat_debate_exp1_topic2']`（2144 个窗口）。训练集：80 个 bag（78 WordWolfExp + chat_topic1/2）。checkpoint：旧 = `access-model/weights/config_simvp_exp4.pt`，新 = `train/runs/lr1e-6/best_model.pt`。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 3.96 | 4.59 | 4.99 | 5.39 | 4.73 |
| new (lr1e-6) | 3.98 | 4.69 | 5.08 | 5.52 | 4.82 |
| baseline (repeat-last) | 4.54 | 5.42 | 5.90 | 6.28 | 5.53 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 84.65% | 81.67% | 79.80% | 77.85% | 80.99% |
| new (lr1e-6) | 84.98% | 81.30% | 79.34% | 77.52% | 80.78% |
| baseline (repeat-last) | 82.28% | 78.03% | 76.07% | 74.21% | 77.65% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.481 | 0.423 | 0.406 | 0.388 | 0.424 |
| new (lr1e-6) | 0.478 | 0.421 | 0.389 | 0.381 | 0.417 |
| baseline (repeat-last) | 0.440 | 0.333 | 0.316 | 0.295 | 0.346 |
| naive continuity (last-input pos) | 0.440 | 0.333 | 0.316 | 0.295 | 0.346 |

**注意**：旧模型是在这个测试集上重新评的，不是它原来 WordWolfExp holdout 上的历史数字，两者不能直接比较。

**这次测试集（`['chat_debate_exp1_topic2']`）在 new 模型的 TRAIN_BAGS 里，是训练集内表现的诊断性对比，不是真正的 held-out 评估**，用来看模型在见过的场景类型上到底能不能学会跟踪位置——如果这里位置相关性依然很低，说明问题不在「没见过chat这个具体场景」，而在模型本身或训练配比更深层的地方。

---
## 2026-07-11 14:57 对比运行

测试集：`['G6_game5_PSSP']`（352 个窗口）。训练集：80 个 bag（78 WordWolfExp + chat_topic1/2）。checkpoint：旧 = `access-model/weights/config_simvp_exp4.pt`，新 = `train/runs/lr1e-6/best_model.pt`。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 10.97 | 14.47 | 15.63 | 16.81 | 14.47 |
| new (lr1e-6) | 11.31 | 12.09 | 12.04 | 12.18 | 11.91 |
| baseline (repeat-last) | 11.51 | 14.41 | 15.93 | 16.79 | 14.66 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 62.50% | 49.72% | 45.45% | 41.19% | 49.72% |
| new (lr1e-6) | 61.93% | 58.52% | 59.09% | 59.09% | 59.66% |
| baseline (repeat-last) | 60.51% | 48.86% | 42.61% | 39.49% | 47.87% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.317 | 0.095 | 0.016 | -0.071 | 0.089 |
| new (lr1e-6) | 0.229 | nan | -0.067 | -0.104 | nan |
| baseline (repeat-last) | 0.296 | 0.104 | 0.037 | -0.034 | 0.101 |
| naive continuity (last-input pos) | 0.296 | 0.104 | 0.037 | -0.034 | 0.101 |

**注意**：旧模型是在这个测试集上重新评的，不是它原来 WordWolfExp holdout 上的历史数字，两者不能直接比较。

**这次测试集（`['G6_game5_PSSP']`）在 new 模型的 TRAIN_BAGS 里，是训练集内表现的诊断性对比，不是真正的 held-out 评估**，用来看模型在见过的场景类型上到底能不能学会跟踪位置——如果这里位置相关性依然很低，说明问题不在「没见过chat这个具体场景」，而在模型本身或训练配比更深层的地方。

---
## 2026-07-11 15:04 对比运行

测试集：`['GRP_meeting_2025-01-16-13_56_44']`（5536 个窗口）。训练集：80 个 bag（78 WordWolfExp + chat_topic1/2）。checkpoint：旧 = `access-model/weights/config_simvp_exp4.pt`，新 = `train/runs/lr1e-6/best_model.pt`。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 6.43 | 7.36 | 7.62 | 7.78 | 7.30 |
| new (lr1e-6) | 6.62 | 8.19 | 8.72 | 9.38 | 8.23 |
| baseline (repeat-last) | 6.49 | 7.87 | 8.58 | 9.04 | 8.00 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 66.09% | 61.11% | 58.92% | 58.56% | 61.17% |
| new (lr1e-6) | 64.20% | 55.33% | 50.13% | 47.63% | 54.32% |
| baseline (repeat-last) | 62.45% | 53.43% | 49.22% | 46.44% | 52.89% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.521 | 0.429 | 0.400 | 0.376 | 0.432 |
| new (lr1e-6) | 0.511 | 0.392 | 0.326 | 0.277 | 0.376 |
| baseline (repeat-last) | 0.512 | 0.390 | 0.323 | 0.275 | 0.375 |
| naive continuity (last-input pos) | 0.512 | 0.390 | 0.323 | 0.275 | 0.375 |

**注意**：旧模型是在这个测试集上重新评的，不是它原来 WordWolfExp holdout 上的历史数字，两者不能直接比较。

**这次测试集（`['GRP_meeting_2025-01-16-13_56_44']`）在 new 模型的 TRAIN_BAGS 里，是训练集内表现的诊断性对比，不是真正的 held-out 评估**，用来看模型在见过的场景类型上到底能不能学会跟踪位置——如果这里位置相关性依然很低，说明问题不在「没见过chat这个具体场景」，而在模型本身或训练配比更深层的地方。

---
## 2026-07-11 15:04 对比运行

测试集：`['olab_0630_2025-06-30-15_17_22']`（1184 个窗口）。训练集：80 个 bag（78 WordWolfExp + chat_topic1/2）。checkpoint：旧 = `access-model/weights/config_simvp_exp4.pt`，新 = `train/runs/lr1e-6/best_model.pt`。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 8.00 | 7.86 | 7.99 | 8.11 | 7.99 |
| new (lr1e-6) | 9.07 | 9.00 | 8.73 | 9.42 | 9.05 |
| baseline (repeat-last) | 9.66 | 10.28 | 10.57 | 10.88 | 10.35 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 60.30% | 58.87% | 56.59% | 55.15% | 57.73% |
| new (lr1e-6) | 56.67% | 54.81% | 56.76% | 51.52% | 54.94% |
| baseline (repeat-last) | 50.34% | 47.21% | 46.28% | 43.83% | 46.92% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.296 | 0.313 | 0.299 | 0.298 | 0.301 |
| new (lr1e-6) | 0.256 | 0.266 | 0.289 | 0.245 | 0.264 |
| baseline (repeat-last) | 0.201 | 0.172 | 0.161 | 0.133 | 0.167 |
| naive continuity (last-input pos) | 0.201 | 0.172 | 0.161 | 0.133 | 0.167 |

**注意**：旧模型是在这个测试集上重新评的，不是它原来 WordWolfExp holdout 上的历史数字，两者不能直接比较。

**这次测试集（`['olab_0630_2025-06-30-15_17_22']`）在 new 模型的 TRAIN_BAGS 里，是训练集内表现的诊断性对比，不是真正的 held-out 评估**，用来看模型在见过的场景类型上到底能不能学会跟踪位置——如果这里位置相关性依然很低，说明问题不在「没见过chat这个具体场景」，而在模型本身或训练配比更深层的地方。

---
## 2026-07-11 15:04 对比运行

测试集：`['Experiment0312_EXP3_PSSP']`（352 个窗口）。训练集：80 个 bag（78 WordWolfExp + chat_topic1/2）。checkpoint：旧 = `access-model/weights/config_simvp_exp4.pt`，新 = `train/runs/lr1e-6/best_model.pt`。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 10.30 | 13.07 | 13.73 | 13.07 | 12.54 |
| new (lr1e-6) | 10.41 | 12.42 | 12.60 | 12.87 | 12.08 |
| baseline (repeat-last) | 10.05 | 12.35 | 13.78 | 14.52 | 12.68 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 67.33% | 56.82% | 55.68% | 56.53% | 59.09% |
| new (lr1e-6) | 67.33% | 59.09% | 57.95% | 59.09% | 60.87% |
| baseline (repeat-last) | 67.33% | 58.52% | 53.98% | 51.70% | 57.88% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.403 | 0.185 | 0.093 | 0.143 | 0.206 |
| new (lr1e-6) | 0.371 | 0.225 | 0.157 | 0.113 | 0.217 |
| baseline (repeat-last) | 0.410 | 0.286 | 0.182 | 0.139 | 0.254 |
| naive continuity (last-input pos) | 0.410 | 0.286 | 0.182 | 0.139 | 0.254 |

**注意**：旧模型是在这个测试集上重新评的，不是它原来 WordWolfExp holdout 上的历史数字，两者不能直接比较。

**这次测试集（`['Experiment0312_EXP3_PSSP']`）在 new 模型的 TRAIN_BAGS 里，是训练集内表现的诊断性对比，不是真正的 held-out 评估**，用来看模型在见过的场景类型上到底能不能学会跟踪位置——如果这里位置相关性依然很低，说明问题不在「没见过chat这个具体场景」，而在模型本身或训练配比更深层的地方。

---
## 2026-07-16 15:30 train/test 分列复核（新指标口径：逐步不合并，重点t+2）

训练集：`TRAIN_BAGS`（80 个 bag = 78 WordWolfExp + chat_topic1/2）。测试集：`TEST_BAGS`（chat_debate_exp1_topic3，未裁剪，第7轮起才有的"只用后10%"修正这里还没做，如实复现third-run当年的测试集）。exp4 不在训练集上评估（训练数据边界不同，无过拟合诊断意义）。

#### TRAIN（42368 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| third-run baseline | 9.35 | 10.41 | 10.60 | 10.76 | 10.28 |
| third-run lr1e-6 | 9.23 | 10.38 | 10.62 | 10.87 | 10.27 |
| baseline (repeat-last) | 9.80 | 11.52 | 12.29 | 12.75 | 11.59 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| third-run baseline | 67.99% | 64.02% | 63.30% | 62.73% | 64.51% |
| third-run lr1e-6 | 68.23% | 64.14% | 63.25% | 62.67% | 64.57% |
| baseline (repeat-last) | 65.60% | 58.90% | 55.88% | 54.05% | 58.61% |

#### TEST（2112 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 6.75 | 8.60 | 9.06 | 9.49 | 8.48 |
| third-run baseline | 6.98 | 9.26 | 10.01 | 10.41 | 9.17 |
| third-run lr1e-6 | 7.04 | 9.07 | 9.69 | 10.30 | 9.02 |
| baseline (repeat-last) | 7.11 | 9.27 | 10.06 | 10.53 | 9.24 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 71.92% | 63.16% | 60.84% | 58.81% | 63.68% |
| third-run baseline | 71.45% | 61.74% | 57.77% | 55.97% | 61.73% |
| third-run lr1e-6 | 71.50% | 62.45% | 58.85% | 56.77% | 62.39% |
| baseline (repeat-last) | 70.31% | 60.46% | 56.68% | 54.69% | 60.54% |


---
