# PSSP 模型 —— 项目记录

## 项目是什么

PSSP = 预测**下一个说话人在声图（sound map）中的位置**。声图是麦克风阵列音频通过
波束成形得到的房间二维热力图，模型输入过去若干帧的声图+摄像头画面序列，预测未来
几帧的声图。核心评估指标是**预测峰值位置的定位精度**，不是逐像素重建误差。

**背景**：代码是负责人独自写的（无AI辅助、无git历史），用了一段时间后决定重新
审视整个流程。信心缺口涉及四个方面：模型架构选择、训练超参数、数据流程正确性、
评估指标有效性——默认不假设任何一部分是对的，逐一排查。这次重启的目标是找到真正的
性能瓶颈，而不是简单地用新数据重跑一遍训练。

## 仓库结构（当前状态）

```
train-pssp/
  CONTEXT.md                # 本文件
  preprocessing/            # 统一的原始数据处理入口（rosbag → npz + QC视频）
    bag_io.py                 # sqlite3直读bag + 手写CDR解码，不依赖ROS2
    soundmap.py                # 纯PyTorch波束成形声图生成器
    build_dataset.py            # JOBS注册表驱动的提取脚本，见"数据集"一节
    DATA_REPORT.md               # 数据来源/适用性/最终规模的详细记录
  train-data/                # 默认训练用的npz池，所有run共享
  train-data-aux/            # 结构性存疑/成分混杂的数据，不进默认训练（2026-07-12
                                # PSSPData整盘重组重跑后暂时清空，负责人要求全部先
                                # 进train-data，后续再判断哪些挪过来）
  soundmap-videos/           # 每个bag的目视QC视频（主画面+下方VAD/label滚动面板，
                                # 见"数据集"一节）
  access-model/              # 旧模型（exp4），只做推理，不再训练
    predict.py / simvp.py / configs/ / weights/
    access-model-train/        # 产出exp4权重的旧训练代码存档（不再运行/修改）
  archive-runs/
    old-runs-1/                # 2026-07-18 迁移到新PC（更强GPU/更大显存）时整批归档，
                                  # 旧PC上做的全部8轮，只读存档
      first-run/                  # 第1轮：归一化+lr修复验证
      second-run/                  # 第2轮：loss函数消融（MSE/BCE/KL）
      third-run/                   # 第3轮：数据规模+chat泛化测试
      fourth-run/                  # 第4轮：SimVP vs DMVFN架构对比（结论：DMVFN败）
      fifth-run/                   # 第5轮：数据规模/多样性测试#2（结论：仍未突破天花板，
                                      # 但挖出lr/滑窗密度/数据增强三个真实训练管线差异）
      sixth-run/                   # 第6轮：只用chat小规模消融lr/滑窗密度/数据增强
                                      # （结论：lr=1e-3+数据增强追平旧模型，dense不是必需）
      seventh-run/                 # 第7轮：搬到278-bag全量+WordWolfExp+chat子集，扫lr
                                      # （结论：没推广；lr只让曲线健康，动不了天花板）
      eighth-run/                  # 第8轮：换loss监督目标（soft-argmax峰值位置）+更长
                                      # 窗口，攻七轮都没解决的位置相关性天花板，chat上
                                      # 小测（train/losses.py 是核心新增）。**这轮是在
                                      # 旧PC上做的最后一轮，C_g1g2_train_g3_test 配方是
                                      # 迁移时最新的正向结论，run-1 的起点**（见"当前
                                      # 开放问题"一节和 run-1 自己的说明）。
  run-1/                       # 第9轮起，新PC（更强GPU/更大显存，见下）上的第一轮，
                                  # 2026-07-18 立项，计划草案见 run-1/PLAN.md
```
`archive-runs/old-runs-1/` 下的 run 路径深度比迁移前又多了一层，脚本里原来的
`DATA_DIR`/`ACCESS_MODEL_DIR` 等用 `_HERE.parent.parent.parent`（3层）定位到
`train-pssp/`——现在需要 `_HERE.parent.parent.parent.parent`（4层）。**这些脚本
当前没有跟着改**（沿用上次归档时的先例：只读存档，RESULTS.md 里的数字是归档前
留下的，不代表现在能直接跑通）——如果以后想重跑某一轮旧代码，先按新深度改这几个
路径常量。

每一轮都是自包含的完整流水线快照（只共享 `train-data/` 这个 npz 池，不共享其它
顶层公共模块），这样任何时候看某一轮结果都对应得上那一轮自己的代码。以后新方案
默认开新的 `Nth-run/` 目录（新PC上从 `run-1/` 重新计数，不接旧PC的"第N轮"编号，
避免和 `old-runs-1/` 下的历史编号混淆）。

## 2026-07-18 迁移到新PC

工作区换到了一台桌面GPU更强、显存更大的新PC（`nvidia-smi` 确认：RTX 3090，
24GB显存）。旧PC上的8轮全部归档到 `archive-runs/old-runs-1/`（见上）。这次迁移
不只是换机器重跑——旧PC上受限于显存的几个折中（比如 eighth-run B/C/D 实验
`clip_len=20` 时模型参数从13M涨到38M，必须把 batch size 从32降到16才跑得动）
在新PC上不再是硬约束，新PC上第一轮工作（`run-1/`）会重新评估这些折中，计划草案
见 `run-1/PLAN.md`（截至2026-07-18仍是草案，还没开始实现/训练）。

旧代码库（`access-model/access-model-train/`）本身有若干已知问题（`vae.py`/`vdt.py`
源码缺失导致只有 `simvp` 方法能跑、DDP 硬编码 `CUDA_VISIBLE_DEVICES`、`sm_ratio`
融合是像素级加权不可学习等），但这份代码只做历史存档、不再修改，这些问题不再需要
跟踪——新代码（`first-run/` 起）都是重写的，不继承这些问题。

## 已经验证/settled 的方法论决定

排查过、有证据支撑、不需要重新论证：

- **归一化**：target 和输入的声图分量都用 `exp(sm - sm.max())`（逐帧独立），输入
  再按 `sm_ratio` 和灰度图混合，模型输出 sigmoid。这是机器人实际部署代码
  （`boxie_node/mode_pssp.py`/`policy_utils.py`）验证过的真实方式——最初误以为
  旧训练用原始 0~160 量级当 target（依据是一份未经验证的脚本快照的注释状态），
  改用 exp 变换后单样本冒烟测试 peak_dist 从 27 降到 2.24，强烈佐证方向正确。
- **提取频率 2Hz**：是从任务信息量本身出发的建模决策（0.5s 内变化不大），不是
  随意继承的旧约定；4Hz 只是旧硬件延迟约束、30Hz 只是"先抓多点以后再说"，都不是
  "更准确"的替代方案。
- **声图生成器**：纯 PyTorch 重实现（`soundmap.py`），在 generator-compare 项目里
  对 65 个 bag、约 5 万个 tick 验证过和旧 acoular 版等价（Pearson r≈0.99999），
  GPU 上快 7.7 倍。
- **bag 读取**：`bag_io.py` 直接读 sqlite3 底层 `.db3` 文件 + 手写 CDR 解码，不依赖
  ROS2/rosbags。`audio_decoder_for()` 按每个 bag 自己声明的消息类型（`AudioData`
  无 header版 vs `AudioDataStamped` 带 header 版）自动选解码器。
- **train/test 切分按组/按 bag，不按文件内部时间切**：文件内部时间切分（旧代码
  方式）没有帧级重叠，但 train/test 是同一段对话的相邻几秒，测的是"记不记得住"
  不是"能不能泛化"。
- **密集滑窗是过采样不是数据增强**：语义上没有新信息，但只要 split 在组/bag 级别
  做对了，就不影响评估有效性。
- **PSR_k@n 指标**（来自负责人自己的论文，k5@5 是 headline 设置）：k×k 边缘感知
  局部均值滤波后再算 peak_dist，看距离小于阈值 n 的样本占比——是成功率，不是均值。
  k=1（不滤波）就是原始 peak_dist。**负责人后来明确表示这篇论文的指标"是几年前
  胡诌的"，不必再当权威依据**——现在核心看 peak_dist/PSR，加上下面这个更关键的
  "位置相关性"指标，PSR 仅作参考。

## 关键诊断指标：位置相关性（Pearson r）

目前最重要的诊断工具（second-run 引入、third-run 大量使用）：预测峰值位置和真值
峰值位置的皮尔逊相关系数（行、列分别算再取平均）。同时总是报告一个**朴素连续性
基线**——直接用输入最后一帧的峰值位置当预测，不用模型——作为参照天花板：如果
模型的相关系数低于这个基线，说明模型没学会"跟着输入变化调整预测"，比什么都不做
还差。

这个指标比 peak_dist/PSR 更能揭示问题：峰值距离小可能只是"收敛到一个平均位置、
真值本身波动也不大"的假象，相关系数直接检验"输入变、预测跟不跟着变"。

