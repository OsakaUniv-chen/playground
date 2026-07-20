# PSSP 模型 —— 项目记录（新PC，run-1 起）

**这份文件只记录 run-1 及以后在新PC上产生的新知识。** 旧PC上8轮的完整历史
（项目背景、已验证的方法论决定、数据集细节、踩过的坑、逐轮实验结果）全部保留在
`archive-runs/old-runs-1/CONTEXT.md`，本文件不重复摘抄——需要历史背景/方法论
依据时去查那份文件，这里默认读者已经看过或会去查。

## 项目是什么（一句话）

PSSP = 预测下一个说话人在声图（声源热力图）中的位置，输入过去若干帧的声图+
摄像头画面序列，预测未来几帧的声图，核心指标是预测峰值位置的定位精度。完整
背景见 `archive-runs/old-runs-1/CONTEXT.md`"项目是什么"一节。

## 2026-07-18 迁移到新PC

工作区换到了一台GPU更强、显存更大的新PC（RTX 3090，24GB显存）。旧PC上的8轮
全部归档到 `archive-runs/old-runs-1/`（连同当时的 `CONTEXT.md`）。新PC上的
第一轮工作 `run-1/`（chat-only，超参消融）已完成并归档到
`archive-runs/old-runs-2/run-1/`（2026-07-18，见下"run-1归档"）。当前活跃的
曾经是 `run-2/`（chat+WordWolfExp+GRP_meeting混合训练池）。**run-2已收尾
并归档（2026-07-21）**：Phase1（121-bag混合池）是最终锁定的配方，Phase 2~4/
噪声排查/过拟合探针/正则化排查/157-bag held-out测试这一整条后续排查全部
是负责人主动要求做的补充验证，结论是"当前配方已经追不动了，Phase1就是
这个阶段能拿到的最终结果"——见下"run-2结论摘要（已收尾）"。计划见
`archive-runs/old-runs-2/run-2/PLAN.md`（已标记收尾）。

## 仓库结构（当前状态）

```
train-pssp/
  CONTEXT.md                 # 本文件，只记录run-1起的新知识
  preprocessing/              # 原始数据处理入口，未变，详见archive里的CONTEXT.md
  train-data/                 # 训练用npz池，所有run共享，未变
  soundmap-videos/            # QC视频，未变
  access-model/                # 旧模型(exp4)，只做推理，未变
  archive-runs/
    old-runs-1/                 # 旧PC上做的全部8轮 + 当时的CONTEXT.md，只读存档
    old-runs-2/                 # 新PC上已完成的run，只读存档
      run-1/                      # chat-only配方+超参消融，见下方"run-1新增的方法论决定"
      run-2/                      # 121-bag混合池+后续排查，见下方"run-2结论摘要（已收尾）"
```

**run-1归档说明（2026-07-18）**：`run-1/` 移进 `archive-runs/old-runs-2/
run-1/` 后路径深度多了2层，脚本里原来的`DATA_DIR`用`_HERE.parent.parent`
（2层，run-1原本是顶层）定位到`train-pssp/`——现在需要`_HERE.parent.parent
.parent.parent`（4层）。**这些脚本当前没有跟着改**（沿用之前归档的先例：
只读存档，`RESULTS.md`里的数字是归档前留下的，不代表现在能直接跑通）。

**run-2归档说明（2026-07-21）**：`run-2/`移进`archive-runs/old-runs-2/
run-2/`后，同run-1的先例，路径深度多了2层——`run-2/train/dataset.py`的
`DATA_DIR`（`_HERE.parent.parent`）和`run-2/evaluation/report.py`的
`ACCESS_MODEL_DIR`（`_HERE.parent.parent`）都需要4层才能定位到
`train-pssp/`根目录。**同样没有跟着改代码**，只读存档，不代表现在能直接
跑通；`run-2`内部脚本互相引用的相对路径（`_HERE.parent / "train"`等）不
受影响，因为整个`run-2/`目录结构原样搬过去了，只有指向仓库根目录
（`train-data/`、`access-model/`）的路径需要额外2层。

## run-1 起沿用的既有约定（不重复论证，直接继承自旧PC的经验）

- 每轮 run 自包含，只共享 `train-data/`。
- Python环境：这台新PC用`~/.virtualenvs/train`（`workon train`），旧PC的
  `wolf`环境没有迁移过来，见下方"Phase 0（基础设施）"的环境细节。
- 评估口径：t+1~t+4 分步报告，t+2 是重点参考步，不合并成聚合数字；train/test（本
  轮起严格说是 train/val，见 `archive-runs/old-runs-2/run-1/PLAN.md`）两边都要报；每个实验的比较表固定带
  exp4 和朴素基线（重复最后一帧）两行对照；位置相关性指标已弃用，只看
  peak_dist(k=1) + PSR_k5@5。

## run-1 新增的方法论决定（run-1已归档到archive-runs/old-runs-2/，以下是归档前的记录，不改动）

**run-1（chat-only, 2026-07-18）总结**：Phase 0搭基础设施 → Phase 1选
`bs=32` → Phase 2确认`clip_len=10`优于20 → Phase 3确认`sm_ratio=0.5`/
`N_S=N_T=4`默认值已接近最优。最终配方 **`bs=32, clip_len=10, sm_ratio=0.5,
N_S=N_T=4, lr=1e-3起始+LR衰减联动早停, noise_ratio增强`**，val peak_dist
6.04/PSR 75.25%，稳定追平/略超exp4（6.14/74.88%）。详细数据见下，完整逐步
数字见`archive-runs/old-runs-2/run-1/RESULTS.md`。

**Phase 0（基础设施）**：
- **确认：`DataLoader(shuffle=True)` 每个epoch都重新洗牌**（`archive-runs/old-runs-2/run-1/train/
  verify_shuffle.py`）。直接对训练集用的 `RandomSampler` 连续调用4次
  `iter()`（等价于DataLoader每个epoch内部的行为），4次结果两两不同、但每次
  都覆盖同一组window index——不是"只洗一次然后固定顺序复用"。代码本身不用改，
  这条之后不用再查。
