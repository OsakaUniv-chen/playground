# seventh-run 结果报告

seventh-run 把 sixth-run（只用 chat 3 个bag做的小规模消融）得出的结论——
**sparse(2Hz) + lr=1e-3 + 数据增强（噪声+sm_ratio抖动，`noise_ratio`）** 追平/超过
旧模型 exp4，dense 密集滑窗不是必需的——搬到 fifth-run 的全量数据集上验证能不能
推广。训练集和 fifth-run 完全一样：278 个 bag，15+ 个来源（WordWolfExp 含合并进去
的 EXP1-3/testrun_0420、GRP_meeting、olab_0630/rev、ATR_teleoperation 两个 RIKEN
集合、chat、Demonstration_Data(+nonconv)、demo_data_0318_becap、egoSAS_test_data、
kitchen，见 CONTEXT.md/DATA_REPORT.md）。测试集也和 fifth-run 完全一样：
chat_debate_exp1_topic3（只用最后10%，见 dataset.py 的 `TEST_BAG_MIN_START_FRAC`）+
G13 的 game3/4/5/6 四局——**G13_game2_Video 和 G13_interview 仍然留在训练集里，
这是负责人明确要求的选择，会有同组信息泄漏风险，不是无意的疏漏**，dataset.py 的
`KNOWN_GROUP_OVERLAP` 显式记录了这个例外。单一变量是 lr（1e-6→1e-3，虽然一直都是
1e-3，这里指相对旧模型真实lr的差异）+ 数据增强（sixth-run 新加的，fifth-run 没有），
SimVP 架构/loss 等其它超参数不变。旧模型（access-model exp4）在同一个测试集上重新
跑一遍，不用它原来的历史数字，保证公平对比。脚本每次运行会在下面追加一节，不覆盖
历史记录。

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
## 2026-07-14 23:35 对比运行

训练集：278 个 bag（2026-07-13 PSSPData 重跑后的全部集合，见 dataset.py/DATA_REPORT.md）。checkpoint：旧 = `access-model/weights/config_simvp_exp4.pt`，新 = `train/runs/baseline/best_model.pt`。

### chat

测试集：`['chat_debate_exp1_topic3']`（192 个窗口）。

#### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 6.57 | 7.80 | 7.70 | 7.85 | 7.48 |
| new (baseline) | 7.10 | 8.65 | 8.58 | 8.90 | 8.31 |
| baseline (repeat-last) | 6.81 | 9.03 | 9.29 | 9.56 | 8.68 |

#### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 72.92% | 67.19% | 66.67% | 66.67% | 68.36% |
| new (baseline) | 70.31% | 64.06% | 62.50% | 60.94% | 64.45% |
| baseline (repeat-last) | 72.40% | 61.98% | 60.94% | 58.85% | 63.54% |

#### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.305 | 0.164 | 0.137 | 0.158 | 0.191 |
| new (baseline) | 0.203 | 0.111 | 0.122 | 0.117 | 0.138 |
| baseline (repeat-last) | 0.414 | 0.159 | 0.107 | 0.092 | 0.193 |
| naive continuity (last-input pos) | 0.414 | 0.159 | 0.107 | 0.092 | 0.193 |

### wordwolfexp (G13)

测试集：`['G13_game3_DoA', 'G13_game4_Random', 'G13_game5_PSSP', 'G13_game6_Tele']`（1440 个窗口）。

#### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 11.00 | 13.19 | 13.45 | 13.90 | 12.88 |
| new (baseline) | 10.97 | 13.17 | 13.41 | 13.84 | 12.85 |
| baseline (repeat-last) | 11.20 | 13.49 | 13.57 | 13.99 | 13.06 |

#### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 62.22% | 54.03% | 53.54% | 51.81% | 55.40% |
| new (baseline) | 62.22% | 54.44% | 53.68% | 52.36% | 55.68% |
| baseline (repeat-last) | 60.07% | 51.46% | 50.49% | 48.75% | 52.69% |

#### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.386 | 0.251 | 0.208 | 0.176 | 0.255 |
| new (baseline) | 0.357 | 0.172 | 0.149 | 0.100 | 0.195 |
| baseline (repeat-last) | 0.370 | 0.222 | 0.210 | 0.191 | 0.248 |
| naive continuity (last-input pos) | 0.370 | 0.222 | 0.210 | 0.191 | 0.248 |

### combined

测试集：`['chat_debate_exp1_topic3', 'G13_game3_DoA', 'G13_game4_Random', 'G13_game5_PSSP', 'G13_game6_Tele']`（1664 个窗口）。

#### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 10.39 | 12.50 | 12.77 | 13.14 | 12.20 |
| new (baseline) | 10.43 | 12.60 | 12.83 | 13.29 | 12.29 |
| baseline (repeat-last) | 10.59 | 12.89 | 13.02 | 13.41 | 12.48 |

#### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 63.82% | 55.71% | 54.99% | 53.61% | 57.03% |
| new (baseline) | 63.46% | 55.65% | 54.75% | 53.25% | 56.78% |
| baseline (repeat-last) | 61.90% | 52.94% | 51.86% | 50.12% | 54.21% |

#### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.427 | 0.299 | 0.266 | 0.245 | 0.309 |
| new (baseline) | 0.403 | 0.247 | 0.240 | 0.207 | 0.274 |
| baseline (repeat-last) | 0.413 | 0.263 | 0.251 | 0.233 | 0.290 |
| naive continuity (last-input pos) | 0.413 | 0.263 | 0.251 | 0.233 | 0.290 |

**注意**：旧模型是在这个测试集上重新评的，不是它原来 WordWolfExp holdout 上的历史数字，两者不能直接比较。`chat_debate_exp1_topic3` 只用了每个 bag 时间轴上的最后 10%（`TEST_BAG_MIN_START_FRAC`）——旧模型训练代码（`access-model-train/utils_all_load.py`）确认过对每个 bag 按 `train_ratio=0.9` 做时间切分，`exp_name=[]` 意味着它当时用了 npz_path 下的全部数据，不确定 chat 数据当时在不在那个目录里；用最后 10% 可以保证这部分数据无论如何都不是旧模型训练时见过的。G13 的 4 个测试 bag 确认双方模型都没用来训练，按整段使用。


---
## 2026-07-15 09:25 对比运行

训练集：278 个 bag（2026-07-13 PSSPData 重跑后的全部集合，见 dataset.py/DATA_REPORT.md）。checkpoint：旧 = `access-model/weights/config_simvp_exp4.pt`，新 = `train/runs/wordwolf_chat_lr1e-3/best_model.pt`。

### chat

测试集：`['chat_debate_exp1_topic3']`（192 个窗口）。

#### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 6.57 | 7.80 | 7.70 | 7.85 | 7.48 |
| new (wordwolf_chat_lr1e-3) | 7.32 | 9.00 | 8.92 | 9.40 | 8.66 |
| baseline (repeat-last) | 6.81 | 9.03 | 9.29 | 9.56 | 8.68 |

#### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 72.92% | 67.19% | 66.67% | 66.67% | 68.36% |
| new (wordwolf_chat_lr1e-3) | 71.35% | 64.06% | 63.02% | 59.90% | 64.58% |
| baseline (repeat-last) | 72.40% | 61.98% | 60.94% | 58.85% | 63.54% |

#### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.305 | 0.164 | 0.137 | 0.158 | 0.191 |
| new (wordwolf_chat_lr1e-3) | 0.073 | 0.115 | 0.130 | 0.099 | 0.104 |
| baseline (repeat-last) | 0.414 | 0.159 | 0.107 | 0.092 | 0.193 |
| naive continuity (last-input pos) | 0.414 | 0.159 | 0.107 | 0.092 | 0.193 |

### wordwolfexp (G13)

测试集：`['G13_game3_DoA', 'G13_game4_Random', 'G13_game5_PSSP', 'G13_game6_Tele']`（1440 个窗口）。

#### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 11.00 | 13.19 | 13.45 | 13.90 | 12.88 |
| new (wordwolf_chat_lr1e-3) | 10.74 | 13.35 | 13.64 | 13.98 | 12.93 |
| baseline (repeat-last) | 11.20 | 13.49 | 13.57 | 13.99 | 13.06 |

#### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 62.22% | 54.03% | 53.54% | 51.81% | 55.40% |
| new (wordwolf_chat_lr1e-3) | 62.85% | 54.37% | 53.54% | 51.60% | 55.59% |
| baseline (repeat-last) | 60.07% | 51.46% | 50.49% | 48.75% | 52.69% |

#### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.386 | 0.251 | 0.208 | 0.176 | 0.255 |
| new (wordwolf_chat_lr1e-3) | 0.378 | 0.157 | 0.131 | 0.064 | 0.183 |
| baseline (repeat-last) | 0.370 | 0.222 | 0.210 | 0.191 | 0.248 |
| naive continuity (last-input pos) | 0.370 | 0.222 | 0.210 | 0.191 | 0.248 |

### combined

测试集：`['chat_debate_exp1_topic3', 'G13_game3_DoA', 'G13_game4_Random', 'G13_game5_PSSP', 'G13_game6_Tele']`（1664 个窗口）。

#### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 10.39 | 12.50 | 12.77 | 13.14 | 12.20 |
| new (wordwolf_chat_lr1e-3) | 10.25 | 12.79 | 13.10 | 13.54 | 12.42 |
| baseline (repeat-last) | 10.59 | 12.89 | 13.02 | 13.41 | 12.48 |

#### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 63.82% | 55.71% | 54.99% | 53.61% | 57.03% |
| new (wordwolf_chat_lr1e-3) | 64.18% | 55.59% | 54.69% | 52.40% | 56.72% |
| baseline (repeat-last) | 61.90% | 52.94% | 51.86% | 50.12% | 54.21% |

#### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.427 | 0.299 | 0.266 | 0.245 | 0.309 |
| new (wordwolf_chat_lr1e-3) | 0.421 | 0.243 | 0.235 | 0.195 | 0.274 |
| baseline (repeat-last) | 0.413 | 0.263 | 0.251 | 0.233 | 0.290 |
| naive continuity (last-input pos) | 0.413 | 0.263 | 0.251 | 0.233 | 0.290 |

**注意**：旧模型是在这个测试集上重新评的，不是它原来 WordWolfExp holdout 上的历史数字，两者不能直接比较。`chat_debate_exp1_topic3` 只用了每个 bag 时间轴上的最后 10%（`TEST_BAG_MIN_START_FRAC`）——旧模型训练代码（`access-model-train/utils_all_load.py`）确认过对每个 bag 按 `train_ratio=0.9` 做时间切分，`exp_name=[]` 意味着它当时用了 npz_path 下的全部数据，不确定 chat 数据当时在不在那个目录里；用最后 10% 可以保证这部分数据无论如何都不是旧模型训练时见过的。G13 的 4 个测试 bag 确认双方模型都没用来训练，按整段使用。


---
## 2026-07-15 11:15 对比运行

训练集：278 个 bag（2026-07-13 PSSPData 重跑后的全部集合，见 dataset.py/DATA_REPORT.md）。checkpoint：旧 = `access-model/weights/config_simvp_exp4.pt`，新 = `train/runs/wordwolf_chat_lr1e-4/best_model.pt`。

### chat

测试集：`['chat_debate_exp1_topic3']`（192 个窗口）。

#### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 6.56 | 7.81 | 7.70 | 7.85 | 7.48 |
| new (wordwolf_chat_lr1e-4) | 6.72 | 9.08 | 9.03 | 9.38 | 8.55 |
| baseline (repeat-last) | 6.81 | 9.03 | 9.29 | 9.56 | 8.68 |

#### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 72.92% | 67.19% | 66.67% | 66.67% | 68.36% |
| new (wordwolf_chat_lr1e-4) | 72.40% | 61.46% | 60.94% | 59.38% | 63.54% |
| baseline (repeat-last) | 72.40% | 61.98% | 60.94% | 58.85% | 63.54% |

#### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.305 | 0.163 | 0.137 | 0.158 | 0.191 |
| new (wordwolf_chat_lr1e-4) | 0.211 | 0.105 | 0.115 | 0.095 | 0.132 |
| baseline (repeat-last) | 0.414 | 0.159 | 0.107 | 0.092 | 0.193 |
| naive continuity (last-input pos) | 0.414 | 0.159 | 0.107 | 0.092 | 0.193 |

### wordwolfexp (G13)

测试集：`['G13_game3_DoA', 'G13_game4_Random', 'G13_game5_PSSP', 'G13_game6_Tele']`（1440 个窗口）。

#### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 11.01 | 13.17 | 13.45 | 13.88 | 12.88 |
| new (wordwolf_chat_lr1e-4) | 10.71 | 12.96 | 13.48 | 13.93 | 12.77 |
| baseline (repeat-last) | 11.20 | 13.49 | 13.57 | 13.99 | 13.06 |

#### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 62.22% | 54.17% | 53.68% | 51.74% | 55.45% |
| new (wordwolf_chat_lr1e-4) | 62.85% | 55.76% | 53.40% | 51.53% | 55.89% |
| baseline (repeat-last) | 60.07% | 51.46% | 50.49% | 48.75% | 52.69% |

#### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.386 | 0.251 | 0.208 | 0.177 | 0.255 |
| new (wordwolf_chat_lr1e-4) | 0.387 | 0.194 | 0.128 | 0.044 | 0.188 |
| baseline (repeat-last) | 0.370 | 0.222 | 0.210 | 0.191 | 0.248 |
| naive continuity (last-input pos) | 0.370 | 0.222 | 0.210 | 0.191 | 0.248 |

### combined

测试集：`['chat_debate_exp1_topic3', 'G13_game3_DoA', 'G13_game4_Random', 'G13_game5_PSSP', 'G13_game6_Tele']`（1664 个窗口）。

#### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 10.39 | 12.49 | 12.77 | 13.13 | 12.19 |
| new (wordwolf_chat_lr1e-4) | 10.15 | 12.50 | 13.04 | 13.44 | 12.28 |
| baseline (repeat-last) | 10.59 | 12.89 | 13.02 | 13.41 | 12.48 |

#### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 63.82% | 55.83% | 55.11% | 53.55% | 57.08% |
| new (wordwolf_chat_lr1e-4) | 64.30% | 56.37% | 53.85% | 52.16% | 56.67% |
| baseline (repeat-last) | 61.90% | 52.94% | 51.86% | 50.12% | 54.21% |

#### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.427 | 0.299 | 0.267 | 0.245 | 0.310 |
| new (wordwolf_chat_lr1e-4) | 0.429 | 0.253 | 0.222 | 0.188 | 0.273 |
| baseline (repeat-last) | 0.413 | 0.263 | 0.251 | 0.233 | 0.290 |
| naive continuity (last-input pos) | 0.413 | 0.263 | 0.251 | 0.233 | 0.290 |

**注意**：旧模型是在这个测试集上重新评的，不是它原来 WordWolfExp holdout 上的历史数字，两者不能直接比较。`chat_debate_exp1_topic3` 只用了每个 bag 时间轴上的最后 10%（`TEST_BAG_MIN_START_FRAC`）——旧模型训练代码（`access-model-train/utils_all_load.py`）确认过对每个 bag 按 `train_ratio=0.9` 做时间切分，`exp_name=[]` 意味着它当时用了 npz_path 下的全部数据，不确定 chat 数据当时在不在那个目录里；用最后 10% 可以保证这部分数据无论如何都不是旧模型训练时见过的。G13 的 4 个测试 bag 确认双方模型都没用来训练，按整段使用。


---
## 2026-07-15 21:33 对比运行