**2026-07-16起弃用**：负责人认为这个指标的计算方式不一定合理——它不是看单条轨迹
里预测有没有跟着真值动，而是把整个测试集所有窗口的预测/真值峰值坐标各自拼成一条
长向量再算皮尔逊相关（见`compare_ablations.py`旧版`_pearson`实现），测的其实是
"预测值对测试集内样本间差异是否敏感"，不完全等价于"跟踪能力"。**往后的评估
（`compare_ablations.py`）不再计算/报告这个指标，只看 peak_dist + PSR。** 本节及
下面所有历史记录里出现的"位置相关性"数字保留原样，不回填、不删除，仅作历史参考、
不再作为决策依据。

## 全部模型/run 的结果一览

**汇报硬性要求（负责人定的，必须遵守，2026-07-16 更新）**：①朴素基线每次都要
一起报，不能只看模型自己的数字；②t+1~t+4 逐步结果都要给全，**不能合并成一个
聚合数字当判断依据**——**t+2 是重点参考步，t+1/t+3/t+4 仅作辅助参考**；③train
集和 test 集指标都要报，只看 test 不能诊断是否过拟合。指标本身不变，仍是
peak_dist(k=1) + PSR_k5@5，位置相关性已弃用（见上一节）。

下表紧凑只列**t+2 的 train/test 数值**（重点步），t+1/t+3/t+4 完整逐步数字见
各 `RESULTS.md`（2026-07-16 已为全部 archive-runs 重新生成，新增 train 集评估，
脚本是每个 run 目录下新增的 `evaluation/eval_train_test.py`）。278-bag 规模的
训练集（fifth/seventh-run 的 baseline/wordwolf_chat 系列）评估时间过长，train
指标改用等距抽样（stride subsample，见各脚本 `MAX_TRAIN_WINDOWS`），不是随机
抽样，抽样比例在对应 RESULTS.md 里注明。**train 指标不含 exp4**——exp4 的训练
数据边界和这里的 TRAIN_BAGS 无关，"train 指标"对它没有过拟合诊断意义。

### G2+G6 WordWolf holdout（first-run/second-run 用的测试集，train=16bag/8704窗口，test=3520窗口）

**t+2（重点步），train / test 分列，peak_dist↓ / PSR_k5@5↑：**

| 模型 | train peak_dist | train PSR | test peak_dist | test PSR |
|---|---|---|---|---|
| exp4（旧，不参与train集评估） | — | — | 12.15 | 59.86% |
| first-run baseline (MSE, lr=1e-3) | 10.27 | 63.79% | 10.65 | 65.00% |
| second-run MSE | 10.27 | 63.74% | 10.58 | 64.83% |
| second-run BCE | 10.53 | 62.57% | 10.29 | 65.71% |
| second-run KL | 10.15 | 63.40% | 10.41 | 65.71% |
| 朴素基线（重复最后一帧） | 11.53 | 57.56% | 12.41 | 57.84% |

**结论（2026-07-16 用新标准复核，不变）**：t+2 上四个模型都稳定超过朴素基线
（train/test 都是），loss 从 MSE 换成 BCE/KL 在 t+2 互有胜负、差距很小，没有
哪个明显更准。train/test 差距不大（比如 MSE train 10.27 vs test 10.58），没有
明显过拟合迹象。位置相关性排查（已弃用指标，历史参考）当时发现三个模型都低于
朴素连续性天花板——这个判断不依赖已弃用指标也大致成立：t+2 上模型只比朴素基线
好一点，不是压倒性优势。t+1/t+3/t+4 完整数字见 `archive-runs/old-runs-1/first-run/
RESULTS.md`、`archive-runs/old-runs-1/second-run/RESULTS.md`。

### chat_debate_exp1_topic3（third-run 的 held-out 测试集，训练集=80个bag/42413窗口
=78个WordWolf bag + chat 前两段，test=2138窗口，未做TEST_BAG_MIN_START_FRAC裁剪，
如实复现third-run当年的测试集）

**t+2（重点步），train / test 分列，peak_dist↓ / PSR_k5@5↑：**

| 模型 | train peak_dist | train PSR | test peak_dist | test PSR |
|---|---|---|---|---|
| exp4（旧，未训练过 WordWolf/chat，不参与train集评估） | — | — | 8.60 | 63.16% |
| third-run baseline（lr=1e-3，早停epoch12） | 10.41 | 64.02% | 9.26 | 61.74% |
| third-run lr=1e-6（跑满60epoch,未早停） | 10.38 | 64.14% | 9.07 | 62.45% |
| fourth-run DMVFN（架构对比，见下） | 21.15 | 11.27% | 13.66 | 31.72% |
| 朴素基线/朴素连续性 | 11.52 | 58.90% | 9.27 | 60.46% |

**结论（2026-07-16 用新标准复核）**：train 上两个 SimVP 变体都清楚超过朴素基线
（10.4 vs 11.5），但 **test 上四个模型（含 exp4）和朴素基线几乎打平**（9.07~9.27
之间），test peak_dist 甚至比 train 更好——这不是模型学得更好，是 test 集
（chat_topic3，未裁剪）本身比 train 集更容易（朴素基线自己在 test 上 9.27，
在 train 上 11.52，任务难度不同，不能直接比较 train/test 的绝对数值）。lr=1e-6
（旧模型真实训练 lr，`simvp_exp4.json` 确认，cosine scheduler 代码从未启用，
全程恒定 lr）比 lr=1e-3 在 test 上略好，但两者都没能明显超过朴素基线。
**fourth-run DMVFN 在 train 集上比朴素基线还差**（21.15 vs 11.52 peak_dist，
PSR 11%），确认不是"只在没见过的数据上失败"，是架构本身（自回归误差累积）
的问题，训练数据本身也学不好。

### 补充诊断：同一 lr=1e-6 checkpoint 在其它场次上的位置相关性（非正式评估，用来
排查"是训练集内/外的问题，还是场次本身难度不同"）

| 场次 | 是否在third-run训练集里 | 朴素基线 | exp4 | third-run(lr1e-6) |
|---|---|---|---|---|
| chat_topic2 | 是 | 0.346 | 0.424 | 0.417 |
| chat_topic3 | 否 | 0.165 | 0.181 | 0.150 |
| G6_game5_PSSP | 是 | 0.101 | 0.089 | NaN（t+2预测方差为0）|
| GRP_meeting(1-56-44) | 否 | 0.375 | **0.432** | 0.376 |
| olab_0630(15-17-22) | 否 | 0.167 | **0.301** | **0.264** |
| Experiment0312_PSSP | 否 | 0.254 | 0.206 | 0.217 |

**关键发现**：
1. **模型架构本身有能力学会超过朴素基线**（chat_topic2/olab_0630/GRP_meeting 上
   exp4 和/或 third-run 都明显超过）——不是这套方法根本学不会跟踪位置。
2. **"训练集内/外"不能干净地解释表现好坏**——不同场次本身的可预测性差异很大
   （比如 G6_game5_PSSP 连朴素基线自己的相关系数都只有 0.101，远低于 chat_topic2
   的 0.346），场次的内在动态特性比"见没见过"更重要。
3. **反直觉的发现：exp4（旧模型，未见过 WordWolf/chat 任何数据）在这 6 个场次里
   除 Experiment0312 外全部稳定超过朴素基线，泛化表现比 third-run（训练数据量
   大得多：80 个 bag，几乎全是 WordWolf）更稳定**。提示**训练数据的多样性（场景
   类型丰富）可能比单纯堆同质数据的数量更重要**——third-run"加量"的方式（78个
   高度同质的 WordWolf bag + 仅 2 个 chat）换来的可能是对 WordWolf 模式的强适应，
   代价是跨场景泛化能力反而不如 old model 原本（构成未知，但大概率更多样）的
   训练配方。**这是目前最值得跟进的方向。**

### fourth-run：SimVP vs DMVFN 架构对比（2026-07-11，结论：DMVFN 败）

背景：SimVP 是 2022 年的 CNN 架构，负责人问是否有更新、更适合实时交互（快、不用
diffusion）、又比 transformer 省数据的替代方案。选了 **DMVFN**（Hu et al., CVPR
2023 highlight, arXiv:2303.09875）试——查证原论文后发现负责人对"用12帧算光流"的
回忆有误，论文实际只用**最近2帧**，9级 MVFB 级联反复精炼光流+融合掩码、backward
warp 合成下一帧；多步预测靠自回归滚动（把自己的输出喂回去）。协商后决定：照论文
用2帧+自回归，但不实现纯粹为推理提速的 Routing Module（论文自己的消融显示去掉它
精度几乎不变）。数据、train/test 划分和 third-run 完全一致（`fourth-run/train/
dataset.py` 是 third-run 的原样拷贝），单一变量是架构本身。

**实现过程中连续踩了两个真实的数值/训练坑（不是"数据不够"，是这次新写的架构代码
本身的问题）**：
1. **flow 场无约束爆炸**：9 级残差累积没有上限，几步内窜到千万量级，融合掩码
   饱和到精确 0，模型输出坍缩成常数——加了 `tanh` 限幅（每级最多贡献±2像素）修复。
