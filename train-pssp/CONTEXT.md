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
  archive-runs/               # 已完成使命的run，只读存档
    first-run/                  # 第1轮：归一化+lr修复验证
    second-run/                  # 第2轮：loss函数消融（MSE/BCE/KL）
    third-run/                   # 第3轮：数据规模+chat泛化测试
  fourth-run/                 # 第4轮：SimVP vs DMVFN架构对比（结论：DMVFN败）
    train/ / evaluation/ / RESULTS.md
```
`archive-runs/` 下的 run 路径深度多了一层，`DATA_DIR`/`ACCESS_MODEL_DIR` 等用
`_HERE.parent.parent.parent` 定位（不是 `.parent.parent`）。

每一轮都是自包含的完整流水线快照（只共享 `train-data/` 这个 npz 池，不共享其它
顶层公共模块），这样任何时候看某一轮结果都对应得上那一轮自己的代码。以后新方案
默认开新的 `Nth-run/` 目录。

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

## 全部模型/run 的结果一览

**汇报硬性要求（负责人定的，必须遵守）**：①朴素基线每次都要一起报，不能只看
模型自己的数字；②t+1~t+4 逐步结果都要给全，不能只给聚合平均。下表为紧凑只列
聚合值，完整逐步数字见各 `RESULTS.md`。

### G2+G6 WordWolf holdout（3522 窗口，first-run/second-run 用的测试集）

| 模型 | 数据 | peak_dist↓ | PSR_k5@5↑ | 备注 |
|---|---|---|---|---|
| exp4（旧） | 未知，不含 WordWolfExp | 11.91 | 60.45% | 训练/测试分布不完全对等 |
| first-run baseline (MSE, lr=1e-3) | WordWolfExp 3组16bag | 10.47 | 66.01% | epoch6早停(共16epoch) |
| second-run MSE | 同上 | 10.38 | 65.85% | peak_mass 0.0111, entropy 6.623 |
| second-run BCE | 同上 | **10.19** | 66.14% | peak_mass 0.0111, entropy 6.343 |
| second-run KL | 同上 | 10.33 | 66.12% | peak_mass **0.0131**, entropy **6.129**（最尖但不是最准） |
| 朴素基线（重复最后一帧） | — | 12.36~12.68 | 57.83~60.54% | |

**结论**：loss 从 MSE 换成 BCE/KL 让预测变"尖"（entropy 降、peak_mass 升），但
**没有转化成定位更准**——peak_dist/PSR 三者几乎打平。用位置相关性排查发现三个
模型（mse 0.237~0.250, bce 0.157~0.164, kl 0.167~0.182）**全部低于"最后一帧位置
预测下一帧位置"的朴素天花板（约0.31~0.33）**，且 MSE 反而跟踪最好——BCE/KL 变尖
是靠更死板地收敛到固定答案换来的，不是真的更准。**这轮拖累定位精度的不是 loss
函数形状，是模型本身没学会利用输入去跟踪位置变化。**

### chat_debate_exp1_topic3（2138 窗口，third-run 的 held-out 测试集，训练集=78个
WordWolf bag + chat 前两段）

| 模型 | peak_dist↓ | PSR_k5@5↑ | 位置相关性↑ |
|---|---|---|---|
| exp4（旧，未训练过 WordWolf/chat） | 8.48 | 63.68% | 0.181 |
| third-run lr=1e-3（早停epoch12,最优epoch2） | 9.17 | 61.73% | 0.161 |
| third-run lr=1e-6（跑满60epoch,未早停） | 9.02 | 62.39% | 0.150 |
| fourth-run DMVFN（架构对比，见下） | 14.86 | 25.00% | 0.027 |
| 朴素基线/朴素连续性 | 9.24 | 60.54% | 0.165 |

**两个 lr 设置的 new 模型，位置相关性都没能超过朴素基线**——lr 降低让训练更平稳
（lr=1e-3 的 test_loss 在 epoch2 就见顶转差，lr=1e-6 全程单调改善没有过拟合迹象），
peak_dist/PSR 略有改善，但没解决核心问题。（lr=1e-6 是负责人要求复现的旧模型
真实训练 lr——查实 `simvp_exp4.json` 确实是 `lr=0.000001`，且训练脚本里对应的
cosine annealing scheduler 代码是注释掉没启用的，`eta_min` 只是死配置字段，旧
模型实际训练全程是恒定 lr。third-run 照此不加 scheduler。）

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

## 数据集（详见 `preprocessing/DATA_REPORT.md`）

**2026-07-12 PSSPData 整盘重组 + 全量重跑**：负责人把所有数据集整理到统一的
`/media/chen/Extreme SSD/PSSPData/` 下（`WordWolfExp` 并入、新增
`chat`/`Testrun0420`/`Demonstration_Data`/`kitchen` 等集合、`GRP_meeting` 从 5
场增到 44 场含一场 2.15 小时超长录音），全部集合统一重新提取。**当前
`train-data/` 210 个 npz（186,193 ticks ≈ 25.86 小时），`train-data-aux/` 暂时
清空**——负责人明确要求这次不做默认排除判断，先全部进 `train-data/`，后续再判断
哪些该挪到 aux（之前版本"摄像头会动的集合路由到 aux"这类判断依据本身仍然成立，
只是暂时没有物理隔离，还在 `train-data/` 里等负责人处理）。

QC 视频（`soundmap-videos/`）这次改成和 `video-generator/bag2video.py` 完全一致
的设计：主画面（声图叠加摄像头，若有 `/head/head_box`+`/room2_audio/vad` 话题则
叠加 head-box/speaking-box/4-label 标注）下方拼接一条滚动面板，显示 room1 音量
包络+silero-VAD 语音段，缺 4-label 话题的集合优雅降级为纯 VAD 条。之前版本只有
"黄色声图叠加摄像头"，没有下方面板，负责人明确指出后重做。

处理过程中发现并修复了一个真实的内存 bug（超长音频先拼接全16声道数组再转
float32 才降混单声道，峰值内存能到 ~33GB）和一处 `index.csv` 记录缺口（`process_
job()` 只在整个 job 循环结束后才写一次 index，进程中途被打断会让已落盘的 npz
永远进不了 index），两处细节都记录在 `DATA_REPORT.md`。

每个集合的场次数、判定理由和最终规模见 `DATA_REPORT.md`，不在这里重复。

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
  `archive-runs/third-run/train/train.py` 撞名，`sys.path.insert` 的顺序决定
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

- **fifth-run 计划**：扩大训练数据，用 **SimVP**（DMVFN 架构对比已败下阵，见上）。
  2026-07-12 PSSPData 重组+全量重跑后，`train-data/` 已有 210 个 bag/25.86 小时
  （远超 third-run 的 78 个 WordWolf bag+2 段 chat），具体用多少、是否要挪一部分
  去 aux，等负责人决定。
- **数据多样性可能比数据量更重要**这个假设目前最值得跟进（样本量还小，只测了
  6 个场次）——现在 `GRP_meeting`（44 场）/`olab_0630`/`chat`/`Testrun0420`/
  `Demonstration_Data` 等非 WordWolf 集合都已经处理好，是直接测试"多样性"这个
  变量的现成材料，不用再等数据处理。
- KL loss 条件下 `peak_mass` 在早停点之后仍在涨，但当前早停只看 test_loss——
  "早停该监控哪个指标"这个问题还没有处理。
- `train-data-aux/` 里 `Demonstration_Data` 非对话片段的"加入训练是否有帮助"这个
  有对照消融还没有做。
- **旧模型对比基线是否权威还有疑点**：`access-model-train/results/` 目录下还有
  一个更新的 checkpoint `config_simvp_exp4_new_epoch31.pt`（比归档的 `exp4.pt`
  更新，config 相同，代码里被注释掉没启用），一直没有确认过它是不是比现在用的
  `exp4.pt` 更好/更该作为对比基线——目前所有"新旧模型对比"都用的是 `exp4.pt`。
- `sm_ratio` 融合权重目前固定 0.5（像素级加权，不可学习），一直没有作为超参数
  扫过，也没试过做成可学习的融合方式。
