# run-3 计划（v1 草案，2026-07-21，新会话据此执行）

## 背景 / 目标

run-2已收尾（`archive-runs/old-runs-2/run-2/`，锁定配方`phase1_chat_ww_mtg`）。收尾结论：
121→283bag数据量翻倍没有可辨识收益（噪声排查确认过），157个新bag里至少
ATR_RIKEN_1F相关的108个混入训练带来了明确负面结果（比zero-shot基线还差一倍多，
且预测随步长发散，判断是"相近但不同"场景导致负迁移）。四个正则化/容量单变量
实验全部是空结果或方向错误。综合诊断：当前配方的泛化更像是"记住训练时见过的
具体环境"，不是"学到声源定位任务本身的通用规律"。run-2判断这条"扩数据/调参"
路线在283-bag规模上已经追不动，收尾。

**2026-07-21新增变量**：`train-data/`新增两个此前从未训练过的大数据源——
`expo_reception_2025`（167 bag）和`indy_teleoperation`（23 bag），是run-2收尾
之后才加进来的，run-2的全部结论（包括157-bag测试）都不涉及这两个来源，不能
直接套用。**run-3的核心目标**：用run-2已验证过的"held-out设计在前、混入训练
在后"方法论，老老实实测一遍这两个新来源到底有没有泛化价值——不是简单调用
现成的`--use-full-pool`把它们悄悄扫进去（见下"重要提醒"）。

## 重要提醒：不要直接对现有代码跑 `--use-full-pool`

`run-2/train/dataset.py`里`OTHER_BAGS`是**动态派生**的
（`DATA_DIR.glob("*.npz")`里所有不属于chat/wordwolfexp/grpmtg的bag），不是写死
的157个名字的清单。这意味着现在如果直接复用run-2代码跑`--use-full-pool`，
`expo_reception_2025`和`indy_teleoperation`会被自动、悄悄并入`OTHER_BAGS`
（157→347个bag），在没有任何held-out设计的情况下整体扔进训练——这正是157-bag
测试事后才发现有问题的做法。**run-3不能重复这个模式**，两个新数据源必须先设计
held-out拆分，才能进训练池。

## 数据规模速览（2026-07-21核实，见`train-data/index.csv`）

| 数据源 | bag数 | ticks(2Hz) | 约合时长 | 备注 |
|---|---|---|---|---|
| expo_reception_2025 | 167 | 188,259 | 26.1小时 | 单一来源里最大的；横跨9个记录日（09-16~09-29） |
| indy_teleoperation | 23 | 20,376 | 2.8小时 | 规模接近ATR_RIKEN_3f；横跨5个记录日（03-30~04-17） |

对照：run-2 Phase 1的121-bag训练池是116,227个窗口，Phase 4推到283-bag全量池是
229,871个窗口。**expo单独一个来源的规模就接近整个Phase1训练池，两个新源加起来
接近或超过Phase4全量池的规模**——不是小打小闹的增量，值得认真设计held-out，
不能延用Phase4"全部直接扔进去、不设计held-out"的做法。

`game`/`mode`两列在expo/indy的index.csv行里都是空的（和ATR_RIKEN/olab同类，不像
WordWolfExp/GRP_meeting有结构化场次设计）——这两个来源和157-bag测试里出问题的
`OTHER_BAGS`是同一种"原始录制、无内建held-out设计"性质，**风险提示同样适用，
不能默认"新数据=更好"**。

## Phase 0：基础设施 + 零成本诊断（先做，不训练模型）

1. **补registry**：`preprocessing/build_dataset.py`的`JOBS`列表目前没有
   `expo_reception_2025`/`indy_teleoperation`条目（这两批npz的生成时间晚于
   `build_dataset.py`最后一次修改，说明是绕开当前registry产出的，`DATA_REPORT.md`
   两张表也还是283-bag时代的旧数字）。补上`Job(...)`条目+重新生成
   `DATA_REPORT.md`，保持"一个脚本、一份来源登记"的项目约定。
2. **抽查QC视频**：训练前先看几段`soundmap-videos/expo_reception_2025_*.mp4`和
   `indy_teleoperation_*.mp4`，搞清楚"reception"和"teleoperation"实际是什么场景
   （人数、机位、有没有机器人噪声等）——沿用项目一贯"先看视频再信数字"的习惯，
   成本几分钟。