2. **限幅后仍有一次"饱和死锁"**：`max_flow=8`+`lr=1e-3`（照搬SimVP的lr）时，9级
   全部学会往同一方向饱和推，梯度在 tanh/sigmoid 饱和区消失，模型卡死在"直接采样
   输入角落像素"（角落在这个任务里恰好接近0）这个偷懒解——test_loss 从第2个epoch
   起完全冻结不变，是死锁不是收敛。换成 `max_flow=2`+`lr=1e-4` 后训练恢复正常。
3. **肉眼看预测形状像"漂在空中的塑料袋"**（负责人原话）——放大对比图证实：预测
   是不规则、带尖锐棱角、拖着细丝延伸的碎片状，不是真值那种平滑高斯团块，是**光流
   场空间不平滑导致的经典 warping 伪影**（我最初的简化实现漏了这个几乎所有光流
   论文都会加的正则项）。加了 total-variation 平滑正则（`flow_smoothness_loss`，
   权重0.1）后重新训练，t+1/t+2 的形状明显改善；t+3/t+4 仍有拖尾，但比之前好很多。

**最终结果（60 epoch 跑满，未早停）**：t+1 尚可（peak_dist 8.98 接近 SimVP 的
7.0，PSR 63% vs SimVP 71%），**但 t+2 起断崖式下跌，t+3/t+4 基本失效**（PSR 仅
4.3%/0.95%）——自回归滚动的误差累积是主因（每一步都在 warp 自己上一步不完美的
输出，偏差逐级放大），可视化诊断（形状从"清晰"变"撕裂"正好发生在 t+2→t+3）和
这个数字趋势完全吻合。这正是负责人一开始就承认的风险（"我理解这个风险，不过我
觉得还是可以尝试一下"），风险应验了。

**结论：这次 DMVFN 实现全面不如 SimVP（也不如朴素基线）**，fifth-run 扩数据训练
应该用 SimVP，不是 DMVFN。DMVFN 代码保留在 `fourth-run/`，如果以后想再试（比如
换成非自回归的多帧直出头、或加大训练数据量看能不能缓解误差累积），代码和这次的
教训都在，不用从头再来。

### fifth-run：数据规模/多样性测试#2（2026-07-13/14，结论：仍未突破天花板）

背景：third-run 的假设——"数据多样性可能比数据量更重要"——只用 78 个 WordWolf bag +
2 个 chat bag 测过，样本太小。2026-07-13 PSSPData 重跑后 `train-data/` 有了 283 个
真正来自 15+ 个不同来源的 bag，fifth-run 用 278 个（283 - 5 测试）做训练，架构/loss/
lr 等和 third-run 完全一致，单一变量还是数据。测试集：chat_debate_exp1_topic3 +
WordWolfExp G13 的 game3/4/5/6（G13_game2_Video/interview 仍在训练集里，负责人明确
接受的同组泄漏风险）。60 epoch 上限，lr=1e-3 下 epoch 3 是最优、epoch 13 早停。

**评估方法论上的一个真实修正**：旧模型（exp4）的训练代码
（`access-model-train/utils_all_load.py` 的 `NPZLoader.get_index()`）确认过它对
**每个 bag 按时间做 train_ratio=0.9 的切分**（前90%它自己训练、后10%它自己测试），
且它的 config 里 `exp_name: []` 意味着当时用了 npz_path 下的全部数据——不确定
chat_debate_exp1_topic3 当时在不在那个目录里，但如果在，用完整这个 bag 评旧模型
就会让它"偷看"过前90%，对旧模型不公平地有利。**修复：chat_debate_exp1_topic3 只用
时间轴上最后10%做测试**（`dataset.py` 的 `TEST_BAG_MIN_START_FRAC`），和旧模型自己
的切分对齐，保证这部分数据双方模型都没见过。G13 的 4 个测试 bag 确认双方模型都没
拿来训练过，按整段使用不需要裁剪。`compare_old_new.py` 现在把 chat / wordwolfexp
(G13) / combined 三组结果分开报告，不再只看合并数字。

**结果（chat, 202窗口 / wordwolfexp(G13), 1467窗口，旧版评估，位置相关性已弃用
仅存档）**：

| | peak_dist chat↓ | PSR chat↑ | corr chat↑ | peak_dist G13↓ | PSR G13↑ | corr G13↑ |
|---|---|---|---|---|---|---|
| 旧模型(exp4) | 7.48 | 68.36% | 0.191 | 12.88 | 55.40% | 0.255 |
| fifth-run(新) | 8.25 | 64.84% | 0.133 | 12.94 | 54.88% | 0.145 |
| 朴素基线 | 8.68 | 63.54% | **0.193** | 13.06 | 52.69% | 0.248 |

**2026-07-16 复核修正**：位置相关性指标已弃用（见"关键诊断指标"一节）。**只看
peak_dist/PSR 重新读上表，"全面不如旧模型"这个判断被夸大了**——chat 上 peak_dist
8.25 明显好于朴素基线8.68（不是"不如"，是介于基线和旧模型之间），G13 上 peak_dist
12.94 基本和旧模型12.88打平、PSR 54.88%也基本打平旧模型55.40%、明显好于朴素基线
52.69%。当时"全面不如"的结论主要是被相关性数字拖累的（尤其chat上0.133明显低于
基线0.193），peak_dist/PSR 本身其实是个中规中矩、并不差的结果。

**2026-07-16 第二次复核（新标准：t+2重点、train/test分列）**：加了 train 集评估
（278 bag 全量，等距抽样至 29248 窗口，见本节开头说明）——

**t+2（重点步），train / test 分列，peak_dist↓ / PSR_k5@5↑：**

| | train | test[chat] | test[G13] | test[combined] |
|---|---|---|---|---|
| exp4（不参与train评估） | — | 7.81 / 67.19% | 13.06 / 54.55% | 12.49 / 55.83% |
| fifth-run baseline | 9.30 / 63.42% | 8.72 / 62.50% | 13.19 / 54.47% | 12.79 / 54.87% |
| 朴素基线 | 10.24 / 56.95% | 9.03 / 61.98% | 13.45 / 51.56% | 12.89 / 52.94% |

train 上模型清楚超过朴素基线（9.30 vs 10.24），test 三组也都超过朴素基线，
G13/combined 上和 exp4 基本打平（差距在 0.1~0.3 之间），chat 上比 exp4 差一截
（8.72 vs 7.81）——和之前 t+1~t+4 聚合结论一致，train/test 差距不大，没有明显
过拟合迹象，278 bag 全量数据在 t+2 上的表现是"追平旧模型、清楚超过朴素基线"，
不是"没学会"。

**中间发现的一个方法论教训**：训练过程中用未裁剪+合并的旧评估方式看过一次中间结果
（epoch 3 checkpoint），当时新模型合并位置相关性（0.407）看起来超过了旧模型
（0.400）——这个"看起来更好"的结论后来被证明是假象，主要来自 chat_topic3 前90%
可能被旧模型训练时见过、以及把两个难度差异很大的测试域直接合并平均掩盖了真实差距。
**教训：对比旧模型前必须先确认旧模型的训练数据边界，测试集混合多个来源时必须分开
报告，不能只看合并平均数**。

**深挖旧模型训练代码，找到三个真实生效、目前完全没对齐的训练管线差异**（负责人
追问"除了数据还有什么区别"引出的排查，逐条查代码而不是猜）：

1. **lr**：旧模型真实训练用 `lr=1e-6`（`simvp_exp4.json`），third/fifth-run 一直
   用 `lr=1e-3`，相差1000倍——这个之前就知道。
2. **滑窗密度/相位多样性**（比"30Hz vs 2Hz"这个说法更准确的表述）：旧模型的原始
   soundmap/gray_camimg 是按摄像头原生帧率密集存的（`create_dataset.py` 里
   `sm_generator.generate()` 挂在 `if connection.topic == image_topic`下，每个
   摄像头帧都算一次；实测一个269秒的 bag 存了8352帧，约31Hz）。`NPZLoader.
   get_index()` 里候选窗口**起点**是在这个密集数组的**每一帧**上取的
   （`data_num=len(原始密集数组)`），窗口**内部**才按 `skip_frames=30//fps`
   （fps默认2）抽稀到2Hz间隔。也就是同一段录音，旧模型能切出的（高度重叠但起始
   相位不同的）训练窗口数量比我们多约15倍——我们的声图从提取时就只在2Hz的tick上
   算过，根本没有更密的原始数据可切。音频窗口本身两边一致（都是 `audio_max_len`/
   `AUDIO_WIN=160`≈0.46s），clip_len=10/pred_len=4 默认值也一致，不是任务定义
   变了，纯粹是同一份数据被旧模型更充分地重复利用。
3. **数据增强**：旧模型 `train_all_load.py` 对每个 batch 都做 50%概率水平翻转+
   随机缩放裁剪（裁剪面积95%~100%、长宽比0.9~1.11，裁剪后插值回原尺寸，input/
   target 用同一套 flip/crop 参数保持空间对应），`data_aug: true` 在
   `method=='simvp'` 分支外层统一生效，不是死代码。third/fifth-run 明确没做任何
   数据增强。

