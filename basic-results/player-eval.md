# 玩家实验（线下）的评估方法

玩家亲自参加游戏，每局结束后填写问卷（后置问卷 / post survey）。本文是这部分的评价方案。

- 数据处理见 [post_survey.py](post_survey.py)。

---

## 1. 实验设计（关键事实）

- 12 组 × 3 人；每组玩 6 局，每局 1 个条件。
- 条件 = Onsite / Video / Tele / PSSP / DoA / Random。Onsite、Video 不用机器人；
  Tele / PSSP / DoA / Random 是机器人条件。
- **顺序**：Onsite 固定第 1 局、Video 固定第 2 局；机器人 4 条件随机排在第 3–6 局。
- **操作者**：除 Onsite 外的 5 局各有 1 名操作者，按**序位**轮换 A→B→C→A→B；
  非操作者的 2 人（in-person）给出评价（Onsite 由 3 人评价）。操作者本人的评价剔除
  （[post_survey.py](post_survey.py) 中的 `drop_teleoperator_rows`）。
- 主假设：**PSSP 优于 DoA、Random**；Tele（真人遥操作）作参照。

---

## 2. 测量指标

| 指标 | 测量的条件 | 含义 |
| --- | --- | --- |
| GQS-J（5 子量表） | 仅机器人 4 条件 | 对机器人的印象（拟人性、好感度等），取各题平均 |
| GEQ（5 子量表） | 全部 6 条件 | 游戏体验（沉浸、愉悦、紧张等），取各题平均 |
| PTL（0–100%） | 仅机器人 4 条件 | "觉得是真人遥操作"的程度，越高越像真人 |
| Yes-Rate | 仅机器人 4 条件 | 回答"是真人遥操作"的人所占比例 |
| Wolf 推测 | 全部 6 条件 | 狼人猜对与否（游戏成绩） |

- **PTL** 由"是/否（是否觉得是真人遥操作）× 自信度"映射成一条 0–100% 量表
  （[post_survey.py](post_survey.py) 中的 `PTL_MAP`），50% 为"是/否"的分界：

  | 回答 | 自信度 | PTL |
  | --- | --- | --- |
  | 是 | 高 | 100 |
  | 是 | 普通 | 80 |
  | 是 | 低 | 60 |
  | 否 | 低 | 40 |
  | 否 | 普通 | 20 |
  | 否 | 高 | 0 |

- Negative Affect、Tension **越高越差**（不做反向计分）。
- **GQS Perceived Safety 子量表的 quiescent–surprised（平静–惊讶）项做反向计分**
  （[post_survey.py](post_survey.py) 中的 `REVERSE_CODE_ITEMS`）：该项方向与子量表其余项相反，
  反转后使其效价与 anxious–relaxed、agitated–calm 一致（高分 = 更安全 / 更平静）。依据：

  > Following Kim, Lee & Mutlu (2024), *Understanding LLM-powered Human-Robot Interaction*,
  > the quiescent–surprised item of the Godspeed Perceived Safety subscale was reverse-coded
  > to align its valence with the anxious–relaxed and agitated–calm items, since its direction
  > is inconsistent with the rest of the subscale.

---

## 3. 评价设计与理由

每条关键事实 → 由此决定方法：

- **不完全被试内设计**：操作者轮换使每名评价者只评了机器人 4 条件中的 **2–3 个**（没人评满）。
- **缺失与条件无关**：机器人条件顺序随机，所以"谁缺哪个条件"是随机的（随机缺失）。
  → 适合用**混合模型**：它能用上全部可用观测、正确处理这种缺失，不要求每人评满。
- **不在组内取平均**：同组 2 名评价者的看法可能差很大，取平均会掩盖个体差异；
  混合模型在**个体层面**建模，把每个评价者直接纳入，不做聚合。
- **Onsite/Video 位置固定** → 与出场顺序、疲劳完全混杂，无法剥离 → 只作**描述参照**，不进检验。
- **机器人 4 条件随机** → 顺序效应被平衡，组间比较干净 → **严格比较只在机器人 4 条件内进行**。

---

## 4. 统计模型（个体级线性混合模型）

**各指标的计算与统计处理：**

| 指标 | 指标值如何计算 | 统计处理 |
| --- | --- | --- |
| GQS-J 5 子量表 | 子量表各题平均（1–5）；Perceived Safety 的 quiescent–surprised 项先反向计分（见 §2） | 线性混合模型，**确认性**（给方向判定 ↑/↓） |
| GEQ 5 子量表 | 子量表各题平均（1–5） | 线性混合模型，**探索性**（标 `exp`，不下方向） |
| PTL | 是/否 × 自信度 → 0–100%（见 §2 映射表） | 线性混合模型，**确认性** |
| Yes-Rate | "是"人数 ÷ 总人数（忽略自信度） | 描述为主（各条件比例）；可选混合 logistic |
| Wolf 推测 | 猜对人数 ÷ 总人数 | 描述为主（各条件正确率） |

下面是混合模型（GQS / GEQ / PTL）的具体做法：