- **新PC Python环境**：旧PC的`wolf`虚拟环境没有迁移过来，这台机器上新建了
  `~/.virtualenvs/train`（`workon train`）。**硬件约束**：GPU驱动470.256.02
  较旧，只有 `cu118` 分支的torch实测能跑（`cu121`+ 官方要求驱动≥525，装不上），
  而`cu118`官方wheel只发到Python 3.10——**这台机器上跑GPU训练目前锁定在
  Python 3.10 + torch 2.7.1+cu118**（`pip install torch==2.7.1+cu118
  --index-url https://download.pytorch.org/whl/cu118`），更新的Python版本
  （3.11+/3.14等）现状下用不了GPU。以后想用更新的CUDA/Python，需要先升级
  显卡驱动（更大的系统改动，未做）。`simvp.py`（GSTA backbone）额外依赖
  `timm` 包，不在torch/numpy/matplotlib的常规安装清单里，容易漏装。

**Phase 1（batch size消融）**：

- **Phase 1 batch size消融结论（2026-07-18）：bs对结果影响很小，选 `bs=32` 作为
  run-1默认**。固定配方（clip_len=10, lr=1e-3起始+LR衰减联动早停, sm_ratio=0.5,
  noise_ratio增强）下扫了 `{16, 32, 64, 96, 128}`（`report.py`固定bs=32评估,
  跨实验可比，`report.py`固定用bs=32评估）：

  | bs | train peak_dist/PSR | val peak_dist/PSR |
  |---|---|---|
  | 16 | 6.60 / 72.45% | 6.12 / 75.04% |
  | 32 | 6.42 / 73.02% | **6.04 / 75.25%**（val最优） |
  | 64 | 6.52 / 72.60% | 6.11 / 74.88% |
  | 96 | **6.32 / 73.52%**（train最优，略过拟合迹象） | 6.16 / 74.67% |
  | 128 | — | **OOM**，13M参数模型+bs=128在24GB显存上装不下（单独跑、无并发也一样） |
  | exp4（参考） | 6.71 / 71.81% | 6.14 / 74.88% |
  | 朴素基线（参考） | 7.49 / 68.16% | 7.14 / 70.97% |

  四个可行bs差距都很小（val peak_dist 6.04~6.16），全部清楚超过朴素基线、追平
  exp4。选`bs=32`：val指标四者最优，且给Phase 2要测的`clip_len=20`（参数量
  13M→38M）留显存余量，不用贴着上限走。
- **方法论细节：train.py训练过程中内部早停跟踪的"最优val_peak_dist"和
  report.py最终报告的数字不完全一致**——内部早停用训练时的bs做val评估，
  report.py固定用bs=32重新评估；`DataLoader(drop_last=True)`丢的窗口数随bs
  变化（611个val窗口本来就不多，bs越大丢得越多，且丢的是时间轴上最后那段，
  不是随机的），两边看到的窗口子集不完全一样。不是bug，只是"训练时用来选
  checkpoint的依据"和"最终报告数字"来自略有差异的评估窗口，**以report.py
  数字为准**。
**Phase 2（clip_len对照）**：

- **Phase 2 结论（2026-07-18）：chat-only数据上 `clip_len=10` 全面优于
  `clip_len=20`**。固定bs=32/lr=1e-3起始/sm_ratio=0.5/noise_ratio增强对照：

  | 配置 | train peak_dist/PSR | val peak_dist/PSR |
  |---|---|---|
  | clip_len=10（=Phase1的bs=32基线） | 6.42 / 73.02% | **6.04 / 75.25%** |
  | clip_len=20 | 6.83 / 71.19% | 6.26 / 74.18% |

  train/val、peak_dist/PSR 四项clip_len=20全部更差——10s窗口在chat这个小/
  同质数据集（5797个训练窗口，3个bag）上没有额外收益，模型参数量却翻倍
  （38M vs 13M），大概率是"容量喂不饱/更容易过拟合"。**和旧PC eighth-run
  的`B_clip20`实验（当时用粗粒度早停判"不确定"）结论方向一致，这次用LR衰减
  联动早停看得更清楚，是真负结果不是噪声**。但历史上`C_g1g2_train_g3_test`
  显示clip_len=20的收益是在训练池加入WordWolfExp G1+G2**之后**才体现出来
  的（G3 held-out组上全面超过exp4）——**这个负结果不代表clip_len=20本身没用，
  更可能是"长窗口需要更大/更多样的训练数据才能发挥作用"，留给run-2扩数据
  规模时验证**，run-1范围内（chat-only）先确认 `clip_len=10` 更合适。
**Phase 3（sm_ratio + 容量消融）**：

- **Phase 3 结论（2026-07-18）：`sm_ratio`/模型容量在chat-only数据上都不是
  瓶颈，当前默认值已经接近最优**。固定bs=32/clip_len=10/lr=1e-3起始/
  noise_ratio增强对照：

  `sm_ratio`扫（N_S=N_T=4默认）：

  | sm_ratio | val peak_dist | val PSR |
  |---|---|---|
  | 0.3 | 6.19 | 74.26% |
  | **0.5（默认）** | **6.04** | **75.25%** |
  | 0.7 | 6.15 | 74.96% |
  | 0.9 | 6.24 | 74.59% |
  | 1.0（纯声图，无灰度混合） | 6.17 | 74.71% |

  `(N_S, N_T)`容量扫（sm_ratio=0.5默认）：

  | (N_S, N_T) | val peak_dist | val PSR |
  |---|---|---|
  | (2,2) 偏小 | 6.08 | 74.75% |
  | **(4,4) 默认** | **6.04** | **75.25%** |
  | (6,6) 偏大 | 6.13 | 74.88% |

  两组消融的所有候选值差距都很小（sm_ratio: 6.04~6.24；容量: 6.04~6.13），
  **当前默认值（sm_ratio=0.5、N_S=N_T=4）碰巧都是各自组里的最优或接近最优**
  ——不是因为这两个超参对这个任务毫无影响，更可能是本来就设得合理，chat-only
  这个数据规模下也测不出更大的差异。**容量往上（6,6）没有带来提升，往下（2,2）
  也没有明显变差**，说明在这个数据规模下模型容量不是瓶颈，扩数据规模（run-2）
  之前不需要在这个旋钮上纠结。