**排查后确认不是差异、可以放心忽略的**：`load_vae`/`vae_weight`（只在
`method=='vdt'`——扩散模型分支——生效，exp4 的 `method` 是 `'simvp'`，对它是死
配置）；`eta_min`/cosine annealing scheduler（代码里 `scheduler.step()` 从没被
调用过，之前就确认过是死代码，全程恒定lr）；`SimVP` 的 `N_S`/`N_T`——exp4 显式传
`N_S=4, N_T=4`，和我们 `simvp.py` 的默认值本来就一致，不是差异。

### sixth-run：消融 lr / 滑窗密度 / 数据增强（2026-07-14，结论：三者叠加追平旧模型）

只用 chat（3 个 bag，每个按 `train_ratio=0.9` 做时间切分，前90%训练/后10%测试，和
exp4 自己的切分方式完全一致——`sixth-run/train/dataset.py`）做小规模快速消融，
按顺序验证 fifth-run 深挖出的三个真实训练管线差异，每一步固定上一步的胜者：

1. **滑窗密度**（sparse=2Hz原生 vs dense=~30Hz原生密集起点+skip_frames=15内部抽稀，
   见 `sixth-run/extract_chat_dense.py`/`dataset.py`），固定 lr=1e-6（exp4真实lr）、
   无数据增强——**dense 小幅全面胜出**（peak_dist 6.43 vs 6.53，PSR 73.56% vs
   73.23%，相关性 0.297 vs 0.288），且 dense 只需 5 个epoch就追上 sparse 跑满60
   epoch的水平，验证了"同一份录音能更充分复用"这个机制确实有效。
2. **学习率**（dense 密度下，1e-6 vs 1e-3），无数据增强——**lr=1e-3 险胜**
   （peak_dist 6.34 vs 6.43，PSR 73.68% vs 73.56%，相关性 0.294 vs 0.297，一升
   一降但差距都很小）；1e-3 在 dense 数据上只需 1 个 epoch 就达到 lr=1e-6 跑5个
   epoch的水平，之后立刻开始过拟合（train_loss 持续降、test_loss 持续升），epoch1
   就是最优点。
3. **数据增强**（dense + lr=1e-3，复刻 exp4 的 flip+random-crop，见
   `sixth-run/train/augment.py`）——**加了增强后 epoch1 直接追平/略超旧模型**：
   peak_dist 6.08（exp4 6.14）、PSR 74.96%（exp4 74.88%）、相关性 0.306（exp4
   0.313，非常接近）。同时增强明显减轻了过拟合速度（epoch2~4 的下滑比消融2的
   同期缓和很多，虽然 epoch1 仍是最优点，之后仍会过拟合）。

**这是整个项目 first-run 以来第一次有新训练的模型在 peak_dist/PSR 上追平甚至
略微超过旧模型，位置相关性也非常接近**。三个差异（滑窗密度+lr+数据增强）叠加
起来，基本解决了 third/fourth/fifth-run 一直没能突破的"模型学不会跟踪位置"问题。

**补充测试（负责人要求）：数据增强能不能单独在 sparse 数据上就够，不需要
dense？**——sparse + lr=1e-3 + 数据增强（不做密集提取），early stopping 停在
epoch11：**peak_dist 6.14，和 exp4 完全打平；PSR 74.88%，和 exp4 完全打平；
位置相关性 0.319，反超 exp4 的 0.313**——是 sixth-run 里综合最好的一次结果，
比 dense+lr=1e-3+增强（peak_dist 6.08/PSR 74.96%/相关性 0.306）更好地突破了
相关性这个最关键指标，而且完全不需要密集提取。**结论修正：dense 滑窗密度单独看
确实有小幅正贡献（消融1），但一旦有了 lr=1e-3 + 数据增强，dense 就不是必需的了
——lr+增强这两者才是真正把模型从"学不会跟踪"拉到"追平旧模型"的主因。**

这对下一步意义重大：**把这些修正搬到 fifth-run 的 278 个 bag 全量数据上重新跑
一次时，不需要为全部数据做~15倍代价的密集提取**，只需要把 lr 从 1e-3（本来就是
这个值，不用改）加上数据增强（`sixth-run/train/augment.py`，直接复用）应用到
现有的 2Hz 数据上即可，成本低很多。

**2026-07-16 补充：6 个 checkpoint 的 train/test t+2 分列**（新标准，dense
train集87559窗口等距抽样至29184，sparse train集5797窗口全量）：

| checkpoint (train密度) | train peak_dist↓ | train PSR↑ | test peak_dist↓ | test PSR↑ |
|---|---|---|---|---|
| exp4（不参与train评估） | — | — | 6.37 | 73.85% |
| ablation1_sparse_oldlr | 7.03 | 70.80% | 6.40 | 74.18% |
| ablation1_dense_oldlr | 7.01 | 70.48% | 6.32 | 74.34% |
| ablation2_dense_newlr | 6.42 | 72.93% | 6.30 | 74.01% |
| ablation3_dense_newlr_aug | 6.74 | 71.58% | 6.11 | 74.67% |
| extra_sparse_newlr_aug | 6.56 | 72.24% | 6.22 | 74.67% |
| extra_sparse_newlr_noiseratio | 6.56 | 72.38% | 6.14 | 75.00% |
| 朴素基线(sparse train/test) | 7.45 | 68.32% | 7.09 | 71.05% |
| 朴素基线(dense train) | 7.55 | 67.80% | 同上 | 同上 |

t+2 上六个 checkpoint 的 test 表现都追平或略超 exp4（6.11~6.40 vs exp4 的
6.37），train 上也都清楚超过朴素基线，train/test 数值接近，没有过拟合迹象——
和 t+1~t+4 聚合结论一致，extra_sparse_newlr_noiseratio 在 t+2 上 test PSR
75.00% 是六者中最高的。

**第二次补充测试（负责人要求）：设计一个不同机制的新数据增强，看效果是不是
flip+crop 这一种技巧凑巧成功的**——新方案（`augment_batch_noise_ratio`）不用
任何空间几何变换（明确排除了旋转：声图-摄像头像素映射是麦克风阵列固定物理几何
标定出来的，旋转会破坏这个映射、制造不真实的样本，这一点和 flip 不同——flip 是
镜像对称，物理上仍然自洽），改用两种信号/通道扰动：①`sm_ratio` 抖动（数据集构建
时固定烘焙的 0.5 混合比例，训练时在每个 batch 重新按 0.3~0.7 随机比例重新混合）
②高斯噪声（重新混合后对两个通道加小幅噪声，只作用于输入，不动 target）。
sparse + lr=1e-3 + 这个新增强，early stopping 停在 epoch9：**peak_dist 6.11、
PSR 74.92%、相关性 0.316——三项全部追平或超过 exp4**（exp4: 6.14/74.88%/0.313），
和 flip+crop 的结果（6.14/74.88%/0.319）幾乎打平，谁更好在噪声级别，说不出显著
差异。**结论：追平旧模型不依赖某一种特定的增强技巧（flip+crop）凑巧奏效，换成
完全不同机制的增强（信号扰动而非空间几何扰动）效果相当——数据增强这个正则化
机制本身配合 lr=1e-3 才是关键，具体用哪种增强不是决定性的**，这让"lr+增强能解决
问题"这个结论更稳健。

**只在 chat 这个小规模测试床上验证过**，下一步就是在全量数据上验证能不能推广，
不是 chat 单一场景的巧合。完整逐步数字见 `archive-runs/old-runs-1/sixth-run/RESULTS.md`。

### seventh-run：把 sixth-run 的配方搬到全量数据（2026-07-14/15，结论：没能推广）

sparse + lr=1e-3 + `noise_ratio` 数据增强（sixth-run 在 chat 上的最佳配方），原样
套到 fifth-run 的 278-bag 全量训练集/同一测试集（chat_topic3 最后10% + G13
game3-6）上——**结果没能复现 chat 上的成功**：60 epoch 上限，实际 epoch1 就是
最优点，之后10个epoch（patience=10）全部在退化，early stopping 停在 epoch11。
最终（epoch1 checkpoint）三项指标全面不如旧模型，chat/G13 分别看时位置相关性
甚至低于朴素基线（chat: 0.138 vs 朴素0.193；G13: 0.195 vs 朴素0.248），和 chat
小测试床上"全面追平/超过旧模型"的结果反差很大。

**候选原因（还没验证，留给下一步排查）**：278-bag 数据集每个 epoch 约 7318 个
batch（234194 windows/32），是 chat 数据集（181 个 batch/epoch）的 ~40 倍——
lr=1e-3 在这个规模下一个 epoch 内的梯度更新次数暴增，很可能在 epoch1 跑完之前
（甚至前半程）就已经越过了真正的最优点，只是我们只在 epoch 边界做 checkpoint/
早停判断，粒度太粗，抓不到 epoch 内部的最优时刻——third-run/fifth-run 也都是
"早停点异常靠前"（分别是 epoch2、epoch3，相对 60 epoch 的预算都很早），这个模式
是一致的，不是 seventh-run 独有的新问题。另一个可能：`noise_ratio` 增强的强度
（噪声标准差0.03、ratio抖动范围0.3~0.7）是针对 chat 这种小/同质数据集调的，278
个bag 本身已经足够多样，增强可能是冗余甚至有害的正则化，不能照搬同一套强度。