3. **零成本zero-shot基线**：仿照`atr1f_holdout`的现成模式
   （`ATR1F_TEST_BAGS`/`OTHER_TRAIN_BAGS_EX_ATR1F`那一套），在`dataset.py`/
   `report.py`里新增`expo`/`indy`两个domain分支，**不需要训练新模型**，直接复用
   已训练好的`phase1_chat_ww_mtg` checkpoint + `report.py --extra-eval-domain
   expo indy`跑一次评估，同时把exp4、朴素基线也在同样的held-out切片上跑一遍。
   成本不到半小时，但价值高——照抄ATR_RIKEN_1F测试的经验，**zero-shot基线是
   后面判断"训练之后到底有没有真正学到东西"的裁判基准**，没有它就没法区分
   "训练带来的提升"和"运气/噪声"。

### held-out拆分建议（负责人可调整，以下是首版提议）

数据没有`game`/`mode`结构，只能按"整天held out"，参照GRP_meeting/ATR_RIKEN_1F
"整段没见过"的先例：

- **expo_reception_2025**（9个记录日，8/13/17/14/14/30/32/27/12个bag）：建议
  held out最后一个记录日**09-29（12个bag，约7%）**作为第一版轻量测试——
  "没见过的未来场次"这个框架和实际部署场景（同一展位，新的一天）最贴近。如果
  这个比例统计功效不够，可以把09-28也一起held out（27+12=39个bag，约23%，
  量级上接近ATR_RIKEN_1F的49/157≈31%）。
- **indy_teleoperation**（5个记录日，4/6/5/6/2个bag）：建议held out单独最大的
  一天**04-09（6个bag，约26%）**——总量只有23个bag，held-out太小会测不出稳定
  信号；如果6个bag held-out后train集只剩17个bag、信号依然不稳定，可以考虑退回
  chat式的"时间切分"策略而不是整bag held-out。

这两组拆分需要写进`dataset.py`（新增`EXPO_TRAIN_BAGS/EXPO_TEST_BAGS`、
`INDY_TRAIN_BAGS/INDY_TEST_BAGS`常量，按日期前缀`sorted(b for b in ... if ...)`
筛出来，不用手写全部文件名，做法和`ATR1F_TEST_BAGS`一致）。

## Phase 1：分别验证——indy先（便宜），expo后（贵）

**单变量原则**（沿用run-1/run-2一贯做法）：expo和indy不同时加入，一次只变一个，
避免两个新源互相干扰、分不清是谁的功劳/谁的问题。

- **Phase 1a（indy）**：Phase1的121-bag池 + indy的train部分（约17个bag），
  clip_len=10，其余配方**原样沿用锁定配方**（bs=32/lr=1e-3+LR衰减早停/
  sm_ratio=0.5/N_S=N_T=4/noise_ratio增强/drop=drop_path=0.2/weight_decay=0.01）。
  indy只占池子一小部分（新增约17,000窗口，池子从116K涨到约133K，+15%左右），
  预计单epoch耗时和Phase1接近，总训练时长量级预计接近Phase1的137分钟，成本低，
  先做。
- **Phase 1b（expo）**：Phase1的121-bag池 + expo的train部分（约155个bag，不含
  indy）。expo体量大（新增约17万+窗口，池子接近或超过Phase4的229,871窗口
  规模），预计单epoch耗时、总训练时长量级会接近甚至超过Phase4（约300分钟/5
  小时），需要预留完整GPU时段（建议排到夜间/无人值守跑）。

两次实验都要：①对chat/wordwolfexp/grpmtg三个已验证域重新评估，确认新数据没有
像157-bag测试那样拖累已有能力（这是最容易被忽略但run-2教训里最重要的一条）；
②对应新域评估自己的held-out切片，和Phase 0第3步的zero-shot基线比较——只有
"训练后 < zero-shot"才算真正学到了东西，不能只看训练后的绝对数字。

## Phase 2（视Phase 1结果决定）

- 若indy、expo**都**显示正向（训练后明显优于zero-shot，且没有拖累三个老域）
  → 仿照run-2 Phase 1→Phase 2的路径，合并成一个新池子（121+expo train+indy
  train），并至少做一次交叉验证（换一个held-out天/换seed），确认不是偶然，
  形成run-3的锁定配方。
- 若其中一个正向一个负向/持平 → 只并入正向的那个，负向/持平的那个记录下来、
  暂缓（参考157-bag测试对`ATR_RIKEN_3f`类"相近但不同"域的处理方式），不要
  强行都塞进去。