训练集：278 个 bag（2026-07-13 PSSPData 重跑后的全部集合，见 dataset.py/DATA_REPORT.md）。checkpoint：旧 = `access-model/weights/config_simvp_exp4.pt`，新 = `train/runs/wordwolf_chat_lr1e-6/best_model.pt`。

### chat

测试集：`['chat_debate_exp1_topic3']`（192 个窗口）。

#### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 6.56 | 7.81 | 7.70 | 7.85 | 7.48 |
| new (wordwolf_chat_lr1e-6) | 7.58 | 8.71 | 9.09 | 9.27 | 8.66 |
| baseline (repeat-last) | 6.81 | 9.03 | 9.29 | 9.56 | 8.68 |

#### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 72.92% | 67.19% | 66.67% | 66.67% | 68.36% |
| new (wordwolf_chat_lr1e-6) | 70.83% | 64.06% | 62.50% | 60.42% | 64.45% |
| baseline (repeat-last) | 72.40% | 61.98% | 60.94% | 58.85% | 63.54% |

#### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.305 | 0.163 | 0.137 | 0.158 | 0.191 |
| new (wordwolf_chat_lr1e-6) | 0.157 | 0.118 | 0.111 | 0.108 | 0.123 |
| baseline (repeat-last) | 0.414 | 0.159 | 0.107 | 0.092 | 0.193 |
| naive continuity (last-input pos) | 0.414 | 0.159 | 0.107 | 0.092 | 0.193 |

### wordwolfexp (G13)

测试集：`['G13_game3_DoA', 'G13_game4_Random', 'G13_game5_PSSP', 'G13_game6_Tele']`（1440 个窗口）。

#### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 11.01 | 13.17 | 13.45 | 13.88 | 12.88 |
| new (wordwolf_chat_lr1e-6) | 10.85 | 13.19 | 13.73 | 13.73 | 12.88 |
| baseline (repeat-last) | 11.20 | 13.49 | 13.57 | 13.99 | 13.06 |

#### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 62.22% | 54.17% | 53.68% | 51.74% | 55.45% |
| new (wordwolf_chat_lr1e-6) | 62.99% | 54.51% | 53.06% | 51.88% | 55.61% |
| baseline (repeat-last) | 60.07% | 51.46% | 50.49% | 48.75% | 52.69% |

#### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.386 | 0.251 | 0.208 | 0.177 | 0.255 |
| new (wordwolf_chat_lr1e-6) | 0.364 | 0.164 | 0.095 | 0.103 | 0.182 |
| baseline (repeat-last) | 0.370 | 0.222 | 0.210 | 0.191 | 0.248 |
| naive continuity (last-input pos) | 0.370 | 0.222 | 0.210 | 0.191 | 0.248 |

### combined

测试集：`['chat_debate_exp1_topic3', 'G13_game3_DoA', 'G13_game4_Random', 'G13_game5_PSSP', 'G13_game6_Tele']`（1664 个窗口）。

#### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 10.39 | 12.49 | 12.77 | 13.13 | 12.19 |
| new (wordwolf_chat_lr1e-6) | 10.39 | 12.63 | 13.28 | 13.27 | 12.39 |
| baseline (repeat-last) | 10.59 | 12.89 | 13.02 | 13.41 | 12.48 |

#### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 63.82% | 55.83% | 55.11% | 53.55% | 57.08% |
| new (wordwolf_chat_lr1e-6) | 64.24% | 55.71% | 53.79% | 52.52% | 56.57% |
| baseline (repeat-last) | 61.90% | 52.94% | 51.86% | 50.12% | 54.21% |

#### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.427 | 0.299 | 0.267 | 0.245 | 0.310 |
| new (wordwolf_chat_lr1e-6) | 0.407 | 0.254 | 0.209 | 0.220 | 0.272 |
| baseline (repeat-last) | 0.413 | 0.263 | 0.251 | 0.233 | 0.290 |
| naive continuity (last-input pos) | 0.413 | 0.263 | 0.251 | 0.233 | 0.290 |