下一步方向（未决定，需要负责人判断优先级）：①更细粒度的 checkpoint/评估（比如
每 N 个 batch 而不是每个 epoch）来抓住 epoch 内部的最优点；②重新调数据增强强度
或者在全量数据上干脆不用增强，只测 lr 的效果；③降低 lr 让每个 epoch 内的更新量
更平缓。

**追加排查（2026-07-15，负责人要求）：数据集大小 vs 数据集成分，哪个才是真正
的变量？** 用 WordWolfExp（74 个训练 bag，G13 测试 bag 除外）+ chat（2 个训练
bag）这个中等规模子集（76 个 bag，约1279 batch/epoch，明显少于278-bag的7318，
但远多于sixth-run纯chat的181）重跑，同一套配方（sparse+noise_ratio增强），
先用 lr=1e-3，再把 lr 降到 1e-4 对比：

| | chat相关性 | G13相关性 | 合并相关性 |
|---|---|---|---|
| 旧模型(exp4) | 0.191 | 0.255 | 0.310 |
| wordwolf_chat, lr=1e-3 | 0.104 | 0.183 | 0.274 |
| wordwolf_chat, lr=1e-4 | 0.132 | 0.188 | 0.273 |
| 朴素基线 | 0.193 | 0.248 | 0.290 |

两个变体都在 epoch1~4 就是最优点（lr=1e-4 把最优点从 epoch1 推迟到 epoch4，
早停从 epoch11 推迟到 epoch14，但最终指标只有边际改善），和 278-bag 全量数据
表现出**同样的失败模式**。**这排除了"单纯数据量太大"这个解释**（76个bag/1279
batch每epoch 远小于278个bag，问题依然存在）**，也基本排除了"单纯lr太高"**（降
10倍只有边际改善）。真正的差异变量指向 **WordWolfExp 数据本身**——sixth-run
纯 chat（3个bag）能训练成功追平旧模型，一旦混入 WordWolfExp（无论是74个还是
276个）就失败，这和 third-run 更早的发现（exp4 在多个 WordWolfExp/GRP_meeting
场次上比 third-run 自己训练的模型更稳定，"训练数据多样性可能比数量更重要"）
方向一致，但这次是更直接的证据：**不是"WordWolfExp不够多样"，而是"当前这套
训练配方在 WordWolfExp 这类数据上学不好"，具体是数据本身的什么特性（可能是
声源定位难度更高、或者 exp4 本来就更擅长这类数据）还需要专门排查**。

**第三个 lr:1e-6（旧模型真实 lr，负责人要求）跑满 60 epoch 的结论**：三个 lr 的
合并测试集对比（chat相关性 / G13相关性）——exp4: 0.191/0.255；lr=1e-3:
0.104/0.183（epoch1最优）；lr=1e-4: 0.132/0.188（epoch4最优）；**lr=1e-6:
0.123/0.182（epoch51最优，跑满60未早停）**。**关键发现：lr=1e-6 让训练曲线彻底
变健康**——test_loss 全程单调下降、不过拟合，和 1e-3/1e-4 那种"epoch1~4见顶后
退化"形成鲜明对比，**证实了高 lr 导致的过拟合/不稳定确实是 WordWolfExp"学不好"
的一部分原因**。**但三个 lr 的最终指标几乎打平、都没突破天花板**：位置相关性全部
低于 exp4，chat 上还低于朴素基线（0.193）。lr=1e-6 曲线最漂亮，换来的最终指标并
不比高 lr 更好。**这精确复现了 third-run 早就记录的 lr=1e-6 结论（本文件
chat_topic3 结果表：曲线健康、指标略变、但没超朴素基线），用 WordWolfExp+chat
独立验证了一遍。**

**2026-07-16 复核修正（重要，动摇了下面"七轮总结论"的前提）**：位置相关性已弃用，
去翻 `archive-runs/old-runs-1/seventh-run/RESULTS.md` 原始数据重新核对 peak_dist/PSR（当时
CONTEXT.md 只摘录了相关性数字，peak_dist/PSR 从没写进来过）。**结果和"没能推广"
的结论正相反**——278-bag baseline 和三个 lr 变体，在 G13（WordWolfExp 本域）和
combined 测试集上，peak_dist/PSR **基本追平旧模型、明显超过朴素基线**：

| 配置 | G13 peak_dist↓ | G13 PSR↑ | combined peak_dist↓ | combined PSR↑ |
|---|---|---|---|---|
| 旧模型(exp4) | 12.88 | 55.40% | 12.20 | 57.03% |
| 278-bag baseline | 12.85 | 55.68% | 12.29 | 56.78% |
| wordwolf_chat lr=1e-3 | 12.93 | 55.59% | 12.42 | 56.72% |
| wordwolf_chat lr=1e-4 | 12.77 | 55.89% | 12.28 | 56.67% |
| wordwolf_chat lr=1e-6 | 12.88 | 55.61% | 12.39 | 56.57% |
| 朴素基线 | 13.06 | 52.69% | 12.48 | 54.21% |

四个变体的 G13/combined peak_dist 都和 exp4 差在 0.2 以内、PSR 差在 0.5 个百分点
以内——统计上基本打平，而且清楚超过朴素基线。**只有 chat 这个192窗口的小子集上
exp4 明显领先**（peak_dist 7.48 vs 新模型 8.3~8.7），这个差距被之前"三域分开报告
但结论按相关性下"的写法放大成了"全面没能推广"。**当时"没能推广"/"WordWolfExp
这类数据学不好"的判断，主要证据来自相关性（chat 0.104~0.138 vs 基线0.193，G13
0.182~0.195 vs 基线0.248），peak_dist/PSR 从未真正支持这个结论。** 换句话说，
"调 lr/数据量/多样性/增强这套配方在 WordWolfExp 上学不好"这个说法本身可能是
不准确的——peak_dist/PSR 显示它在 WordWolfExp(G13)域上其实学得还行。

**2026-07-16 第二次补充：train/test t+2 分列**（新标准；baseline训练集278bag/
234194窗口、wordwolf_chat系列训练集76bag/40946窗口，均等距抽样至约2~3万窗口，
见本节开头说明）：

| checkpoint (peak_dist↓/PSR↑) | train | test[chat] | test[G13] | test[combined] |
|---|---|---|---|---|
| exp4（不参与train评估） | — | 7.81/67.19% | 13.17/54.17% | 12.49/55.83% |
| baseline (278bag) | 9.62 / 62.69% | 8.65/64.06% | 13.17/54.44% | 12.61/55.65% |
| wordwolf_chat lr=1e-3 | 10.45 / 64.17% | 9.00/64.06% | 13.35/54.37% | 12.79/55.59% |
| wordwolf_chat lr=1e-4 | 9.98 / 64.85% | 9.08/61.46% | 12.96/55.76% | 12.50/56.37% |
| wordwolf_chat lr=1e-6 | 10.23 / 64.44% | 8.71/64.06% | 13.19/54.51% | 12.63/55.71% |
| 朴素基线(baseline train) | 10.24 / 56.95% | 9.03/61.98% | 13.49/51.46% | 12.89/52.94% |
| 朴素基线(wordwolf_chat train) | 11.33 / 59.61% | 同上 | 同上 | 同上 |

四个变体 train 上都清楚超过各自的朴素基线，test 三组也都和 exp4 基本打平、超过
朴素基线，train/test 差距不大——t+2 单独看和 t+1~t+4 聚合结论一致，**没有证据
支持"WordWolfExp 数据训练配方学不好"这个说法，也没有过拟合迹象**。

**七轮总结论（原表述，前提已被上面修正削弱，仅供参照）**：调 lr / 数据量 / 多样性 /
数据增强这些外围旋钮，最多让训练过程变健康（lr=1e-6 的健康曲线），但**动不了位置
相关性这个天花板**。天花板在别处——最可能是 loss 的监督目标（七轮都是像素重建，
从没直接监督峰值位置）。eighth-run 就是攻这个（见"当前开放问题"一节）。**这个
"天花板"叙事本身是用位置相关性定义的，该指标已弃用——eighth-run 存在的原始动机
需要重新审视，见文件末尾"下一步方向"。**

完整逐步数字见 `archive-runs/old-runs-1/seventh-run/RESULTS.md`。

### eighth-run：换 loss 监督目标 + 更长窗口（2026-07-15/16，旧PC上的最后一轮，已归档）