- 若两个都复现157-bag测试的模式（比zero-shot还差，且预测随步长发散）→ 这将是
  一个有信息量的负结果，进一步印证run-2"当前配方靠的是记住具体环境，不是学到
  任务通用规律"这个诊断——不必惊讶。run-2已把domain-invariant特征学习/领域
  自适应这类结构性方法列为该场景下的下一步方向，但工程投入明显更大，需要单独
  立项评估，不在run-3默认范围内。

## 明确不再重新验证的旋钮

bs（=32）、sm_ratio（=0.5）、模型容量（N_S=N_T=4）、weight_decay（=0.01）、
dropout/drop_path（=0.2/0.2）、augment力度（noise_ratio默认参数）——run-1+run-2
已经做过独立的单变量实验，全部是空结果或方向已经很清楚，run-3没有新理由重新
花GPU时间验证这些。

## Phase 3（stretch目标，视Phase 1/2结果，非run-3必答题）

`clip_len=20`——"长窗口需要更大/更多样训练数据才能发挥作用"这个假设，在旧PC
eighth-run（11个bag规模）和run-2 Phase 3（121个bag规模）两次都还没能证实，但也
没被证伪，一直悬着。如果Phase 1/2顺利、且最终池子规模明显超过之前（expo+indy
加进去后总窗口数很可能接近或超过40万，是Phase 3测试时121-bag规模的3倍以上），
这可能是"数据终于够大了"的第一次机会，值得作为Phase 1/2之后的补充对照，而不是
run-3一开始就要做的事——沿用项目一贯的单变量纪律，先把数据组成的问题解决完，
再单独测clip_len。

## 复用run-2的基础设施

`run-3/`直接从`archive-runs/old-runs-2/run-2/train/`+`evaluation/`复制一份作为
起点（`dataset.py`/`train.py`/`simvp.py`/`augment.py`/`report.py`/
`metrics.py`），沿用完全相同的评估口径（t+1~t+4分步/t+2重点，train/val(test)都
报，exp4+朴素基线固定对照，域之间永远分开报告不做静默合并）。需要新增的代码
改动集中在`dataset.py`（`EXPO_*`/`INDY_*`常量+`make_datasets()`新增
`expo_holdout`/`indy_holdout`开关，仿照`atr1f_holdout`）和`report.py`
（`build_domain_pair()`新增`"expo"`/`"indy"`两个分支，仿照`"atr1f"`）——不是
从零重写，是在已验证过的框架上加两个和`atr1f_holdout`同构的分支。

## Phase 0 结果（2026-07-21，已完成）

1. **补registry**：`DATA_REPORT.md`两张表已刷新（473 bag/450,110 ticks/62.52
   小时）。`build_dataset.py`的`JOBS`列表**没有补**——这两批npz生成时间晚于
   `build_dataset.py`最后一次修改，真实`root`源目录未经核实，这台机器上
   `/media/chen/Extreme SSD/PSSPData/`当前未挂载，不确认路径就写`Job(...)`
   条目风险比不写更大（会误导以后的人）。**需要负责人补充这两个来源的真实
   PSSPData子目录路径**。
2. **QC视频抽查**：**没做成**——`soundmap-videos/`在这台机器的工作目录里
   不存在（历史上体积较大，大概率是本地生成产物，没有随仓库同步过来）。
   目前完全没有渠道目视确认expo/indy的场景内容。见下"结果解读"，这一步
   缺失现在看比预想更值得补上。
3. **零成本zero-shot基线**：`dataset.py`新增`EXPO_*`/`INDY_*`常量+
   `make_datasets()`的`expo_holdout`/`indy_holdout`开关（复核过：167→
   155训练/12held-out，23→17训练/6held-out，两者held-out都是按日期整天
   划分，和`atr1f_holdout`同构）；`report.py`的`build_domain_pair()`加了
   `"expo"`/`"indy"`两个分支。用`phase1_chat_ww_mtg`（未见过任何expo/indy
   数据）跑`--extra-eval-domain expo indy`，完整结果见`run-3/RESULTS.md`。
   chat/wordwolfexp/grpmtg三个老域的数字和run-2原始记录逐位一致，确认代码
   搬运/checkpoint加载没有引入偏差。

### 结果解读：t+2（重点步），val/test（held-out day）列，peak_dist↓/PSR↑