- **LR衰减联动早停（Phase 0a）跑出的真实行为**：四个bs的训练曲线都是同一个
  模式——第一次plateau前（约epoch 6~13）达到最优，之后3次lr衰减全部没能把
  指标拉回来，反而持续变差（比如bs=16：best在epoch8，之后一路掉到epoch20的
  更差水平）。**说明"早停太早"不是这次的问题**——给了充分的机会（3次衰减，
  每次3个epoch耐心）让模型在更低lr下继续精修，但确实就是在第一个plateau附近
  见顶，衰减救不回来。这条独立验证了`archive-runs/old-runs-1/CONTEXT.md`里
  "早停点异常靠前"这个老问题在chat小规模数据上**不是**因为checkpoint粒度太粗
  ——真实最优点就在那附近，值得在run-2扩大数据规模时留意是否同样成立。

## run-2 结论摘要（已收尾，2026-07-21，新会话从这里开始看最快）

**run-1（chat-only）已完成并归档**，最终配方：`bs=32, clip_len=10,
sm_ratio=0.5, N_S=N_T=4, lr=1e-3起始+LR衰减联动早停, noise_ratio增强`——val
peak_dist 6.04/PSR 75.25%，稳定追平/略超exp4，全部超参消融没能进一步突破。
完整数字见`archive-runs/old-runs-2/run-1/RESULTS.md`。

**run-2最终结论：`phase1_chat_ww_mtg`（121-bag混合池，chat+WordWolfExp
74bag+GRP_meeting 44bag，G1_game3~6held-out）是这个阶段锁定的最终配方**。
WordWolfExp held-out上peak_dist 9.14 vs exp4 12.23 vs 朴素基线11.31，
chat/grpmtg上也追平或略超exp4，2个种子重跑+G8交叉验证过是真实、可复现的
泛化能力，不是偶然。**在这个配方基础上，后续所有想再往上推一把的尝试
（更长窗口、翻倍数据、加正则化、改容量、混入新数据源）全部没能带来可辨识
的提升，其中一次（157-bag held-out测试）还测出了明确的负面效果**——run-2
到这里收尾，不再继续在"扩数据/调参"这条线上投入，完整过程见下：

1. **Phase 1**（121bag，G1_game3~6held-out）：**正向**，打破run-1天花板，
   WordWolfExp上peak_dist 9.14 vs exp4 12.23 vs 朴素基线11.31，全面超过。
   **=== 最终锁定的配方 ===**
2. **Phase 2**（同配方，held-out组换G8交叉验证）：**正向且稳定**，
   9.14/11.94两次都是"本实验<基线<exp4"方向一致，不是G1偶然。
3. **Phase 3**（clip_len 10→20对照）：**打平**，没有清楚胜负，但耗时2倍/
   参数量3倍，性价比差，clip_len=10仍是更实际选择。
4. **Phase 4**（推全量283-bag，新加157个bag无held-out设计）：**持平**，
   数据量翻倍没有测出可辨识收益，后续用2个种子重跑确认是真的噪声量级内
   持平，不是类比推测。
5. **过拟合探针**（24-bag小池关掉早停跑满100epoch）：**容量不是瓶颈**，
   train peak_dist能压到2.36，但泛化在早期就已经见顶，continuing只是在
   记忆训练数据。
6. **正则化排查**（weight_decay/增强力度/容量/dropout四个单变量实验）：
   **三个空结果，一个（缩容量）方向还错了**——没有找到能改善泛化的旋钮，
   问题不是"正则化不够"。
7. **157-bag held-out测试**（held出ATR_RIKEN_1F，训练混入其余108个bag）：
   **明确负面结果**——比零shot基线（没加任何新bag）还差一倍多，且预测
   随步长发散，推测是训练混入了"相近但不同"的场景（ATR_RIKEN_3f）导致
   负迁移。

**综合诊断**：过拟合探针+正则化排查+157-bag测试三条线索指向同一个结论——
这个配方学到的更像是"训练时见过的几个具体环境的专属记忆"，不是"声源定位
任务本身的通用规律"，所以在**已经验证过的域**（chat/WordWolfExp/
GRP_meeting，靠held-out组测试确认过）上泛化很好，但对**训练时完全没跑过
的新物理环境**，不但不能指望零成本泛化，混入相近场景反而可能有害。这不是
"数据不够多"或"正则化不够强"能解决的量的问题，要真正提升到新环境的
泛化能力，需要domain-invariant特征学习/领域自适应/新环境少样本微调这类
结构性不同的方法——**这类方法的工程投入明显大于run-2已经做的所有尝试，
负责人决定这个阶段不投入，run-2到此收尾**，需要时可以在此基础上重新立项。

详细数字/推理过程见下"run-2 新增的方法论决定/结果"，完整逐步数字见
`archive-runs/old-runs-2/run-2/RESULTS.md`，run-2代码在
`archive-runs/old-runs-2/run-2/train/`、`archive-runs/old-runs-2/run-2/evaluation/`。

## run-2 新增的方法论决定 / 结果