**2026-07-18 更新**：迁移到新PC时已归档到 `archive-runs/old-runs-1/eighth-run/`
（原顶层 `eighth-run/`，见文件开头"2026-07-18 迁移到新PC"一节）。以下是归档前
的原始记录，不改动；C follow-up 配方是新PC `run-1/` 的起点，见文件末尾。动机见
"当前开放问题"一节：
七轮都在调外围旋钮（lr/数据量/多样性/增强/loss形状），位置相关性天花板没动过；
唯一没变过的是 loss 的**监督对象**（七轮都是对整张声图做像素重建 MSE/BCE/KL，
而 care 的是峰值位置）。eighth-run 攻这个,两个实验分开做、都在 chat 上小测。

**代码位置**：`archive-runs/old-runs-1/eighth-run/train/`（`dataset.py` 精简成 chat-only sparse；
`losses.py` 是核心新增——`soft_argmax_loss`/`combined_loss`；`train.py` 加了
`--loss {mse,softargmax,combined}` 和 `--clip-len`，**早停监控 test peak_dist 均值
而非 test_loss**，让不同 loss 的对照公平）；评估
`archive-runs/old-runs-1/eighth-run/evaluation/compare_ablations.py --run-name <name> --clip-len <10|20>`
（exp4 是固定10帧模型，clip20 时喂它输入的最后10帧 `x[:,-10:]`）。

**四条实验命令**（cd 到 `archive-runs/old-runs-1/eighth-run/train/`，其余超参固定在 sixth-run chat 最佳
配方 lr=1e-3+noise_ratio 增强）：
```
# 实验A：loss 监督目标
python -u train.py --loss mse        --clip-len 10 --run-name A_mse_control
python -u train.py --loss softargmax --clip-len 10 --run-name A_softargmax
# 实验B：输入窗口长度（对照复用 A_mse_control，即 clip_len=10 那个）
python -u train.py --loss mse        --clip-len 20 --run-name B_clip20
```
每个跑完 `compare_ablations.py --run-name <name> --clip-len <10或20>` 出对比报告
（追加到 `archive-runs/old-runs-1/eighth-run/RESULTS.md`，含 exp4 和朴素基线）。chat 每 epoch ~73s。

**soft_argmax_loss 是什么**（`losses.py`）：预测声图归一化成空间概率
`p=softmax(P/τ)`，算期望坐标 `row=Σi·p_ij, col=Σj·p_ij`，对真值峰值 `(i*,j*)`
求距离² `(row-i*)²+(col-j*)²`（t+1~t+4 各算再平均）。可导,直接监督峰值位置。
冒烟测试过：峰值对准→loss≈0，偏10格→loss=100（正好10²），梯度正常。

**实验A结论（2026-07-16，负结果，已定论）**：`A_mse_control` 早停epoch18，
peak_dist_mean=6.19，PSR_agg=74.71%，位置相关性0.298——基本追平exp4(6.14/
74.88%/0.313)，符合预期。`A_softargmax` 三个学习率都跑了（1e-3/1e-4/1e-6），
**全部不敌MSE对照组，没有一个突破位置相关性天花板**：

| 配置 | peak_dist均值 | PSR_agg | 位置相关性均值 |
|---|---|---|---|
| A_mse_control | 6.19 | 74.71% | 0.298 |
| baseline(repeat-last) | 7.14 | 70.97% | 0.238 |
| A_softargmax lr=1e-3 | 9.11 | 72.62% | 0.259 |
| A_softargmax lr=1e-4 | 7.36 | 66.86% | 0.269 |
| A_softargmax lr=1e-6 | 9.09 | 20.19% | 0.242 |

lr=1e-3原始训练不稳定（PSR中途震荡）；lr=1e-4三者中最好但仍全面不敌MSE；
lr=1e-6曲线最"健康"（peak_dist单调下降）但PSR从51%一路崩到epoch35的5.63%——
**确认是塌陷到低方差固定预测点**：平均距离数字尚可，但几乎不落入k=5成功窗口。
调小lr没能治好这个问题，只是让塌陷过程更平滑、更容易被误读为"在正常训练"。
**结论：soft-argmax直接监督峰值位置这个loss设计，在当前架构/数据规模下不仅没
打破天花板，反而比像素重建MSE更容易诱导塌陷——三个学习率贯穿验证，判定为
负结果，不再追加实验。** MSE仍是目前最优loss。详细数字见`archive-runs/old-runs-1/eighth-run/RESULTS.md`。
**（2026-07-16复核：这个结论不依赖位置相关性也成立——三个lr的peak_dist全部比
朴素基线还差，lr=1e-6的PSR崩溃是直接从训练曲线本身看出来的，独立证据充分，
结论不变。）**

**实验B结果（2026-07-16，原判"正向信号"，复核后改判"不确定"）**：`--loss mse
--clip-len 20 --run-name B_clip20`（其余同sixth-run chat配方），显存不够默认
bs=32（clip翻倍后模型参数38M vs clip10的13M），改`--bs 16`才跑通。早停epoch17，
最优peak_dist_mean=6.20@epoch7，PSR_agg=74.57%。**2026-07-16复核**：当时"正向
信号/目前最好结果"的判断完全建立在位置相关性（0.318 vs A_mse_control的0.298）
上，该指标已弃用。**只看peak_dist/PSR，B_clip20(6.20/74.57%)和A_mse_control
(6.19/74.71%)几乎是同一个数字**，统计上分不出高下。**修正结论：窗口从5s拉长到
10s，在还信得过的指标上看不出改善，是个中性结果，不是"目前最好的结果"。**

**follow-up：加WordWolfExp G1+G2训练、G3 game3-6 held-out测试（2026-07-16，
原判"好坏参半/疑似塌陷"，复核后改判"正向泛化结果"）**：负责人要求验证clip_len=20
的窗口收益能否推广到真正跨组泛化。改动`dataset.py`加`MixedWindowDataset`
（通用化：`split_bags`按train_ratio时间切分如chat，`full_train_bags`/
`full_test_bags`整段专属训练或测试）+`--dataset chat_g1g2_g3`开关；
`compare_ablations.py`同步支持，**chat和G3两组分开报告，不合并平均**（吸取
fifth-run的教训）。G3的game2_Video/interview两个bag按负责人明确要求完全不用
（不同于fifth-run对G13的处理，那次留在训练集接受同组泄漏风险——这次选更干净
的切分）。配方同B_clip20（loss=mse, clip_len=20, lr=1e-3+noise_ratio增强），
早停epoch13@best epoch2。run名`C_g1g2_train_g3_test`。

结果分两组（位置相关性列已弃用，仅存档参考不作为结论依据）：
| 测试组 | peak_dist↓ | PSR_agg↑ |
|---|---|---|
| chat（581窗口，含在训练集90%切分里） 新模型 | 6.34 | 73.96% |
| chat exp4 / baseline | 6.14 / 7.14 | 74.87% / 71.05% |
| G3 game3-6（1442窗口，完全held-out） 新模型 | **10.71** | **63.72%** |
| G3 exp4 / baseline | 12.94 / 11.98 | 55.38% / 57.97% |

chat上和B_clip20一致（逼近exp4）。G3上新模型peak_dist/PSR双双清楚超过exp4和
朴素基线。**2026-07-16复核**：原先"看着好但可能塌陷到保守预测"的怀疑，唯一
依据是位置相关性在G3上低于基线（0.181<0.281），该指标已弃用，**这个怀疑没有
独立证据支撑，予以撤回**。**修正结论：这是目前项目里第一次在真正没见过的
WordWolfExp组上，peak_dist+PSR都全面超过旧模型和朴素基线的正向跨组泛化结果**
——chat+G1+G2训练、clip_len=20 这套配方值得作为下一步的基础配方，而不是被
怀疑塌陷搁置。（严谨地说：撤回怀疑不等于证明没塌陷，如果想彻底确认，可以做
可视化诊断直接看预测输出是否收缩成固定点，见"下一步"。）

**实验D结果（2026-07-16，负结果，独立于相关性，结论不变）**：测试"把模型容量
集中在单一近期时间点(+1s=t+2)是否有质的提升"——改`dataset.py`加`pred_offsets`
参数（跳过中间帧，只在指定offset构造窗口/target，不是训练完4步再切片），
`train.py`加`--pred-offsets`，`compare_ablations.py`同步支持（exp4从固定4步
输出里切出对应offset做公平对比）。配方同C（chat+G1+G2训练/G3测试，clip_len=20,
lr=1e-3+noise_ratio），`--pred-offsets 2`，run名`D_horizon1s`，早停epoch15@best
epoch5。

| | chat peak_dist(t+2)↓ | chat PSR(t+2)↑ | G3 peak_dist(t+2)↓ | G3 PSR(t+2)↑ |
|---|---|---|---|---|
| exp4 | 6.25 | 74.31% | 13.00 | 55.28% |
| C（联合4步，取t+2切片） | 6.36 | 73.96% | 10.97 | 62.57% |
| D（专门只学t+2） | 6.36 | 73.96% | 11.02 | 62.71% |
| baseline | 7.11 | 71.18% | 11.65 | 59.65% |

D和C的t+2切片几乎逐位重合（chat上完全一样，G3上差0.05/0.14个百分点，都在噪声
范围内）。**结论：把模型容量集中在单一时间点，对该时间点的精度没有质的提升**
——"联合预测4步稀释了近期精度"这个假设不成立，瓶颈不在输出头分摊注意力，更可能
是输入信息本身对该时刻位置的可预测性上限，或架构/表征能力问题。