| 场景 | exp4 | 朴素基线 | phase1_chat_ww_mtg（zero-shot） |
|---|---|---|---|
| expo_reception_2025（held-out day 09-29） | 18.30 / 26.85% | **14.60 / 39.69%**（三者最好） | 15.43 / 38.69% |
| indy_teleoperation（held-out day 04-09） | **11.35 / 57.00%**（peak_dist最好） | 11.43 / **58.00%**（PSR最好） | 11.52 / 53.24%（三者PSR最差） |

**这是一个和ATR_RIKEN_1F Config A不同、但同样值得认真对待的信号：在这两个
新域上，`phase1_chat_ww_mtg`都没能超过朴素基线**——不是"exp4更好"（ATR
RIKEN_1F那次的模式），而是"什么模型都不如什么都不做"。expo上朴素基线明显
领先两个真实模型（14.60 vs 15.43 vs 18.30，exp4最差），indy上三者非常接近
（11.35~11.52，量级上接近历史噪声跨度），phase1模型在indy的PSR上明显落后
（53.24% vs 其余两个的57~58%）。

**含义**：Phase 1（训练时混入expo/indy）现在有了一个更高、更明确的及格线
——不是简单"训练后有没有变好"，而是"训练后能不能第一次在这两个域上超过
朴素基线"。目前两个真实模型都赢不了"什么都不做"，说明这两个域的动态可能
和已验证域（chat/WordWolfExp/GRP_meeting对话式场景）有质的不同（expo是
"接待"场景，猜测声源可能长时间停留在固定位置附近，朴素连续性因此是强先验；
indy是"遥操作"场景，声学环境可能截然不同）。**在没有QC视频可看的情况下，
这只是推测**——建议在投入Phase 1b（expo，预计约5小时GPU时间）之前，先找
机会看几眼原始视频/录音，确认这个猜测，而不是直接假设"训练会带来提升"。

## Phase 1a 结果（indy，2026-07-21，已完成，正向）

`phase1a_indy`：121-bag基础池 + INDY_TRAIN_BAGS（17个bag）混入训练，其余配方
原样沿用锁定配方（未传任何额外CLI参数，全部用默认值=Phase1原配方）。17个
epoch，每epoch约601秒（约10分钟），总耗时约170分钟，early stopping在epoch17
触发（best在epoch3，overall_pd_mean=7.4911，和Phase1原始的7.41几乎一致）。

**indy held-out天（04-09）结果，t+2均值口径，peak_dist↓/PSR↑**：

| | zero-shot（Phase 0，未见过indy） | 训练后（phase1a_indy） | exp4 | 朴素基线 |
|---|---|---|---|---|
| indy held-out (04-09) | 11.93 / 51.37%（三者最差） | **9.30** / — | 11.58 | 11.65 |

**清楚的正向结果**——从"三者最差、不如朴素基线"（zero-shot）变成"明确超过
exp4和朴素基线"（9.30 vs 11.58 vs 11.65），降幅约2.3，量级上和WordWolfExp
Phase1当年的突破（9.14 vs exp4 12.23 vs 基线11.31）相当。**说明zero-shot
时indy"打平朴素基线"不是"这个域学不到东西"，只是"模型从没见过这类数据"
——给17个bag就足够学出明显超过基线的能力，在真正没见过的那一天上验证过。**

**对三个已验证老域的影响：在此前噪声跨度内，没有可辨识的拖累**——chat
5.99→6.14（Δ0.15，小于三seed跨度0.26）、wordwolfexp 9.14→9.16（Δ0.02，小于
三seed跨度0.05）、grpmtg 7.09→7.17（Δ0.08，小于三seed跨度0.19）。indy这块
新增数据没有重演157-bag测试的负迁移模式。

**对expo zero-shot的影响（附带检查，这次训练没碰expo）**：exp4/朴素基线
不变（after all没在训练里出现过），但phase1家族模型自己在expo上的zero-shot
从15.42变成16.68（更差，Δ1.26）——比其余三个老域的偏移都大，暂时没有
多seed噪声参照，不确定是否有意义，留意但不下结论，Phase 1b/2再看会不会
稳定复现。

**结论：indy正向，且没有反证。按计划推进到Phase 1b（expo）。**

## 环境提醒（继承自run-1/run-2，未变）

`workon train`（Python 3.10 + torch 2.7.1+cu118，这台机器GPU驱动较旧锁定在这个
组合）。装任何会拉torchvision/torchaudio的包时，同一条pip命令里把torch/
torchvision也显式钉住版本+`--index-url`一起传，不要让pip自己去默认索引解析
（教训见CONTEXT.md"踩过的坑"）。