**注意**：旧模型是在这个测试集上重新评的，不是它原来 WordWolfExp holdout 上的历史数字，两者不能直接比较。`chat_debate_exp1_topic3` 只用了每个 bag 时间轴上的最后 10%（`TEST_BAG_MIN_START_FRAC`）——旧模型训练代码（`access-model-train/utils_all_load.py`）确认过对每个 bag 按 `train_ratio=0.9` 做时间切分，`exp_name=[]` 意味着它当时用了 npz_path 下的全部数据，不确定 chat 数据当时在不在那个目录里；用最后 10% 可以保证这部分数据无论如何都不是旧模型训练时见过的。G13 的 4 个测试 bag 确认双方模型都没用来训练，按整段使用。


---
## 2026-07-16 16:34 train/test 分列复核（新指标口径：逐步不合并，重点t+2）

baseline 训练集是全部278 bag，wordwolf_chat_lr* 三个变体训练集是76 bag子集（WordWolfExp+chat，见 RUN_TRAIN_BAGS）——各自的 train 指标用各自实际训练用的数据算。278-bag 训练集超过 30000 窗口时做等距抽样（stride subsample，不是随机），换取可行的评估时间，注释里会写清楚抽样比例。测试集 chat/wordwolfexp(G13)/combined 三组分开报告。exp4 不在训练集上评估。

### baseline (train scope: 278 bags)

#### TRAIN（等距抽样自 234194 个窗口）（29248 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| seventh-run (baseline) | 8.82 | 9.62 | 9.90 | 10.05 | 9.60 |
| baseline (repeat-last) | 8.87 | 10.24 | 10.91 | 11.30 | 10.33 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| seventh-run (baseline) | 66.38% | 62.69% | 61.16% | 60.05% | 62.57% |
| baseline (repeat-last) | 63.33% | 56.95% | 54.16% | 52.44% | 56.72% |

#### TEST [chat]（192 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 6.56 | 7.81 | 7.70 | 7.85 | 7.48 |
| seventh-run (baseline) | 7.10 | 8.65 | 8.58 | 8.90 | 8.31 |
| baseline (repeat-last) | 6.81 | 9.03 | 9.29 | 9.56 | 8.68 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 72.92% | 67.19% | 66.67% | 66.67% | 68.36% |
| seventh-run (baseline) | 70.31% | 64.06% | 62.50% | 60.94% | 64.45% |
| baseline (repeat-last) | 72.40% | 61.98% | 60.94% | 58.85% | 63.54% |

#### TEST [wordwolfexp (G13)]（1440 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 11.01 | 13.17 | 13.45 | 13.88 | 12.88 |
| seventh-run (baseline) | 10.97 | 13.17 | 13.41 | 13.84 | 12.85 |
| baseline (repeat-last) | 11.20 | 13.49 | 13.57 | 13.99 | 13.06 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 62.22% | 54.17% | 53.68% | 51.74% | 55.45% |
| seventh-run (baseline) | 62.22% | 54.44% | 53.68% | 52.36% | 55.68% |
| baseline (repeat-last) | 60.07% | 51.46% | 50.49% | 48.75% | 52.69% |

#### TEST [combined]（1664 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 10.39 | 12.49 | 12.77 | 13.13 | 12.19 |
| seventh-run (baseline) | 10.43 | 12.61 | 12.83 | 13.29 | 12.29 |
| baseline (repeat-last) | 10.59 | 12.89 | 13.02 | 13.41 | 12.48 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 63.82% | 55.83% | 55.11% | 53.55% | 57.08% |
| seventh-run (baseline) | 63.46% | 55.65% | 54.75% | 53.25% | 56.78% |
| baseline (repeat-last) | 61.90% | 52.94% | 51.86% | 50.12% | 54.21% |

### wordwolf_chat_lr1e-3 (train scope: 76 bags)