**2026-07-16 补充：全部7个checkpoint的 train/test t+2 分列**（新标准；A/B系列
训练集chat-only 5792~5760窗口全量，C/D训练集chat+G1+G2共11936~11968窗口全量，
D只预测t+2这一步，本来就没有聚合掩盖的问题）：

| checkpoint (peak_dist↓/PSR↑) | train | test[chat] | test[G3] |
|---|---|---|---|
| A_mse_control | 6.62 / 72.13% | 6.17 / 75.00% | — |
| A_softargmax | 9.53 / 69.39% | 9.33 / 73.03% | — |
| A_softargmax_lr1e-4 | 7.59 / 66.44% | 6.97 / 69.08% | — |
| A_softargmax_lr1e-6 | 9.80 / **18.80%** | 9.72 / **16.94%** | — |
| B_clip20 | 6.30 / 73.68% | 6.35 / 74.13% | — |
| C_g1g2_train_g3_test | 9.27 / 64.83% | 6.36 / 73.96% | 10.97 / 62.57% |
| D_horizon1s | 9.35 / 65.02% | 6.36 / 73.96% | 11.02 / 62.71% |
| 朴素基线(A/B, chat-only train) | 7.45 / 68.32% | 7.09 / 71.05% | — |
| 朴素基线(C/D, chat+G1+G2 train) | 10.27 / 60.31% | 7.11 / 71.18% | 11.62 / 59.72% |
| exp4(G3, 仅test，来自compare_ablations.py历史记录) | — | — | 12.96 / 55.42% |

**A_softargmax_lr1e-6 的塌陷在 train 集上同样出现**（PSR 18.80%，比 test 的
16.94%还高一点但同样崩溃）——确认是模型本身塌陷到低方差固定预测，不是"只在
没见过的数据上崩"，train/test 一致说明这是优化过程的问题，不是过拟合/欠拟合的
常规模式。**C_g1g2_train_g3_test 在 train 集上也清楚超过朴素基线**（9.27 vs
10.27），train/test 差距不大，进一步支持"C 的 G3 正向结果不是靠塌陷到保守
预测撞出来的"这个判断（呼应下面对"疑似塌陷"怀疑的撤回）。

**下一步（2026-07-16更新）**：C实验的正向结果（chat+G1+G2训练+clip_len=20，
G3上peak_dist/PSR全面超过exp4/基线）目前是eighth-run里最值得延续的方向——建议
巩固这套配方（可能进一步扩大训练组、或验证在更多held-out组上是否稳定），而不是
继续在loss形状（已证负）或窗口长度单独效应（已证不确定）上纠结。另外，"七轮
总结论"驱动eighth-run立项的原始前提（"调外围旋钮动不了位置相关性天花板"）本身
建立在已弃用的指标上，seventh-run复核后peak_dist/PSR其实基本追平旧模型（见
seventh-run小节复核说明）——eighth-run"换loss监督目标"这整个方向的必要性值得
重新评估，可能"模型学不好"这个问题本身被高估了。

## 数据集（详见 `preprocessing/DATA_REPORT.md`）

**2026-07-12/13 PSSPData 两轮整盘重组 + 全量重跑**：负责人把所有数据集整理到
统一的 `/media/chen/Extreme SSD/PSSPData/` 下（`WordWolfExp` 并入，`Experiment0312`/
`Experiment1126`/`Testrun0420` 合并进 `WordWolfExp/` 内部，`ProjectMobileRobot_3f`
被更大的 `ATR_teleoperation/data_RIKEN_3f` 取代、新增 `data_RIKEN_1F`），全部
集合统一重新提取。**当前 `train-data/` 283 个 npz（241,475 ticks ≈ 33.54 小时），
没有 `train-data-aux/`**——负责人明确要求不做默认排除判断，全部进 `train-data/`，
后续再判断哪些该挪走。具体每个数据集的规模见 `DATA_REPORT.md` 的两张表。

QC 视频（`soundmap-videos/`）和 `video-generator/bag2video.py` 完全一致的设计：
主画面（声图叠加摄像头，若有 `/head/head_box`+`/room2_audio/vad` 话题则叠加
head-box/speaking-box/4-label 标注——负责人确认 WordWolfExp 下所有 bag（G-前缀、
EXP-前缀、testrun_0420_*）都有这两个话题）下方拼接一条滚动面板，显示 room1
音量包络+silero-VAD 语音段，缺 4-label 话题的集合优雅降级为纯 VAD 条。

**两个真实 bug，均已修复**：①超长音频先拼接全16声道数组再转 float32 才降混
单声道，峰值内存能到 ~33GB——改成逐块降混再拼接；②`cv2.VideoWriter` 从不写
音轨，之前生成的 QC 视频全部是哑的——改成先写无声临时视频，再用 ffmpeg 把
room1 mono 音频（+30dB 增益，和 bag2video.py 一致）混流进去（`_mux_audio()`）。

## 代码/工作流约定

- 每轮 run 自包含，只共享 `train-data/`（npz池）——参见"仓库结构"。
- `preprocessing/build_dataset.py`：硬编码 `JOBS` 注册表（一个 collection 一条），
  `--job <label>` 跑单个/不传跑全部，同时产出 npz 和 QC 视频
  （`soundmap-videos/{name}.mp4`：主画面+下方 VAD/label 滚动面板，完全照搬
  `video-generator/bag2video.py`/`room1_vad.py`/`labeling.py`，见"数据集"一节）。
  视频 10Hz 摄像头/2Hz 声图解耦（声图生成器是唯一昂贵步骤，不多跑，10Hz 只是
  摄像头子帧更流畅）。单个 bag 的 video-only 重跑（npz 已存在只补视频）用独立
  subprocess 隔离（`--video-only-bag` CLI 分支），避免大 bag 之间内存跨 bag 累积。
- 每轮的 `RESULTS.md` 由对比脚本自动追加，不手动整理，历史记录不覆盖。
- `compare_old_new.py`（third-run 版）支持 `--run-name`（选 checkpoint）和
  `--test-bags`（覆盖默认测试集，用于诊断性对比，非正式评估）两个参数，同一份
  代码复用于不同对比场景。
- 不留临时冒烟测试脚本在工作区里，用完即删。

## 踩过的坑（教训，避免重复犯）

- **不查代码就下结论会出错，出过两次**：①最初以为旧训练用原始 0~160 量级当
  target（依据未验证的脚本快照），后来查实际部署代码才发现是 exp 变换；②最初
  说旧代码 train/test 有帧级重叠，其实已经处理好了，真正的问题是"同对话内切分"
  这种更隐蔽的泄漏。**教训：翻旧代码找证据，优先信实际部署/被使用过的代码路径，
  不信没被证实过的脚本快照。**
- **外部持续增长的数据源不能只信第一次的目录扫描**：`Experiment1126` 漏了一半
  （只扫到 `EXP1_*` 没扫到 `EXP2_*`），`ProjectMobileRobot_3f`/`riken_3f`/
  `Testrun0420` 是后来才上传、复查才发现的。
- **可视化对比必须共享色阶，不能每张图各自归一化**——第一版 per-tile 独立
  min-max 归一化把真实的锐利度差异抹平了，负责人一眼就看出图上不对。
- **`x or fallback` 不能用在 numpy 数组上**——数组的真值判断是歧义的，会报
  `ValueError: truth value of an array...`，要用显式 `is None` 判断。
- **配置文件里的字段不代表真的在用**——旧模型 config 里的 `eta_min`（cosine
  annealing scheduler 参数）对应的 scheduler 代码其实是注释掉的，旧模型实际
  训练全程是恒定 lr。**要求"复现旧配置"时，先看训练代码有没有真的用这个字段，
  不要只看 config 文件。**
- **磁盘 I/O 可能被其他并发任务拖慢**：处理超大 PSSPData bag 时遇到过和自己的
  调查脚本、其他并发进程抢 I/O 的情况，教训是大文件查询要避免全表扫描（用
  `LIMIT`/`OFFSET` 而不是拉全部数据）。
- **wolf 虚拟环境**：本项目所有 Python 命令必须在 `wolf` 虚拟环境下跑，不要用
  系统/其他环境（wolf 环境实测比其他环境明显更快，可能是 torch/cuDNN 版本差异）。
- **新写光流/warping 类架构，残差累积必须限幅，且要有平滑正则**——不限幅会数值
  爆炸，限幅了但 lr 太大还会饱和死锁（loss 冻结不代表收敛，可能是梯度死区），
  没有平滑正则会出现"清晰但形状撕裂"的 warping 伪影，肉眼看比看 loss 数字更容易
  发现这类问题（细节见 fourth-run 一节）。
- **两个同名文件在 sys.path 上会互相覆盖**：`fourth-run/train/train.py` 和
  `archive-runs/old-runs-1/third-run/train/train.py` 撞名，`sys.path.insert` 的顺序决定
  `import train` 到底拿到哪一份——写跨 run 引用代码时要注意顺序，或者干脆避免
  跨目录 import 同名模块。