**Phase 1（2026-07-18）：chat+WordWolfExp(74bag)+GRP_meeting(44bag)混合训练
（121个bag，不是全量283），WordWolfExp的G1_game3~6held-out测试——正向结果，
是全项目历史上最强的一次held-out泛化**。配方沿用run-1（bs=32/clip_len=10/
lr=1e-3起始+LR衰减联动早停/sm_ratio=0.5/noise_ratio增强/N_S=N_T=4），训练池
116,227个窗口（约run-1 chat-only的20倍），单epoch~547s（~9.1分钟），15个
epoch后早停（best在epoch3，overall_pd_mean=7.41），总耗时约137分钟。

t+2（重点步）val/test peak_dist：总的7.48、chat 5.92、WordWolfExp
（G1_game3~6，完全held-out）9.43、GRPMTG（held-out那1个bag）7.15——四项
全部见`archive-runs/old-runs-2/run-2/RESULTS.md`t+2行。**均值（更全面的判断依据）**，
peak_dist↓/PSR↑，val/test列：

| 场景 | 本实验 | exp4 | 朴素基线 |
|---|---|---|---|
| 总的（三域按窗口数加权合并） | 7.39 / 62.41% | 7.90 / 62.56% | 8.46 / 57.23% |
| chat | 5.99 / 75.41% | 6.14 / 74.88% | 7.14 / 70.97% |
| **WordWolfExp（held-out）** | **9.14 / 67.93%** | 12.23 / 57.35% | 11.31 / 59.68% |
| GRPMTG（held-out） | 7.09 / 59.65% | 6.98 / 62.59% | 7.87 / 55.17% |

**关键发现**：
1. **WordWolfExp上全面碾压exp4和朴素基线**（peak_dist 9.14 vs 12.23 vs
   11.31，PSR 67.93% vs 57.35% vs 59.68%）——G1_game3~6这4个bag训练时完全
   没见过，这是真正的跨组泛化，不是记住训练集。比旧PC上最好的历史结果
   （eighth-run `C_g1g2_train_g3_test`：G3上peak_dist 10.71 vs exp4的12.94）
   还要好，且这次训练池规模大得多（121 vs 11个bag），说明这个方向不是偶然。
2. **chat上也超过exp4**（5.99 vs 6.14），甚至比run-1纯chat-only跑出来的
   6.04还略好——混入更多其它域数据没有损害chat本身的表现，反而有轻微帮助。
3. **GRPMTG上和exp4基本打平**（7.09 vs 6.98，本实验略差一点点），但清楚
   超过朴素基线（7.87）。不算突出，但exp4自己在这个域的"train"（对exp4来说
   也是没见过的域）表现很差（peak_dist 13.30，比朴素基线9.54还差），本实验
   能追平exp4已经说明确实学到了东西，只是提升空间不如WordWolfExp明显。
4. **这次121-bag规模明显打破了run-1 chat-only的天花板**——run-1无论怎么调
   超参都卡在val peak_dist 6.04附近（只有chat自己一个域），这次同样的配方
   在更大训练池上，WordWolfExp/chat两个域都清楚超过exp4，天花板被打破。

完整逐步数字（t+1~t+4分步、train列）见`archive-runs/old-runs-2/run-2/RESULTS.md`。

**Phase 2（2026-07-18）：换WordWolfExp held-out组为G8，交叉验证Phase 1的正向
结果是否稳定——确认稳定，不是G1的偶然**。配方/训练池完全同Phase 1，唯一变量
是`--ww-test-group G8`（`dataset.py`新增`ww_split(test_group)`函数支持任选
held-out组，`train.py`/`report.py`都跟着改了）。17个epoch，154分钟，best在
epoch5（overall_pd_mean=8.36，比Phase 1的7.41差——**这本身也是有意义的信号**，
见下）。

WordWolfExp held-out组val/test均值对比：

| held-out组 | 本实验 | exp4 | 朴素基线 |
|---|---|---|---|
| G1_game3~6（Phase 1） | 9.14 / 67.93% | 12.23 / 57.35% | 11.31 / 59.68% |
| G8_game3~6（Phase 2） | **11.94 / 58.78%** | 14.07 / 51.36% | 13.36 / 53.01% |

两次都是"本实验 < 朴素基线 < exp4"（peak_dist越小越好）方向完全一致——**泛化
能力确认真实、可复现，不挑held-out组**。G8整体数字比G1差一截，但**exp4和
朴素基线自己在G8上也同样更差**（exp4从12.23变14.07，基线从11.31变13.36），
说明G8本身是更难预测的组（呼应third-run诊断"有些场次本身就不好预测，任何
模型都难赢基线"），不是本实验配方在G8上失效——本实验和两个对照的相对差距
反而没有缩小多少。chat（5.99→6.16）、GRPMTG（7.09→6.97）两次结果也基本
稳定，波动很小，不受WordWolfExp held-out组选择影响（符合预期，这两个域的
train/eval构成没变）。

**Phase 3（2026-07-18/19）：clip_len=20 在121-bag规模下和clip_len=10基本
打平，没有清楚胜负**。同Phase 1训练池（chat+WordWolfExp 74bag+GRP_meeting
44bag，G1_game3~6held-out），唯一变量`clip_len: 10→20`。15个epoch，256分钟
（clip_len=10的Phase1只要137分钟），best在epoch3（overall_pd_mean=7.49，
比Phase1的7.41略差）。val/test均值对比：

| 场景 | clip_len=10（Phase 1） | clip_len=20（Phase 3） |
|---|---|---|
| chat | 5.99 / 75.41% | 6.30 / 74.00%（略差） |
| WordWolfExp（held-out） | 9.14 / 67.93% | 9.16 / 68.32%（几乎打平） |
| GRPMTG（held-out） | 7.09 / 59.65% | 7.01 / 60.92%（略好） |

三个场景互有胜负、差距都在噪声范围内，**没有清楚证据支持clip_len=20更
好**——但clip_len=20训练时间约2倍、参数量约3倍（38M vs 13M），性价比明显
更差。**之前"clip_len=20的收益需要更大/更多样训练数据才能体现"这个猜想
（源自旧PC eighth-run的C配方），在121-bag规模下仍未被证实**——可能需要
更大规模才能看到效果，也可能这个猜想本身不成立。**结论：clip_len=10仍是
更实际的默认选择**，除非以后规模大幅扩大后有理由重新测。

