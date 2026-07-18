# fifth-run 结果报告

fifth-run 的单一变量还是数据规模/多样性（第二次测试这个假设，第一次是 third-run）：
训练集从 third-run 的 78 个 WordWolfExp bag + chat 前两段扩到 2026-07-13 PSSPData 重新
整理后的全部 ~278 个 bag（15+ 个来源：WordWolfExp 含合并进去的 EXP1-3/testrun_0420、
GRP_meeting、olab_0630/rev、ATR_teleoperation 两个 RIKEN 集合、chat、Demonstration_Data
(+nonconv)、demo_data_0318_becap、egoSAS_test_data、kitchen，见 CONTEXT.md/
DATA_REPORT.md）。测试集是 chat_debate_exp1_topic3（和 third-run 一致）+ G13 的
game3/4/5/6 四局——**注意 G13_game2_Video 和 G13_interview 仍然留在训练集里，这是负责人
明确要求的选择，会有同组信息泄漏风险（同一批人同一个房间），不是无意的疏漏**，dataset.py
的 `KNOWN_GROUP_OVERLAP` 显式记录了这个例外。SimVP 架构/loss/lr 等超参数和 third-run
完全一致，不重新引入其它变量。旧模型（access-model exp4）在同一个新测试集上重新跑一遍，
不用它原来的历史数字，保证公平对比。脚本每次运行会在下面追加一节，不覆盖历史记录。

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
## 2026-07-13 22:00 对比运行

测试集：`['chat_debate_exp1_topic3', 'G13_game3_DoA', 'G13_game4_Random', 'G13_game5_PSSP', 'G13_game6_Tele']`（3584 个窗口）。训练集：278 个 bag（2026-07-13 PSSPData 重跑后的全部集合，见 dataset.py/DATA_REPORT.md）。checkpoint：旧 = `access-model/weights/config_simvp_exp4.pt`，新 = `train/runs/baseline/best_model.pt`。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 8.46 | 10.48 | 10.86 | 11.26 | 10.26 |
| new (baseline) | 8.45 | 10.65 | 11.34 | 11.58 | 10.50 |
| baseline (repeat-last) | 8.76 | 10.99 | 11.50 | 11.92 | 10.79 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 68.05% | 59.35% | 57.81% | 56.00% | 60.30% |
| new (baseline) | 67.61% | 58.73% | 55.66% | 54.41% | 59.10% |
| baseline (repeat-last) | 66.21% | 56.78% | 54.10% | 52.29% | 57.35% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.509 | 0.388 | 0.365 | 0.338 | 0.400 |
| new (baseline) | 0.513 | 0.402 | 0.364 | 0.349 | 0.407 |
| baseline (repeat-last) | 0.492 | 0.355 | 0.327 | 0.302 | 0.369 |
| naive continuity (last-input pos) | 0.492 | 0.355 | 0.327 | 0.302 | 0.369 |

**注意**：旧模型是在这个测试集上重新评的，不是它原来 WordWolfExp holdout 上的历史数字，两者不能直接比较。

---
## 2026-07-13 22:54 对比运行

训练集：278 个 bag（2026-07-13 PSSPData 重跑后的全部集合，见 dataset.py/DATA_REPORT.md）。checkpoint：旧 = `access-model/weights/config_simvp_exp4.pt`，新 = `train/runs/baseline/best_model.pt`。

### chat

测试集：`['chat_debate_exp1_topic3']`（192 个窗口）。

#### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 6.57 | 7.80 | 7.70 | 7.85 | 7.48 |
| new (baseline) | 7.21 | 8.72 | 8.43 | 8.62 | 8.25 |
| baseline (repeat-last) | 6.81 | 9.03 | 9.29 | 9.56 | 8.68 |

#### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 72.92% | 67.19% | 66.67% | 66.67% | 68.36% |
| new (baseline) | 71.88% | 62.50% | 62.50% | 62.50% | 64.84% |
| baseline (repeat-last) | 72.40% | 61.98% | 60.94% | 58.85% | 63.54% |

#### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.305 | 0.164 | 0.137 | 0.158 | 0.191 |
| new (baseline) | 0.193 | 0.109 | 0.119 | 0.110 | 0.133 |
| baseline (repeat-last) | 0.414 | 0.159 | 0.107 | 0.092 | 0.193 |
| naive continuity (last-input pos) | 0.414 | 0.159 | 0.107 | 0.092 | 0.193 |

### wordwolfexp (G13)

测试集：`['G13_game3_DoA', 'G13_game4_Random', 'G13_game5_PSSP', 'G13_game6_Tele']`（1440 个窗口）。

#### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 11.00 | 13.19 | 13.45 | 13.90 | 12.88 |
| new (baseline) | 10.61 | 13.26 | 13.97 | 13.94 | 12.94 |
| baseline (repeat-last) | 11.20 | 13.49 | 13.57 | 13.99 | 13.06 |

#### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 62.22% | 54.03% | 53.54% | 51.81% | 55.40% |
| new (baseline) | 62.92% | 54.17% | 51.11% | 51.32% | 54.88% |
| baseline (repeat-last) | 60.07% | 51.46% | 50.49% | 48.75% | 52.69% |

#### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.386 | 0.251 | 0.208 | 0.176 | 0.255 |
| new (baseline) | 0.382 | 0.153 | 0.018 | 0.025 | 0.145 |
| baseline (repeat-last) | 0.370 | 0.222 | 0.210 | 0.191 | 0.248 |
| naive continuity (last-input pos) | 0.370 | 0.222 | 0.210 | 0.191 | 0.248 |

### combined

测试集：`['chat_debate_exp1_topic3', 'G13_game3_DoA', 'G13_game4_Random', 'G13_game5_PSSP', 'G13_game6_Tele']`（1664 个窗口）。

#### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 10.39 | 12.50 | 12.77 | 13.14 | 12.20 |
| new (baseline) | 10.11 | 12.79 | 13.39 | 13.39 | 12.42 |
| baseline (repeat-last) | 10.59 | 12.89 | 13.02 | 13.41 | 12.48 |

#### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 63.82% | 55.71% | 54.99% | 53.61% | 57.03% |
| new (baseline) | 64.30% | 54.87% | 52.10% | 52.28% | 55.89% |
| baseline (repeat-last) | 61.90% | 52.94% | 51.86% | 50.12% | 54.21% |

#### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.427 | 0.299 | 0.266 | 0.245 | 0.309 |
| new (baseline) | 0.421 | 0.239 | 0.205 | 0.205 | 0.267 |
| baseline (repeat-last) | 0.413 | 0.263 | 0.251 | 0.233 | 0.290 |
| naive continuity (last-input pos) | 0.413 | 0.263 | 0.251 | 0.233 | 0.290 |

**注意**：旧模型是在这个测试集上重新评的，不是它原来 WordWolfExp holdout 上的历史数字，两者不能直接比较。`chat_debate_exp1_topic3` 只用了每个 bag 时间轴上的最后 10%（`TEST_BAG_MIN_START_FRAC`）——旧模型训练代码（`access-model-train/utils_all_load.py`）确认过对每个 bag 按 `train_ratio=0.9` 做时间切分，`exp_name=[]` 意味着它当时用了 npz_path 下的全部数据，不确定 chat 数据当时在不在那个目录里；用最后 10% 可以保证这部分数据无论如何都不是旧模型训练时见过的。G13 的 4 个测试 bag 确认双方模型都没用来训练，按整段使用。


---
## 2026-07-16 16:21 train/test 分列复核（新指标口径：逐步不合并，重点t+2）

训练集：全部 278 个 bag。超过 30000 窗口时做等距抽样（stride subsample，不是随机），换取可行的评估时间。测试集：chat_debate_exp1_topic3(最后10%) + G13 game3-6，chat/wordwolfexp/combined 三组分开报告（不只看合并平均）。exp4 不在训练集上评估（训练数据边界不同，无过拟合诊断意义）。

#### TRAIN（等距抽样自 234194 个窗口）（29248 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| fifth-run (baseline) | 8.52 | 9.30 | 9.68 | 9.91 | 9.35 |
| baseline (repeat-last) | 8.87 | 10.24 | 10.91 | 11.30 | 10.33 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| fifth-run (baseline) | 67.17% | 63.42% | 62.02% | 60.90% | 63.38% |
| baseline (repeat-last) | 63.33% | 56.95% | 54.16% | 52.44% | 56.72% |

#### TEST [chat]（192 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 6.56 | 7.81 | 7.70 | 7.85 | 7.48 |
| fifth-run (baseline) | 7.21 | 8.72 | 8.43 | 8.62 | 8.25 |
| baseline (repeat-last) | 6.81 | 9.03 | 9.29 | 9.56 | 8.68 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 72.92% | 67.19% | 66.67% | 66.67% | 68.36% |
| fifth-run (baseline) | 71.88% | 62.50% | 62.50% | 62.50% | 64.84% |
| baseline (repeat-last) | 72.40% | 61.98% | 60.94% | 58.85% | 63.54% |

#### TEST [wordwolfexp (G13)]（1408 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 10.95 | 13.06 | 13.29 | 13.81 | 12.78 |
| fifth-run (baseline) | 10.53 | 13.19 | 13.93 | 13.92 | 12.89 |
| baseline (repeat-last) | 11.16 | 13.45 | 13.43 | 13.85 | 12.97 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 62.43% | 54.55% | 54.26% | 51.99% | 55.81% |
| fifth-run (baseline) | 63.28% | 54.47% | 51.28% | 51.42% | 55.11% |
| baseline (repeat-last) | 60.23% | 51.56% | 50.99% | 49.22% | 53.00% |

#### TEST [combined]（1664 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 10.39 | 12.49 | 12.77 | 13.13 | 12.19 |
| fifth-run (baseline) | 10.11 | 12.79 | 13.39 | 13.39 | 12.42 |
| baseline (repeat-last) | 10.59 | 12.89 | 13.02 | 13.41 | 12.48 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| old (exp4) | 63.82% | 55.83% | 55.11% | 53.55% | 57.08% |
| fifth-run (baseline) | 64.30% | 54.87% | 52.10% | 52.28% | 55.89% |
| baseline (repeat-last) | 61.90% | 52.94% | 51.86% | 50.12% | 54.21% |


---
