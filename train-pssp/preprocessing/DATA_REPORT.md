# 数据来源与规模

数据来源：`/media/chen/Extreme SSD/PSSPData/`。提取脚本 `preprocessing/build_dataset.py`
的 `JOBS` 注册表定义每个集合的路径。全部集合统一进 `train-data/`，没有
`train-data-aux/`。

## 表1：总规模（train-data/）

| 场次数 | 2Hz 采样点(ticks) | 约合时长 | npz 磁盘占用 | QC 视频磁盘占用 |
|---|---|---|---|---|
| 283 | 241,475 | 33.54 小时 | 2.2GB | 11GB |

（"时长"按 2Hz tick 数 ×0.5s 折算，即模型实际会看到的采样点数，不是原始录制
时长。准确数字以 `train-data/index.csv` 为准，本表是某一时刻的加总快照。）

## 表2：各数据集规模

| 数据集 | 场次数 | 2Hz 采样点(ticks) | 约合时长 |
|---|---|---|---|
| WordWolfExp | 78 | 39,118 | 5.43 小时 |
| ATR_RIKEN_1F | 49 | 36,016 | 5.00 小时 |
| GRP_meeting | 45 | 80,189 | 11.14 小时 |
| ATR_RIKEN_3f | 28 | 21,635 | 3.00 小时 |
| Experiment_EXP | 15 | 6,741 | 0.94 小时 |
| olab_0630 | 13 | 15,996 | 2.22 小时 |
| olab_rev_0630 | 13 | 16,538 | 2.30 小时 |
| demo_data_0318_becap | 8 | 1,253 | 0.17 小时 |
| egoSAS_test_data | 8 | 10,826 | 1.50 小时 |
| riken_3f | 8 | 3,482 | 0.48 小时 |
| Demonstration_Data | 6 | 242 | 0.03 小时 |
| Demonstration_Data_nonconv | 4 | 133 | 0.02 小时 |
| Testrun0420 | 3 | 1,489 | 0.21 小时 |
| chat | 3 | 6,486 | 0.90 小时 |
| kitchen | 2 | 1,331 | 0.18 小时 |

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