**Phase 4（2026-07-19）：训练池从121个bag翻倍到全量283个（加157个新来源：
ATR_RIKEN_1F/3f、olab_0630/olab_rev_0630、Demonstration_Data、
demo_data_0318、egoSAS、riken_3f、Testrun0420等，全部WHOLLY进训练、无
held-out设计），在已有的3个评估域上没有可辨识的收益，但也没有变差**。
`dataset.py`新增`OTHER_BAGS`（动态派生=train-data下所有不属于chat/
wordwolfexp/grpmtg的bag，不写死清单，跟着train-data目录走）+
`make_datasets(use_full_pool=True)`，`train.py`加`--use-full-pool`开关，
`report.py`同步支持"总的"pooled train计入other域。配方同Phase1
（clip_len=10, G1_game3~6held-out），组合训练池229,871个窗口（约121-bag
规模的2倍）。17个epoch，300分钟（约5小时），best在epoch5
（overall_pd_mean=7.47，比Phase1的7.41略差）。val/test均值对比：

| 场景 | val/test窗口数 | 121-bag（Phase 1） | 283-bag全量（Phase 4） | Δpeak_dist |
|---|---|---|---|---|
| 总的（四域加权） | 7,872 | 7.39 / 62.41% | 7.35 / 63.11% | -0.04（微降/微好） |
| chat | 608（四场景最小） | 5.99 / 75.41% | 6.31 / 74.05% | +0.32（升/略差） |
| WordWolfExp（held-out） | 1,472 | 9.14 / 67.93% | 9.09 / 68.48% | -0.05（微降/微好） |
| GRPMTG（held-out） | 5,792 | 7.09 / 59.65% | 7.02 / 60.60% | -0.07（微降/微好） |

**解读（2026-07-19修正，之前"负结果"措辞偏负面，改成更中性的判断）**：
总的/wordwolfexp/grpmtg三项Δpeak_dist都只有0.04~0.07，比Phase2交叉验证
held-out组时观察到的"稳定域"噪声波动（chat/grpmtg在换G1→G8时分别波动
0.17/0.12，当时判定为"基本稳定"）还要小，**方向偏正但完全落在噪声量级内，
不能算证实的提升**——这个项目所有实验都是单次训练，没有多种子/置信区间，
"提升"和"噪声"目前无法严格区分。chat是唯一Δ较大（+0.32）且方向为负的一项，
但chat的val集只有608个窗口，是四场景里最小的（grpmtg有5792个，差近10倍），
按噪声应随样本量增大而减小的直觉，chat这项波动偏大很可能主要是小样本噪声，
不代表真实性能下降。**综合结论：121→283bag（数据量翻倍）没有测出可与噪声
区分的收益，但也没有可信的下滑证据，是"持平"而不是"变差"**。新加的157个
bag本身在train上表现不错（本实验8.38 vs exp4 10.35 vs 朴素基线10.65），
但没有对应的held-out测试，无法验证这些新来源本身能不能提升"对没见过数据
的泛化"，只能看它们对已有3个held-out/val域的间接影响——目前看是持平。
瓶颈可能不在数据量本身（架构容量、任务本身的可预测性上限，或者新加的157
个bag和现有held-out测试域关联度不够高、"多样性"没有真正命中要害，呼应
third-run"数据多样性可能比数量更重要"的老猜想），但也可能这次翻倍本来就
不该期待很大提升——Phase1那次的大幅提升来自"第一次让训练池覆盖held-out域
的数据分布"这个质变，Phase4只是同域数据量的量变，边际收益递减是正常预期，
不必然说明方法或数据有问题。具体排查过程和结论见下"噪声排查"/"过拟合探针"/
"正则化排查"/"157-bag held-out测试"各小节，以及"run-2收尾"一节的最终判断。

**噪声排查（2026-07-19）：Phase1配方（121-bag池，同val/test集）额外跑2个
种子（1337、2024），用`report.py`同口径评估，确认Phase4的Δ落在噪声内**。
`train.py`本身没有`--resume`，另外Phase4的`log.csv`已经显示继续训练只会
让指标持续变差（epoch5后又跑了12个epoch，overall_pd_mean从7.47恶化到
10.21，wordwolfexp PSR腰斩），不支持"早停太早"的猜想，所以没有另外为这个
排查再跑一次续训。种子42（Phase1原始）/1337/2024三次独立训练，val/test
peak_dist（PSR）对比：

| 场景 | seed42（Phase1原始） | seed1337 | seed2024 | 三seed跨度 | Phase4（283-bag） |
|---|---|---|---|---|---|
| 总的 | 7.39 / 62.41% | 7.27 / 63.48% | 7.32 / 63.15% | 0.12 | 7.35 / 63.11%（**落在跨度内**） |
| chat | 5.99 / 75.41% | 6.25 / 73.89% | 6.22 / 74.18% | 0.26 | 6.31 / 74.05%（比三seed上限只高0.06） |
| wordwolfexp（held-out） | 9.14 / 67.93% | 9.16 / 68.39% | 9.19 / 68.34% | **0.05**（这个域三次种子出奇地稳） | 9.09 / 68.48%（**比三个seed都好**） |
| grpmtg（held-out） | 7.09 / 59.65% | 6.90 / 61.14% | 6.96 / 60.68% | 0.19 | 7.02 / 60.60%（**落在跨度内**） |