- 模型（每个子量表各拟合一次）：`y ~ mode + (1 | group) + (1 | participant)`
  - `mode`：固定效应（机器人 4 条件）。`group`、`participant`：随机截距。
    `participant` 用"组内唯一 ID"（如 `3_A`，A@组1 ≠ A@组2），因此天然嵌套在 group 内。
  - 因变量 `y`：GQS/GEQ 各子量表均值、PTL（当准连续）。
    每名评价者 × 每个所评条件 = 1 个观测（满编约 96 个）。
  - 工具：R 的 `lme4`（拟合）+ `lmerTest`（检验）+ `emmeans`（对比）；
    由 [post_survey.py](post_survey.py) 导出个体级 CSV，再调用 [player_lmm.R](utils/player_lmm.R)。
- 步骤：
  1. **拟合**上面的混合模型。
  2. **`mode` 整体效应**：F 检验，分母自由度用 **Satterthwaite 近似**（lmerTest 默认）。
     混合模型里 F 检验的"分母自由度"并不明确（数据有聚类，有效样本量不是简单的 N），
     Satterthwaite 据数据估计一个合适的（通常是分数）自由度。
  3. **计划对比**（基于模型边际均值 emmeans）：① PSSP vs DoA　② PSSP vs Random
     ③ PSSP vs Tele，p 用 **Holm** 校正。
- 每个对比给出：**估计差、95% 置信区间、效应量 d**。
  d 的分母用**总标准差** `√(σ²_group + σ²_participant + σ²_residual)`（Westfall 等人的做法），
  而非仅残差 SD——后者会高估效应量。
- **方向主张**：只有当对比的 95% 置信区间在预期方向上不跨过 0 时，才写"PSSP 更高"。
- 关于自由度：本可用 **Kenward–Roger（KR）**（比 Satterthwaite 多一层协方差校正，小样本更稳），
  但 KR 依赖 `pbkrtest → doBy` 一大串包、本机编译困难，故用 lmerTest 内置的 **Satterthwaite**；
  二者在本场景结果基本一致。
- **奇异拟合（singular fit）**：组数少时 `group` 方差常被估计为 0。一旦检测到奇异拟合，脚本**主动
  退化**为 `y ~ mode + (1|participant)`（保留被试随机截距以处理重复测量，只去掉估不出来的 `group` 层），
  并在该尺度上标注 `[reduced: dropped (1|group)]`。固定效应（`mode` 的估计与对比）在两种模型下都有效、无偏；
  退化只是让模型稳定、更好向审稿人交代。数据增多后 `group` 方差可估时会自动用完整模型。

---

## 5. 各指标注意

- **PTL** 走上面的混合模型，但它由 6 个离散档（0/20/…/100）映射而来。脚本输出残差正态性诊断
  （Shapiro-Wilk）；实测 PTL 残差显著偏离正态，故其 LMM 结果**谨慎解读、以描述/参照为主**。
- **Yes-Rate**（二值，忽略自信度）：**以描述为主**——各条件"是"的比例，用作 PTL 的方向佐证，
  并核对真人操作的 **Tele 的"是"率最高**（有效性确认）。二值 GLMM（混合 logistic
  `yes ~ mode + (1|group) + (1|participant)`）因当前样本小、易不收敛/完全分离而**暂不做**，
  待数据充足可作为遥操作知觉的稳健检验。
- 因此**遥操作知觉**这一维度当前没有单独的正式推断检验（PTL 谨慎 + Yes-Rate 描述），
  是小样本下的务实取舍，待数据增多再补 GLMM。
- **GEQ** 方向未定，作**探索性**：只报整体差异与描述，不下"哪个更高"。
  Onsite、Video 作自然游玩的参照（仅描述）。
- **Wolf 推测**是游戏成绩（玩家真在玩），按条件报正确率，以描述为主。
- **Tele** 是真人遥操作，机器人行为里含操作者个人风格，方差可能偏大，解释参照时留意。

---

## 6. 要报告的内容

- 描述统计（人数、年龄、性别；各条件均值/分布）。
- 混合模型：`mode` 主效应、各随机效应方差（σ²_group、σ²_participant、残差）、收敛情况。
- 计划对比（主要结果）：估计差、95% 置信区间、校正后 p、效应量 d。
- PTL 与 Yes-Rate 的方向一致性。
- 图：各条件箱线图（由 [post_survey.py](post_survey.py) 生成）、计划对比森林图。

---

## 7. 程序

- [post_survey.py](post_survey.py)：拉取数据 → 剔除操作者 → 生成箱线图 →
  把个体级数据导出到一个**临时 CSV** → 调用 [utils/player_lmm.R](utils/player_lmm.R) 跑混合模型
  →（用完即删该 CSV）。R 的输出连同描述统计一起存进 `post_survey_results.txt`
  （图存为 `post_survey.pdf`）。
- [utils/player_lmm.R](utils/player_lmm.R)：第 4 节的混合模型（`lme4` + `lmerTest` + `emmeans`）。
- [utils/utils.py](utils/utils.py)：绘图与共用辅助函数。
- 依赖：R 及 `lme4` / `lmerTest` / `emmeans`。若未装 R，post_survey.py 会跳过该步并提示。
</content>
