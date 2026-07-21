# run-1 计划（v3，2026-07-18）

状态：草案，超参/范围已获负责人确认，还没开始写代码。

## 范围
- 只用 chat 数据集（3个bag），按bag内时间轴前90%训练/后10%验证（叫 val，不叫
  test，和以后真正held-out的组区分开）。
- 不扩训练组、不推全量283-bag —— 留给 run-2 及以后。

## Phase 0：基础设施
- **0a 早停 = 学习率衰减联动**（已确认）：`plateau_patience=3` 个epoch没创新低
  → lr × `0.5`，计数清零；衰减满 `max_decays=3` 次后再plateau，或lr跌破
  `min_lr=1e-6`，才真正停止。全程保存当前最优checkpoint。
- **0b 确认shuffle**：`DataLoader(shuffle=True)` 语义上每个epoch本就会重新
  randperm，代码不用改；显式记录每epoch首个batch的样本index做一次校验，确认后
  记入 `CONTEXT.md`，之后不用再查。

## 报告格式（所有实验统一用这个，写入 `run-1/RESULTS.md`）
- 固定对照模型，每张表都带：**exp4**、**朴素基线**（重复最后一帧）、**本实验模型**。
  **`exp4_new`（原计划一起报，2026-07-18暂停**：在exp4验证过的预处理约定下
  跑出来peak_dist~46/PSR 0%，明显异常，权重本身正常，怀疑是它真实的训练预处理
  约定和exp4不一样、从没人验证过——深挖成本不低，先从对照表里去掉，只留一条
  开放问题记在CONTEXT.md，不阻塞run-1其余实验，见`access-model/predict.py`）。
- 两张表：peak_dist（↓）+ PSR_k5@5（↑）。列先分 **Train 块**（4个模型）再分
  **Val 块**（4个模型），行是 t+1~t+4（t+2标"重点"）。
- 每个实验单独出表，不跨实验合并平均。

## Phase 1：Batch size 消融（候选已确认）
- 固定配方：MSE + `noise_ratio`增强 + lr=1e-3起始 + `clip_len=10` + Phase 0a早停。
- bs ∈ `{16, 32, 64, 128}`。
- 看收敛曲线、早停点（衰减次数/最终epoch）是否随bs变化，选出run-1默认bs。

## Phase 2：用新infra重新确认最佳配方
- baseline复现：MSE + `noise_ratio` + lr=1e-3 + `clip_len=10`（用Phase 1选出的bs）。
- `clip_len=10` vs `clip_len=20` 对照（旧infra下是"不确定"结果，怀疑是被粗粒度
  早停切早了，这次重新看）。

## Phase 3：低成本单变量实验（已确认全部纳入run-1）
- `sm_ratio` ∈ `{0.3, 0.5, 0.7, 0.9, 1.0}`。
- SimVP容量 `(N_S, N_T)` 扫一下，候选 `(2,2)` 偏小 / `(4,4)` 默认 / `(6,6)` 偏大
  （chat数据小，顺便看有没有过拟合）。
- ~~exp4_new对照~~：已暂停，见"报告格式"一节说明。

## 明确留给 run-2 及以后
- 扩训练组（WordWolfExp G系列等），验证eighth-run C配方（chat+G1+G2训练、G3
  held-out）能否推广到更大规模。
- 推全量283-bag训练。
- `Demonstration_Data_nonconv` 该不该留训练池的消融。
- 数据多样性 vs 数量对照。

## 不再投入（已判负）
- soft-argmax峰值位置loss。
- DMVFN架构。