**结论：Phase4的"持平"判断得到确认，不再是类比推测**。总的/wordwolfexp/
grpmtg三项，Phase4的数字都落在（或优于）三次独立种子重跑划出的自然波动
范围内；chat唯一超出种子跨度的量也很小（0.06），且chat val集样本量本来
就是四场景里最小的（608窗口），噪声本来就该更大，不构成有力的"变差"证据。
**至此，121→283bag数据量翻倍"没有测出可辨识收益"这个结论已经用独立种子
重跑验证过，不是单次训练的偶然**。副产品：wordwolfexp（held-out）域三次
种子跨度只有0.05，是四个场景里最稳的，这个域的泛化能力看起来是这套配方
里最可靠、复现性最好的部分。

## 开放问题（run-1新增，不含旧文件里的历史开放问题）

- **`exp4_new` 在当前预处理约定下输出异常，暂缓验证**（2026-07-18）：
  `access-model/weights/config_simvp_exp4_new.pt`（从`/home/chen/Documents/
  R4/results/`找到，新PC上首次接入）用和exp4完全相同的预处理（exp变换target、
  sm_ratio=0.5混合input——这套约定已用exp4的数字和旧CONTEXT.md历史记录完全
  吻合独立验证过）跑chat数据，peak_dist~46（64x64网格上接近对角线一半）、
  PSR=0%，明显异常。排除了权重文件损坏（无NaN/Inf，各层均值/标准差和exp4
  同量级）、通道顺序颠倒（交换后数字完全不变）。怀疑是exp4_new真实的训练
  预处理约定和exp4不一样（比如target归一化方式、sm_ratio是否真的是0.5——
  配置文件字段不代表真的在用，见旧CONTEXT.md教训），但从没人验证过它的
  训练代码。**负责人决定暂不深挖**（成本不确定，可能要翻`/home/chen/
  Documents/R4`下的训练代码才能查清），`archive-runs/old-runs-2/run-1/evaluation/report.py`的对照
  表格暂时只留exp4+朴素基线+本实验三个模型，`access-model/predict.py`里
  `exp4_new`仍保留注册（`EXP_NAMES`），以后想查再查。

## 踩过的坑

- **`pip install timm`（不加`--index-url`）会偷偷把torch升级掉，表现成"GPU突然
  连不上CUDA"的假象**（2026-07-18）：`train`venv装完`torch==2.7.1+cu118`验证
  GPU可用后，装`timm`（simvp.py的GSTA backbone依赖）时它把`torchvision`作为
  依赖一起装，但没指定`--index-url`，pip从默认PyPI解析`torchvision`时连带把
  torch静默升到了`2.13.0+cu130`——cu130需要新驱动（≥525+），这块机器驱动是
  470.256.02，于是`torch.cuda.is_available()`变成`False`，报错"driver too old
  (found version 11040)"。**排查时最容易被带偏成"GPU/驱动坏了"**（一度怀疑
  persistence mode/GPU电源状态，`sudo nvidia-smi -pm 1`试过没用）——**真正
  判据是`workon r4`下同一块GPU当时`torch.cuda.is_available()`是`True`**，
  证明GPU/驱动本身没问题，问题在`train`venv自己的torch版本被换掉了
  （`pip list | grep torch`一看`torch 2.13.0`就现形）。**教训：装任何会拉
  torchvision/torchaudio的包（timm等）时，同一条pip命令里把torch/torchvision
  也显式钉住版本+`--index-url`一起传，不要让pip自己去默认索引解析，否则会
  静默换掉已经验证过兼容驱动的torch版本。**

## 开放问题 / 下一步方向

run-1（chat-only）的开放问题见上"开放问题（run-1新增）"一节（`exp4_new`
预处理约定未验证，暂缓）。

**run-2的核心开放问题（历史记录，全部已解决/已收尾）**：121→283bag数据量
翻倍没有测出可与噪声区分的提升，当时留了三个候选排查方向，现在全部做完了：
1. ~~给Phase 4新加的157个bag设计held-out测试组~~——**已完成**（见下
   "157-bag held-out测试"小节），结果是明确负面。
2. ~~排查early stopping在大数据下是不是依然过早~~——**已用Phase4自己的
   log.csv关掉，不需要单独实验**：epoch5（best）之后又跑了12个epoch（3次
   lr衰减耗尽），overall_pd_mean从7.47持续恶化到10.21，不支持"早停太早"，
   更像是epoch5附近就是真实最优点。
3. ~~多种子重跑估计噪声量级~~——**已完成**（seed1337/seed2024，见上"噪声
   排查"小节），结论：Phase4三个已有域的Δ都落在三种子自然波动范围内。

**过拟合探针（2026-07-19/20）：`overfit_probe.py`在24-bag小池（chat 3 +
WordWolfExp G1~G3组18 + MTG 3个，38,572训练窗口）上关掉早停跑满100
epoch——确认容量不是瓶颈，是明显的过拟合/泛化差距问题**。train peak_dist
从epoch1的10.49一路单调降到epoch100的**2.36**（train PSR 57%→93.5%），
全程没有趋平迹象，说明13M参数SimVP(gsta, N_S=N_T=4)架构完全有能力把train
指标压得很低，容量绰绰有余。但用`eval_overfit_checkpoint.py`在两个**这个
池子完全没见过**的域（WordWolfExp G8组、GRP_meeting官方held-out bag）上
分别测了epoch67和epoch100的快照：

| 域 | epoch67快照 | epoch100快照 | exp4 | 朴素基线 |
|---|---|---|---|---|
| WordWolfExp G8（unseen） | 14.68 / 48.16% | 14.99 / 46.87% | 14.07 / 51.36% | 13.36 / 53.01% |
| GRPMTG held-out bag | 8.45 / 51.47% | 8.47 / 50.97% | 6.98 / 62.59% | 7.87 / 55.17% |

