# fourth-run 结果报告

fourth-run 的单一变量是模型架构：third-run 用的是 SimVP（直接回归整个 clip_len
历史，一次吐出 t+1~t+4），这里换成 DMVFN（Hu et al., CVPR 2023 highlight，
arXiv:2303.09875）——只用最近 2 帧算光流+融合掩码、反向 warp 合成下一帧，多步预测
靠自回归滚动（把自己的输出喂回去）。数据（train-data/train-test 划分/窗口）和
third-run 完全一致，唯一变量是架构本身。旧模型（access-model exp4）和 third-run
的最优 checkpoint（lr1e-6）都在同一个测试集上重新跑，保证三边公平对比。脚本每次
运行会在下面追加一节，不覆盖历史记录。

## 评价指标说明

- **peak_dist (k=1)**：预测声图和真值声图各自找最大值位置，算欧氏距离（64x64 网格
  的格子数）。数值越小越好。
- **PSR_k@n**：k x k 局部均值滤波后再算 peak_dist，看这个距离小于阈值 n 的样本占多少
  比例——是**成功率**，不是平均距离。数值越大越好。这里统一用 k=5, n=5（PSR_k5@5）。
- **位置相关性（Pearson r）**：预测峰值位置（行/列）和真值峰值位置的皮尔逊相关系数
  （行、列分别算完取平均），同时报告朴素连续性基线本身的相关性作为参照天花板。这是
  fourth-run 真正想验证的指标——DMVFN 的 warp 机制是否比 SimVP 的直接回归更能
  跟踪位置变化。
- **baseline（重复最后一帧）**：把输入历史的最后一帧原样当作未来 4 帧的预测，不用
  模型。

---
## 2026-07-11 18:51 对比运行

测试集：`['chat_debate_exp1_topic3']`（2112 个窗口）。训练集：80 个 bag（78 WordWolfExp + chat_topic1/2，和 third-run 完全一致）。checkpoint：旧 = `access-model/weights/config_simvp_exp4.pt`，third-run = `archive-runs/third-run/train/runs/lr1e-6/best_model.pt`，fourth-run = `train/runs/baseline/best_model.pt`。

### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 6.75 | 8.60 | 9.06 | 9.49 | 8.48 |
| third-run (SimVP, lr1e-6) | 7.04 | 9.07 | 9.69 | 10.30 | 9.02 |
| fourth-run (DMVFN) | 8.98 | 13.66 | 21.13 | 15.66 | 14.86 |
| baseline (repeat-last) | 7.11 | 9.27 | 10.06 | 10.53 | 9.24 |

### PSR_k5@5 (成功率, 越大越好)

| | t+1 | t+2 | t+3 | t+4 | Aggregate |
|---|---|---|---|---|---|
| old (exp4) | 71.92% | 63.16% | 60.84% | 58.81% | 63.68% |
| third-run (SimVP, lr1e-6) | 71.50% | 62.45% | 58.85% | 56.77% | 62.39% |
| fourth-run (DMVFN) | 63.07% | 31.72% | 4.26% | 0.95% | 25.00% |
| baseline (repeat-last) | 70.31% | 60.46% | 56.68% | 54.69% | 60.54% |

### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)

| | t+1 | t+2 | t+3 | t+4 | 平均 |
|---|---|---|---|---|---|
| old (exp4) | 0.280 | 0.177 | 0.142 | 0.124 | 0.181 |
| third-run (SimVP, lr1e-6) | 0.242 | 0.165 | 0.101 | 0.092 | 0.150 |
| fourth-run (DMVFN) | 0.195 | -0.022 | -0.069 | 0.003 | 0.027 |
| baseline (repeat-last) | 0.282 | 0.146 | 0.113 | 0.120 | 0.165 |
| naive continuity (last-input pos) | 0.282 | 0.146 | 0.113 | 0.120 | 0.165 |

**注意**：旧模型和third-run模型都是在这个测试集上重新评的，不是各自原来测试集上的历史数字。DMVFN 用自回归滚动产出 t+1~t+4，其余用直接回归。

---
## 2026-07-16 15:32 train/test 分列复核（新指标口径：逐步不合并，重点t+2）

训练集：`TRAIN_BAGS`（80 个 bag，同 third-run）。测试集：`TEST_BAGS`（chat_debate_exp1_topic3）。自回归滚动模型，t+3/t+4 已知会因误差累积大幅下滑（见 CONTEXT.md fourth-run 结论）。

#### TRAIN（42400 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| fourth-run (DMVFN) | 19.49 | 21.15 | 21.26 | 25.62 | 21.88 |
| baseline (repeat-last) | 9.80 | 11.52 | 12.29 | 12.75 | 11.59 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| fourth-run (DMVFN) | 33.21% | 11.27% | 3.69% | 1.58% | 12.44% |
| baseline (repeat-last) | 65.61% | 58.91% | 55.90% | 54.07% | 58.62% |

#### TEST（2112 个窗口）

peak_dist (k=1, 越小越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | 平均(仅参考) |
|---|---|---|---|---|---|
| fourth-run (DMVFN) | 8.98 | 13.66 | 21.13 | 15.66 | 14.86 |
| baseline (repeat-last) | 7.11 | 9.27 | 10.06 | 10.53 | 9.24 |

PSR_k5@5 (越大越好，**t+2 是重点参考步**):

| | t+1 | t+2 | t+3 | t+4 | Aggregate(仅参考) |
|---|---|---|---|---|---|
| fourth-run (DMVFN) | 63.07% | 31.72% | 4.26% | 0.95% | 25.00% |
| baseline (repeat-last) | 70.31% | 60.46% | 56.68% | 54.69% | 60.54% |


---
