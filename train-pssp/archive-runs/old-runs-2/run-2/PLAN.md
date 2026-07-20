# run-2 计划（v2，2026-07-18，2026-07-21收尾）

**状态：已收尾**。Phase 1~4（121-bag混合池两次held-out交叉验证+clip_len
对照+推全量283-bag）之后，又做了噪声排查（2种子重跑）、过拟合探针、4个
正则化实验、157-bag held-out测试，结论是当前配方追不动了，`phase1_chat_
ww_mtg`（Phase 1）是最终锁定配方。完整结论见CONTEXT.md"run-2结论摘要
（已收尾）"和"run-2收尾"两节。

## 目标

run-1（chat-only）把配方（bs=32/clip_len=10/lr=1e-3+LR衰减联动早停/
sm_ratio=0.5/noise_ratio增强/N_S=N_T=4）榨到了追平exp4的水平，所有超参消融
都没能再突破——小数据规模本身可能就是天花板。run-2 用这套验证过的配方，把
训练池从chat-only（3个bag）扩到一个明显更大、但仍**不是全量283-bag**的
混合规模（负责人指定，见下），检验能不能突破run-1的天花板。

**术语**：**val** = bag内时间切分（chat的90/10），不是held-out；**test** =
完全没见过的bag/组（WordWolfExp的G1_game3~game6、GRP_meeting的held-out那
1个bag）。两者不合并平均，参考旧CONTEXT.md fifth-run的教训——但下面"报告
格式"里负责人把三者统称"验证集"，报告表格按数据来源分场景，不需要在名词上
纠结。

## 训练/评估集设计（负责人指定，2026-07-18，v2修正）

三个数据来源混合成一个训练池：

| 来源 | 训练部分 | 评估部分 |
|---|---|---|
| chat（3个bag） | 前90%（时间切分，同run-1） | 后10% = val |
| WordWolfExp（78个bag=13组×6） | 除 G1_game3~game6 外**全部**（74个bag，含G1自己的game2_Video/interview） | G1_game3~game6（4个bag）= test，完全held-out |
| GRP_meeting/MTG（45个bag） | **除held-out那1个外全部**（44个bag，暂定held出`GRP_meeting_2025-01-16-13_08_04`，"随便选"没有特殊理由） | 那1个bag整段 = test，完全held-out（和WordWolfExp一样是整bag没见过，不是时间切分） |

训练池总规模：3(部分)+74+44=**121个bag**——比v1版本理解大得多（之前误以为
MTG只挑1个进训练），但仍**不是全量283-bag**，符合"不要一开始就用全量数据"。

**其余超参沿用run-1配方不变**：bs=32/lr=1e-3起始+LR衰减联动早停/sm_ratio=0.5/
noise_ratio增强/N_S=N_T=4。**clip_len先用10**（run-1在chat-only上的结论），
如果这一步跑出正向结果、后续想验证clip_len=20需不需要更大数据才有用，可以
再补一次clip_len=20对照。

## 报告格式（负责人指定：不只总表，三个场景各一张）

每次实验出 **4 张表**（peak_dist + PSR 各一套，所以其实是4×2=8个表格区块）：
1. **总的**：所有val/test窗口（chat val + WordWolfExp test + MTG val）合并
   算一次整体指标。
2. **chat场景**：chat train vs chat val。
3. **WordWolfExp场景**：WordWolfExp train（74个bag）vs WordWolfExp test
   （G1_game3~game6）。
4. **GRPMTG场景**：MTG train（44个bag）vs MTG test（held-out那1个bag）。

每张表固定对照exp4+朴素基线（沿用run-1约定），t+1~t+4分步（t+2重点）。

## Phase 0：时间探测（先做，低成本）

chat-only每epoch~28s；121个bag的训练池规模明显更大（约40倍训练窗口数量），
正式跑之前先测一下这个新训练池一个epoch大概多久，据此重新评估`plateau_patience`/`max_decays`这些
早停超参是否还合适（如果一个epoch要几分钟，"3个epoch没提升就衰减"这个粒度
可能太粗——呼应旧CONTEXT.md老问题"checkpoint粒度太粗"，run-1在chat小规模上
验证过这个疑虑不成立，但那是小数据，这次规模上去后需要重新看）。