两个域在epoch67时就已经比exp4和朴素基线都差，epoch67→100之间train继续
大幅改善但这两个未见过的域基本停在原地（微幅变差，且变化量落在此前噪声
排查确认的量级内，不算继续恶化的强证据）——**说明过拟合在早期（epoch5~
67之间某处）就已经把泛化能力打没了，之后主要是在纯粹记忆训练数据，不再
影响（或轻微影响）未见过的数据**。这和Phase1~4历史log里"continuing past
best epoch只会变差"的现象是同一件事的更极端展示，不是24-bag小池的特例。
**结论：容量不是瓶颈，瓶颈是过拟合/泛化差距，下一步应该直接对抗这个问题，
而不是扩大模型规模**（扩大规模大概率只会让过拟合更快发生）。

**正则化排查（2026-07-20，已完成）**：已确认当前配方并非"零正则化"——
`simvp.py`的`SimVP`类默认`drop=0.2, drop_path=0.2`，train.py此前没有覆盖
它们，所以Phase1~4和过拟合探针全程都在dropout=0.2+stochastic depth=0.2+
weight_decay=0.01+noise_ratio增强的组合下跑的，仍然明显过拟合——说明现有
正则化力度不够，或者容量本身（相对当前跨域数据规模）就偏大。给train.py
新增了`--drop`/`--drop-path`/`--aug-noise-std`/`--aug-ratio-lo`/
`--aug-ratio-hi`几个CLI开关（原来这几个旋钮不能从命令行调），在Phase1的
121-bag池上排了4个单变量对照实验（都用seed=42，方便直接对比Phase1原始
基线），run-name分别是`reg_wd0.1`（weight_decay 0.01→0.1）、
`reg_aug_strong`（增强力度加大：ratio_range (0.3,0.7)→(0.1,0.9)，
noise_std 0.03→0.06）、`reg_capacity_2x2`（**容量缩小**N_S=N_T 4→2，
重新验证run-1 Phase3"容量不是瓶颈"这个旧结论在当前跨域大规模数据下是否
依然成立——旧结论是在chat-only小数据上测的，用户提出的"数据集不够大、
模型是不是太大"这个问题值得在当前规模重新测）、`reg_drop0.4`（drop/
drop_path 0.2→0.4）。四个实验顺序跑完，用wordwolfexp/grpmtg held-out结果
做裁判，全部用`report.py`同口径评估。

**结果（2026-07-20）：四个单变量正则化/容量调整，三个是空结果，一个（容量
缩小）方向错误——这条排查路线目前没有找到能改善泛化的旋钮**：

| 场景 | Phase1基线 | reg_wd0.1 | reg_aug_strong | reg_capacity_2x2 | reg_drop0.4 |
|---|---|---|---|---|---|
| 总的 | 7.39/62.41% | 7.39/62.12%（持平） | 7.46/62.12%（噪声内） | **7.55/61.69%（超出噪声）** | 7.42/62.20%（噪声内） |
| chat | 5.99/75.41% | 5.99/75.16%（持平） | 6.19/75.00%（噪声内） | **6.32/73.85%（超出噪声）** | 6.07/75.00%（噪声内） |
| wordwolfexp | 9.14/67.93% | 9.15/68.39%（持平） | 9.14/68.34%（持平） | **9.30/68.12%（超出噪声）** | 9.15/68.12%（持平） |
| grpmtg | 7.09/59.65% | 7.09/59.16%（持平） | 7.16/59.18%（噪声内） | **7.23/58.78%（接近边缘）** | 7.12/59.36%（噪声内） |

- **weight_decay翻10倍（0.01→0.1）：完全空结果**，四个场景几乎和基线逐位
  相同，这个旋钮对当前的过拟合现象没有任何可测的影响。
- **noise_ratio增强力度明显加大：空结果**，四个场景都落在此前种子重跑
  确认的噪声跨度内，没有清楚方向。
- **dropout/drop_path翻倍（0.2→0.4）：空结果**，同样全部落在噪声内。
- **容量缩小（N_S=N_T 4→2）：唯一一个四场景方向一致的信号，但方向是
  变差不是变好**——总的/chat/wordwolfexp三项都超出了噪声跨度，grpmtg
  接近边缘。**再次确认run-1 Phase3的旧结论（容量在这个范围内不是瓶颈）
  在当前跨域大规模数据下依然成立，用户提出的"数据集不够大、模型太大"这个
  假设没有得到支持**——缩容量不但没帮泛化，连带train拟合能力也一起降了，
  是纯粹的双输。

**综合结论**：过拟合探针证明的"容量够用、但泛化差距明显"这个现象，**用
四个最常规的单变量正则化/容量调整都没能缓解**——不是"正则化力度不够"这么
简单，问题可能出在别的地方（数据本身的域覆盖/多样性、架构设计、学习率
调度，或者这就是当前配方在这个数据规模下的自然天花板，early stopping在
epoch3~5附近截住已经是当前能做到的最优）。这条"调正则化旋钮"的路线目前
看没有更多明显低垂果实，下一步更值得投入的方向是数据侧（157-bag
held-out测试）或者更结构性的改动，而不是继续在这几个旋钮上微调。

**157-bag held-out测试（2026-07-20/21，已完成）**：直接排查"Phase4加进来的157
个新来源bag本身有没有泛化价值"这个一直悬着的开放问题。设计：从157个
`OTHER_BAGS`里挑最大的单一来源**ATR_RIKEN_1F（49个bag）整体WHOLLY held
out**，其余108个bag（`OTHER_TRAIN_BAGS_EX_ATR1F`，跨ATR_RIKEN_3f/olab/
egoSAS/kitchen/Demonstration_Data等多个来源）可以加进训练池。`dataset.py`
新增这两个常量+`make_datasets(atr1f_holdout=True)`，`train.py`加
`--atr1f-holdout`开关（和`--use-full-pool`互斥），`report.py`加
`--extra-eval-domain`参数，让任意checkpoint（不管训练时有没有见过这个域）
都能用同一条eval路径去测ATR_RIKEN_1F，这样零shot基线和实际训练过的模型
能公平对比。两个对照：
- **Config A（零shot基线）**：直接复用已有的`phase1_chat_ww_mtg`
  checkpoint（121-bag池，没见过任何OTHER_BAGS），测它在ATR_RIKEN_1F
  （35,360个held-out窗口）上的zero-shot表现。