#### TRAIN（等距抽样自 40946 个窗口）（20416 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| seventh-run (wordwolf_chat_lr1e-3) | 9.44 | 10.45 | 10.78 | 10.74 | 10.35 |
| baseline (repeat-last) | 9.71 | 11.33 | 12.21 | 12.69 | 11.49 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| seventh-run (wordwolf_chat_lr1e-3) | 67.89% | 64.17% | 63.11% | 63.25% | 64.60% |
| baseline (repeat-last) | 66.02% | 59.61% | 56.20% | 54.47% | 59.07% |

#### TEST [chat]（192 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 6.56 | 7.81 | 7.70 | 7.85 | 7.48 |
| seventh-run (wordwolf_chat_lr1e-3) | 7.32 | 9.00 | 8.92 | 9.40 | 8.66 |
| baseline (repeat-last) | 6.81 | 9.03 | 9.29 | 9.56 | 8.68 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 72.92% | 67.19% | 66.67% | 66.67% | 68.36% |
| seventh-run (wordwolf_chat_lr1e-3) | 71.35% | 64.06% | 63.02% | 59.90% | 64.58% |
| baseline (repeat-last) | 72.40% | 61.98% | 60.94% | 58.85% | 63.54% |

#### TEST [wordwolfexp (G13)]（1440 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 11.01 | 13.17 | 13.45 | 13.88 | 12.88 |
| seventh-run (wordwolf_chat_lr1e-3) | 10.74 | 13.35 | 13.64 | 13.98 | 12.93 |
| baseline (repeat-last) | 11.20 | 13.49 | 13.57 | 13.99 | 13.06 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 62.22% | 54.17% | 53.68% | 51.74% | 55.45% |
| seventh-run (wordwolf_chat_lr1e-3) | 62.85% | 54.37% | 53.54% | 51.60% | 55.59% |
| baseline (repeat-last) | 60.07% | 51.46% | 50.49% | 48.75% | 52.69% |

#### TEST [combined]（1664 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 10.39 | 12.49 | 12.77 | 13.13 | 12.19 |
| seventh-run (wordwolf_chat_lr1e-3) | 10.25 | 12.79 | 13.10 | 13.54 | 12.42 |
| baseline (repeat-last) | 10.59 | 12.89 | 13.02 | 13.41 | 12.48 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 63.82% | 55.83% | 55.11% | 53.55% | 57.08% |
| seventh-run (wordwolf_chat_lr1e-3) | 64.18% | 55.59% | 54.69% | 52.40% | 56.72% |
| baseline (repeat-last) | 61.90% | 52.94% | 51.86% | 50.12% | 54.21% |

### wordwolf_chat_lr1e-4 (train scope: 76 bags)

#### TRAIN（等距抽样自 40946 个窗口）（20416 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| seventh-run (wordwolf_chat_lr1e-4) | 9.01 | 9.98 | 10.36 | 10.38 | 9.93 |
| baseline (repeat-last) | 9.71 | 11.33 | 12.21 | 12.69 | 11.49 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| seventh-run (wordwolf_chat_lr1e-4) | 68.68% | 64.85% | 63.89% | 63.52% | 65.23% |
| baseline (repeat-last) | 66.02% | 59.61% | 56.20% | 54.47% | 59.07% |

#### TEST [chat]（192 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 6.56 | 7.81 | 7.70 | 7.85 | 7.48 |
| seventh-run (wordwolf_chat_lr1e-4) | 6.72 | 9.08 | 9.03 | 9.38 | 8.55 |
| baseline (repeat-last) | 6.81 | 9.03 | 9.29 | 9.56 | 8.68 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 72.92% | 67.19% | 66.67% | 66.67% | 68.36% |
| seventh-run (wordwolf_chat_lr1e-4) | 72.40% | 61.46% | 60.94% | 59.38% | 63.54% |
| baseline (repeat-last) | 72.40% | 61.98% | 60.94% | 58.85% | 63.54% |