## Phase 1：跑上面指定的训练/评估集设计

用Phase 0定好的早停超参，跑一次，出4张表格式的报告。这是run-2的第一个正式
结果，用来判断：①这个规模下模型能不能追平/超过run-1的天花板（val
peak_dist 6.04）；②WordWolfExp test（G1_game3~6）上能不能像旧PC
`C_g1g2_train_g3_test`那样清楚超过exp4/朴素基线（哪怕训练组换了、规模更大）。

## Phase 1/2 结果（已完成，详见CONTEXT.md）

- **Phase 1**（chat+WordWolfExp 74bag+GRP_meeting 44bag混合训练，G1_game3~6
  held-out）：**正向**，WordWolfExp held-out上peak_dist 9.14 vs exp4 12.23
  vs 朴素基线11.31，全面超过。打破了run-1 chat-only的天花板。
- **Phase 2**（同配方，held-out组换成G8，交叉验证）：**正向且稳定**，
  peak_dist 11.94 vs exp4 14.07 vs 朴素基线13.36，方向一致，不是G1偶然。

## Phase 3 结果（已完成）

试了clip_len=20对照（同Phase 1训练池，唯一变量clip_len 10→20）：**基本打平，
没有清楚胜负**（三个场景互有胜负，差距都在噪声范围内），但耗时2倍、参数量
3倍——性价比明显更差，clip_len=10仍是更实际的默认选择。详见CONTEXT.md。

## Phase 4 结果（已完成，负结果）

推全量283-bag（新增157个bag，无held-out设计，全部进训练）：**在已有3个
评估域上没有进一步提升**，三个场景都在噪声范围内（chat略差、WordWolfExp
基本打平微升、GRPMTG略好）。121→283bag数据量翻倍没有带来收益。详见
CONTEXT.md，这是个值得深挖的负结果——瓶颈可能不在数据量本身。

## 后续方向（全部已完成/已收尾，2026-07-21）

- ~~给新加的157个bag设计held-out测试组~~——**已完成**：held出
  ATR_RIKEN_1F，训练混入其余108个bag，结果明确负面（比零shot基线还差
  一倍多，且预测随步长发散），推测是混入了`ATR_RIKEN_3f`这类"相近但
  不同"场景导致负迁移。详见CONTEXT.md"157-bag held-out测试"小节。
- ~~排查Phase 4"数据量翻倍没提升"的具体原因~~——**已完成**：early
  stopping用Phase4自己的log.csv排除（continuing只会变差，不是太早停）；
  模型容量用过拟合探针+正则化排查排除（容量够用，缩容量还帮倒忙）。
- `Demonstration_Data_nonconv`单独消融——**决定不做**，run-2已收尾，不再
  在这条线上投入。

**暂缓、留档给以后重新立项参考**：157-bag里排除`ATR_RIKEN_3f`这类近似域后
的更细粒度重测、domain-invariant/领域自适应类结构性方法——见CONTEXT.md
"run-2收尾"一节。

## 复用run-1的基础设施

- `dataset.py`需要泛化：chat/GRPMTG用"时间切分train/val"，WordWolfExp用
  "整组train/整组test"，三种窗口分别建Dataset，训练时`ConcatDataset`合并成
  一个train_loader，评估时三个val/test集分开算，同时也算一次合并的"总的"。
- `report.py`模式复用（exp4+朴素基线固定对照，t+1~t4分步/t+2重点），但要
  从run-1的"1个总表"扩展成"总表+3个分场景表"这4块结构。
- LR衰减联动早停延续，**实际执行时Phase 1~4全部沿用了run-1原封不动的超参**
  `patience=3/decay=0.5/max_decays=3/min_lr=1e-6`——Phase 0原计划"按新规模
  重新调"没有真正做（只是观察了每次的epoch1耗时，没有改早停粒度本身）。
  这本身现在是"后续方向"里的一个待排查项（early stopping在大数据下是否
  依然过早）。
- `bs=32`默认延续——run-1已确认bs对结果影响很小，除非Phase 0发现显存/速度
  有明显问题才重新考虑。

（run-1已归档到`archive-runs/old-runs-2/run-1/`，见CONTEXT.md。MTG held-out
bag用的是`GRP_meeting_2025-01-16-13_08_04`，Phase 1~4全程没变。）
