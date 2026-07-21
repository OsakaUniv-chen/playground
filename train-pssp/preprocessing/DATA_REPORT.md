# 数据来源与规模

数据来源：`/media/chen/Extreme SSD/PSSPData/`。提取脚本 `preprocessing/build_dataset.py`
的 `JOBS` 注册表定义每个集合的路径。全部集合统一进 `train-data/`，没有
`train-data-aux/`。

**2026-07-21 更新，registry 落后于 train-data/ 实际内容**：`train-data/`新增了
`expo_reception_2025`（167 bag）和`indy_teleoperation`（23 bag）两个数据集
（详见 `train-pssp/CONTEXT.md`/`run-3/PLAN.md`），下面两张表已按`index.csv`
现状刷新，包含这两个新集合。但`build_dataset.py`的`JOBS`列表**还没有**这两个
集合的`Job(...)`条目——这两批npz的生成时间晚于`build_dataset.py`最后一次修改，
是绕开当前registry产出的，真实的`root`源目录路径未经核实（这台机器上
`/media/chen/Extreme SSD/PSSPData/`当前未挂载，无法直接确认）。**需要负责人
补充这两个Job条目的`root`路径**，在此之前`JOBS`不是"一个脚本、一份来源登记"
这个约定的完整来源，只能反映到283-bag（不含expo/indy）为止的历史状态。

## 表1：总规模（train-data/）

| 场次数 | 2Hz 采样点(ticks) | 约合时长 | npz 磁盘占用 | QC 视频磁盘占用 |
|---|---|---|---|---|
| 473 | 450,110 | 62.52 小时 | 见下 | 见下 |

（"时长"按 2Hz tick 数 ×0.5s 折算，即模型实际会看到的采样点数，不是原始录制
时长。准确数字以 `train-data/index.csv` 为准，本表是某一时刻的加总快照。npz/QC
视频磁盘占用this次刷新未重新测量——`train-data/`当前合计4.1GB，`soundmap-videos/`
在这台机器上不存在（未同步/未生成，见下"QC视频"节），旧数字283-bag时是
2.2GB/11GB，不再准确，留空待下次实测。）

## 表2：各数据集规模

| 数据集 | 场次数 | 2Hz 采样点(ticks) | 约合时长 |
|---|---|---|---|
| **expo_reception_2025**（新，2026-07-21） | 167 | 188,259 | 26.15 小时 |
| GRP_meeting | 45 | 80,189 | 11.14 小时 |
| WordWolfExp | 78 | 39,118 | 5.43 小时 |
| ATR_RIKEN_1F | 49 | 36,016 | 5.00 小时 |
| ATR_RIKEN_3f | 28 | 21,635 | 3.00 小时 |
| **indy_teleoperation**（新，2026-07-21） | 23 | 20,376 | 2.83 小时 |
| olab_rev_0630 | 13 | 16,538 | 2.30 小时 |
| olab_0630 | 13 | 15,996 | 2.22 小时 |
| egoSAS_test_data | 8 | 10,826 | 1.50 小时 |
| Experiment_EXP | 15 | 6,741 | 0.94 小时 |
| chat | 3 | 6,486 | 0.90 小时 |
| riken_3f | 8 | 3,482 | 0.48 小时 |
| Testrun0420 | 3 | 1,489 | 0.21 小时 |
| kitchen | 2 | 1,331 | 0.18 小时 |
| demo_data_0318_becap | 8 | 1,253 | 0.17 小时 |
| Demonstration_Data | 6 | 242 | 0.03 小时 |
| Demonstration_Data_nonconv | 4 | 133 | 0.02 小时 |

## QC 视频

`soundmap-videos/`（每个bag一份mp4，见下"命名约定"）在这台机器的工作目录里
当前不存在——历史上体积较大（283-bag时11GB），大概率是本地生成产物，没有随
仓库同步/提交到这台PC。**expo_reception_2025/indy_teleoperation这两个新集合
目前没有可供人工抽查的QC视频**，如果需要目视确认这两个场景的内容（人数、
机位、有无机器人噪声等），要么在原本生成这批npz的机器上看，要么等
`/media/chen/Extreme SSD/PSSPData/`能在这台机器挂载后用`build_dataset.py`补跑
（npz已存在，只需要`--video-only-bag`式的补视频路径，不会重新触发GPU声图
生成）。

## 命名约定

`train-data/` 里的文件名：WordWolfExp 用 `{bag}.npz`（如 `G1_game4_PSSP.npz`），
其余所有数据集用 `{数据集名}_{bag}.npz`（如 `GRP_meeting_2025-01-16-13_08_04.npz`）。
`index.csv` 的 `group` 列对应上表的数据集名（WordWolfExp 例外，`group` 是 G 编号
1~13）。`soundmap-videos/` 下的目录结构**镜像 PSSPData 源盘的层级**，每个 bag 一份 mp4，
文件名和 npz 同名，例如 `soundmap-videos/Meeting/GRP_meeting/GRP_meeting_2025-01-16-13_08_04.mp4`、
`soundmap-videos/ATR_teleoperation/data_RIKEN_1F/<bag>.mp4`。WordWolfExp/Experiment_EXP/
Testrun0420 共用同一 `job.root`，故都落在 `WordWolfExp/` 一个文件夹里，与源盘一致。
`train-data/` 里的 npz 仍是扁平的，只有视频按源盘树分文件夹。`build_dataset.py`
新生成的视频也遵循此规则（`video_path_for()` / `_rel_under_source()`）。