#### TEST [wordwolfexp (G13)]（1440 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 11.01 | 13.17 | 13.45 | 13.88 | 12.88 |
| seventh-run (wordwolf_chat_lr1e-4) | 10.71 | 12.96 | 13.48 | 13.93 | 12.77 |
| baseline (repeat-last) | 11.20 | 13.49 | 13.57 | 13.99 | 13.06 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 62.22% | 54.17% | 53.68% | 51.74% | 55.45% |
| seventh-run (wordwolf_chat_lr1e-4) | 62.85% | 55.76% | 53.40% | 51.53% | 55.89% |
| baseline (repeat-last) | 60.07% | 51.46% | 50.49% | 48.75% | 52.69% |

#### TEST [combined]（1664 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 10.39 | 12.49 | 12.77 | 13.13 | 12.19 |
| seventh-run (wordwolf_chat_lr1e-4) | 10.15 | 12.50 | 13.04 | 13.44 | 12.28 |
| baseline (repeat-last) | 10.59 | 12.89 | 13.02 | 13.41 | 12.48 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 63.82% | 55.83% | 55.11% | 53.55% | 57.08% |
| seventh-run (wordwolf_chat_lr1e-4) | 64.30% | 56.37% | 53.85% | 52.16% | 56.67% |
| baseline (repeat-last) | 61.90% | 52.94% | 51.86% | 50.12% | 54.21% |

### wordwolf_chat_lr1e-6 (train scope: 76 bags)

#### TRAIN（等距抽样自 40946 个窗口）（20416 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| seventh-run (wordwolf_chat_lr1e-6) | 9.30 | 10.23 | 10.64 | 10.65 | 10.21 |
| baseline (repeat-last) | 9.71 | 11.33 | 12.21 | 12.69 | 11.49 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| seventh-run (wordwolf_chat_lr1e-6) | 68.08% | 64.44% | 63.40% | 63.18% | 64.77% |
| baseline (repeat-last) | 66.02% | 59.61% | 56.20% | 54.47% | 59.07% |

#### TEST [chat]（192 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 6.56 | 7.81 | 7.70 | 7.85 | 7.48 |
| seventh-run (wordwolf_chat_lr1e-6) | 7.58 | 8.71 | 9.09 | 9.27 | 8.66 |
| baseline (repeat-last) | 6.81 | 9.03 | 9.29 | 9.56 | 8.68 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 72.92% | 67.19% | 66.67% | 66.67% | 68.36% |
| seventh-run (wordwolf_chat_lr1e-6) | 70.83% | 64.06% | 62.50% | 60.42% | 64.45% |
| baseline (repeat-last) | 72.40% | 61.98% | 60.94% | 58.85% | 63.54% |

#### TEST [wordwolfexp (G13)]（1440 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 11.01 | 13.17 | 13.45 | 13.88 | 12.88 |
| seventh-run (wordwolf_chat_lr1e-6) | 10.85 | 13.19 | 13.73 | 13.73 | 12.88 |
| baseline (repeat-last) | 11.20 | 13.49 | 13.57 | 13.99 | 13.06 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 62.22% | 54.17% | 53.68% | 51.74% | 55.45% |
| seventh-run (wordwolf_chat_lr1e-6) | 62.99% | 54.51% | 53.06% | 51.88% | 55.61% |
| baseline (repeat-last) | 60.07% | 51.46% | 50.49% | 48.75% | 52.69% |

#### TEST [combined]（1664 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 10.39 | 12.49 | 12.77 | 13.13 | 12.19 |
| seventh-run (wordwolf_chat_lr1e-6) | 10.39 | 12.63 | 13.28 | 13.27 | 12.39 |
| baseline (repeat-last) | 10.59 | 12.89 | 13.02 | 13.41 | 12.48 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 63.82% | 55.83% | 55.11% | 53.55% | 57.08% |
| seventh-run (wordwolf_chat_lr1e-6) | 64.24% | 55.71% | 53.79% | 52.52% | 56.57% |
| baseline (repeat-last) | 61.90% | 52.94% | 51.86% | 50.12% | 54.21% |


---