- **降混/大数组操作要按块处理，不要先拼接全量数组再转类型/降维**：
  `VideoPanel.__init__` 原来是 `np.concatenate([...16声道...]).astype(float32)
  .mean(axis=1)`，对 2.15 小时的超长 bag 峰值内存到 ~33GB，直接 OOM 崩溃过一次；
  改成每个消息块先降混再拼接，峰值降到接近最终结果大小。**这台机器 swap 空间
  充裕（47Gi），负责人明确表示"available RAM 变低不用紧张，可以用 swap"——
  只要不是内核真的 OOM-kill 或 swap 也快耗尽，不需要为了"available 低"就抢先
  杀进程。**
- **`index.csv` 只在整个 job 循环跑完才写一次，进程中途被打断会丢索引行（不丢
  npz）**：`process_job()` 的 `write_index()` 在 `todo_full` 循环外面，只调用一次。
  这次 PSSPData 重跑中途被打断过，导致 `GRP_meeting` 44 个 npz 里有 39 个已经
  落盘但 `index.csv` 没记录——如果训练代码靠 `index.csv` 做 split，这些 bag 会
  被静默忽略。已经从每个 npz 自带的 `tick_ts` 反推 `dur_s`/`n_ticks` 手动补全，
  但代码本身没改（更彻底的修复是每个 bag 处理完立即增量写 index，不是攒到最后）。
  **教训：中途可能被打断的批处理任务，产出索引应该增量持久化，不要攒到最后一次性
  写。**

## 当前开放问题 / 下一步方向

**2026-07-16 大幅更新**：eighth-run 的实验A/B/C/D 都已跑完（见 eighth-run 小节），
同时**位置相关性指标已弃用**（负责人判断其计算方式不合理，见"关键诊断指标"一节），
往后只用 peak_dist/PSR。这次复核也发现 fifth-run/seventh-run 当年"没能推广"/
"全面不如旧模型"的判断很大程度是被相关性数字带偏的，peak_dist/PSR 本身其实基本
追平旧模型（见对应小节的复核说明）。**下面是复核后的当前状态**：

**2026-07-16 第二次更新（评估标准再收紧）**：负责人进一步明确了往后的评估口径
——①t+1~t+4 不再合并看，**逐步分开报告，t+2 是重点参考步**，其余步骤仅作辅助；
②**train 集和 test 集指标都要报**，只看 test 无法诊断过拟合。已按此标准给
`archive-runs` 全部 8 轮、25 个 checkpoint 补了 train 集评估（新增
`evaluation/eval_train_test.py`，见"全部模型/run 的结果一览"一节每个子节新增的
t+2 train/test 表）。**结论层面这次复核没有推翻任何已定论**——各 run 在 t+2 上
单独看和 t+1~t+4 聚合结论方向一致，train/test 差距普遍不大（第一个真正意义上的
"过拟合"信号只在 fourth-run DMVFN 和 eighth-run A_softargmax_lr1e-6 这两个已经
判负的 checkpoint 上出现，都是 train 和 test 一起崩，不是经典的"train 好 test
差"模式）。副产品：过程中发现并修复了 `archive-runs/old-runs-1/eighth-run/train/dataset.py` 和
`compare_ablations.py` 里一个真实 bug——归档进 `archive-runs/` 后路径深度多了一层，
`DATA_DIR`/`ACCESS_MODEL_DIR` 没跟着改，导致这两个脚本在归档后其实无法运行
（历史 RESULTS.md 里的记录都是归档前留下的）。

- **下一步优先级（2026-07-16 会话末尾定的顺序）**：
  1. 延续C实验配方（chat+G1+G2训练、clip_len=20）——扩大训练组数量（比如加
     G4/G5等）再验证G3那种跨组泛化收益是不是稳定的，不是偶然一次。
  2. ~~给C的预测结果做一次可视化诊断，直接看输出有没有塌陷成固定点~~——
     **部分已由 train 集评估回答**：C_g1g2_train_g3_test 在 train 集上 t+2
     peak_dist/PSR（9.27/64.83%）清楚超过朴素基线（10.27/60.31%），不是塌陷到
     保守预测的模式（塌陷会导致 train 上也逼近基线甚至更差，参照
     A_softargmax_lr1e-6 的真实塌陷案例）。如果还想更彻底确认，仍可做一次
     直接可视化（看输出热力图形状），但已不是最优先级。
  3. loss监督目标（实验A，两次独立证据判负）和单独的窗口拉长（实验B，已不确定）
     这两条不用再继续投入。
  4. 如果第1点要做大规模验证（比如喂更多bag），**建议先排查下面的"早停/
     checkpoint粒度太粗"这个老问题**——third/fifth/seventh-run都在数据变大后
     早停点异常靠前，不解决这个，扩大数据规模很可能重演"epoch1就早停"。
- **eighth-run 结论汇总（都已跑完，见 eighth-run 小节完整数字）**：
  - 实验A（soft-argmax峰值位置loss，三个lr）：**负结果，独立于相关性也成立**——
    peak_dist全面比朴素基线差，lr=1e-6出现PSR从51%崩到5.63%的塌陷，直接从训练
    曲线看出来，不需要相关性佐证。
  - 实验B（clip_len 10→20，chat-only）：peak_dist/PSR和clip_len=10基本打平，
    **不确定/中性结果**，不是之前以为的"最佳结果"（那个判断当时靠的是相关性）。
  - C follow-up（chat+G1+G2训练/G3 held-out测试，clip_len=20）：**目前最值得
    延续的正向结果**——G3上peak_dist/PSR全面超过exp4和朴素基线，是项目里第一次
    在真正没见过的WordWolfExp组上做到这点。之前怀疑的"塌陷"没有独立证据，已撤回。
  - D（只预测+1s单一时间点）：**负结果**——和C联合4步预测切片几乎逐位相同，
    证明"稀释近期精度"的假设不成立，瓶颈不在输出头分摊注意力上。
  - **下一步最有希望的方向**：延续C的配方（chat+G1+G2训练、clip_len=20），
    可能的路子——扩大训练组数量再验证G3这类正向结果是否稳定；对新模型的预测
    做可视化诊断，直接确认有没有塌陷到固定点（弥补相关性指标弃用后留下的空白）；
    重新评估eighth-run"换loss监督目标"这整个方向是否还有必要（premise已被削弱，
    见下一条）。
- **eighth-run 存在的原始前提需要重新评估**："七轮回顾发现位置相关性天花板，唯一
  没变过的是loss监督对象"这个立项逻辑，本身是用现在已弃用的相关性指标定义的。
  复核后 seventh-run 在peak_dist/PSR上其实基本追平旧模型（差距在噪声范围内），
  "模型学不好WordWolfExp"这个问题可能被高估了。这不代表eighth-run没有产出——
  C follow-up的正向结果依然成立且不依赖相关性——但"必须靠换loss监督目标才能
  突破天花板"这个原始动机站不住了，往后的方向判断应该以peak_dist/PSR为准，不要
  再默认存在一个"天花板"。
- **配套评估提醒**：third-run 诊断表（本文件"补充诊断"一节）显示朴素基线自己的
  相关性在不同场次是 0.10~0.35（这个具体数字现在仅供参考）——但背后的现象本身
  还是成立的：**有些场次下一个说话人位置本来就不好预测，任何模型都难赢朴素基线**，
  评估时最好按场次可预测性分层看，不要只看合并平均。
- **早停/checkpoint 粒度问题（仍未验证，仍值得排查）**：third-run(epoch2早停)、
  fifth-run(epoch3)、seventh-run(epoch1) 的最优点相对各自的 epoch 预算都异常靠前，
  是同一个模式反复出现——现在的训练循环只在 epoch 边界评估/存 checkpoint，数据集
  越大这个粒度越粗糙，可能一直错过 epoch 内部真正的最优时刻。值得做一次按 batch
  数（而不是 epoch数）为单位的评估间隔实验，看早停点是不是能挪到更晚、指标是不是
  能更好。
- KL loss 条件下 `peak_mass` 在早停点之后仍在涨，但当前早停只看 test_loss——
  "早停该监控哪个指标"这个问题还没有处理。
- `Demonstration_Data_nonconv`（非对话片段）"加入训练是否有帮助"这个有对照消融
  还没有做——fifth-run 已经把它混进了 278 个训练 bag 里，还没单独测过去掉它会不会
  更好/更差。
- **旧模型对比基线是否权威还有疑点**：`access-model-train/results/` 目录下还有
  一个更新的 checkpoint `config_simvp_exp4_new_epoch31.pt`（比归档的 `exp4.pt`
  更新，config 相同，代码里被注释掉没启用），一直没有确认过它是不是比现在用的
  `exp4.pt` 更好/更该作为对比基线——目前所有"新旧模型对比"都用的是 `exp4.pt`。
- `sm_ratio` 融合权重目前固定 0.5（像素级加权，不可学习），一直没有作为超参数
  扫过，也没试过做成可学习的融合方式。