- **Config B（已完成，见下）**：`atr1f_holdout_test`，121-bag池+108个other bag
  （194,492训练窗口），同样在ATR_RIKEN_1F上评估——如果这108个bag真的带来
  跨域泛化价值，应该比Config A明显更好。

**Config A结果，出现了和chat/WordWolfExp/grpmtg完全相反的模式**：

| 模型 | peak_dist | PSR |
|---|---|---|
| exp4 | **6.32**（最好） | **76.56%**（最好） |
| Phase1本实验（零shot） | 7.65 | 70.70% |
| 朴素基线 | 7.66 | 68.11% |

在这个真正陌生的物理场景（ATR_RIKEN办公室）上，**exp4反而明显优于Phase1
模型，Phase1模型只是勉强追平朴素基线**——和其它三个域"Phase1全面超过exp4"
的模式完全相反。初步解读：Phase1模型可能对训练时见过的几个特定场景（chat/
WordWolfExp/GRP_meeting的房间、机位）学得比较贴合，遇到真正没见过的物理
空间时，泛化不如exp4（exp4的历史训练数据来源可能更广/更多样，具体没有
查证）。这给Config B设了明确的对照标准：加上108个bag后，ATR_RIKEN_1F上的
表现能不能从7.65/70.70%拉近甚至超过exp4的6.32/76.56%。

**Config B结果（2026-07-21）：18个epoch，best epoch9（overall_pd_mean=
7.50，和Phase1的7.41接近），chat/wordwolfexp/grpmtg三个老域数字正常
（6.22/74.26%、9.12/68.21%、7.15/59.54%，都在Phase1~4历史范围内，没有
异常）。但ATR_RIKEN_1F上的结果不是"没帮助"这么温和，而是明显更差，且
呈现异常的发散模式**：

| 模型 | t+1 | t+2 | t+3 | t+4 | 均值peak_dist | 均值PSR |
|---|---|---|---|---|---|---|
| exp4 | 5.86 | 6.32 | 6.50 | 6.59 | 6.32 | 76.56% |
| 朴素基线 | 6.55 | 7.51 | 8.12 | 8.44 | 7.66 | 68.11% |
| Config A（零shot，未见任何OTHER_BAGS） | 6.66 | 7.52 | 8.00 | 8.42 | 7.65 | 70.70% |
| **Config B（本实验，训练含108个other bag）** | **7.91** | **13.71** | **17.37** | **18.75** | **14.43** | **38.12%** |

**Config B从t+1开始就是四者中最差（7.91 vs exp4的5.86），而且随预测步长
急剧发散**（t+1→t+4：7.91→18.75，PSR从68.48%崩到17.29%），和其它三者
"温和线性增长"的模式（exp4/基线/Config A都是差不多斜率的缓慢上升）完全
不同——**这不是"没有收益"，是训练进了108个bag之后在这个held-out域上明显
变得更差，且预测在多步之后出现类似发散/不稳定的行为**。同一个checkpoint
在chat/wordwolfexp/grpmtg上表现正常（说明不是权重损坏或eval代码bug——
`report.py`的`atr1f`评估路径已经用Config A验证过是好的，同一条路径这次
测出的异常应该是真实的模型行为，不是代码问题）。

**初步解读**：108个bag里包含`ATR_RIKEN_3f`（同一建筑/机构的另一个楼层，
和被held-out的`ATR_RIKEN_1F`很可能视觉上相近但不同）——推测模型可能学到
了对3f这类"相近但不同"场景过拟合的特征，这些特征在1F上是"自信地错"，
比完全没见过相关场景（Config A零shot）更具误导性，导致多步预测发散。
如果这个推测成立，说明**"相近但不同"的域混进训练池，可能比"完全无关"的
域更危险**——不是单纯"多样性不够"的问题，而是可能存在负迁移。

**结论：157个新bag（至少这108个）不但没有验证出泛化价值，反而在这个
held-out域上造成了明显更差、且模式异常的结果——这是本轮所有排查里最
明确的负面证据**。如果以后还想再验证"新数据源有没有用"，应该避免把
"同一场地/相近场景的不同子集"混在同一批判断里（比如单独测一下只有
`egoSAS`/`kitchen`这类和已有域完全不相关的来源，而不是把`ATR_RIKEN_3f`
这种和held-out目标高度相关的也放进去）。

## run-2 收尾（2026-07-21）

**负责人决定：run-2到此收尾，不再继续在"扩数据/调正则化/调容量"这条线上
投入**。锁定配方 = `phase1_chat_ww_mtg`（见上"run-2结论摘要"）。

**明确暂缓、不是遗忘**的后续方向（留档，以后重新立项时可参考，不是当前
TODO）：
1. 更细粒度地重测157-bag里"完全无关"的来源（如egoSAS/kitchen，排除
   ATR_RIKEN_3f这类和held-out目标相近的子集），验证"近似域负迁移"这个
   假设是否成立。
2. domain-invariant特征学习/领域自适应/新环境少样本微调——如果以后目标
   明确是"要对全新物理环境有泛化能力"，这类结构性不同的方法值得考虑，但
   工程投入明显大于run-2已做的任何一次实验，需要单独立项评估。

代码资产（供以后复用）：`overfit_probe.py`/`eval_overfit_checkpoint.py`
（容量诊断）、`train.py`的`--drop`/`--drop-path`/`--aug-*`/
`--atr1f-holdout`开关、`report.py`的`--extra-eval-domain`（任意checkpoint
测任意域，不依赖训练配置）——都是这轮排查留下的通用工具，不是一次性脚本。

详细计划/待选项见`archive-runs/old-runs-2/run-2/PLAN.md`"后续方向"一节
（已同步标记收尾）。
