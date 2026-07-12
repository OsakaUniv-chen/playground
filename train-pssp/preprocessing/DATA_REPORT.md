# 数据来源与适用性报告

覆盖 `/media/chen/Extreme SSD/PSSPData/` 下的全部集合（2026-07 起 WordWolfExp 也
并入这个盘统一管理，不再是外部单独路径）。记录每个来源的场次数/时长，以及提取
脚本用到的分类信息。提取脚本是 `preprocessing/build_dataset.py`，来源清单硬编码
在其中的 `JOBS`，本文档是那份清单的场次规模记录。

**2026-07-12 更新（PSSPData 整盘重组后的全量重跑）**：负责人把所有数据集重新整理
到 `PSSPData/` 下（`WordWolfExp` 并入、`Meeting/`/`egoSAS_demo_data/` 子目录重新
分层、新增 `chat`/`Testrun0420`/`Demonstration_Data`/`kitchen` 等集合、
`GRP_meeting`/`riken_3f`/`egoSAS_test_data`/`ProjectMobileRobot_3f`/
`demo_data_0318_becap` 均有新场次上传）之后，**全部集合统一重新提取 npz + QC
视频，且全部放入 `train-data/`**——`train-data-aux/` 目前是空的，负责人明确要求
"不做默认排除判断，全部先进 train-data，之后我来判断哪些该挪到 aux"。之前版本
本文档里"结构性存疑/成分混杂路由到 aux"的判断（摄像头会动的集合、
`Demonstration_Data` 非对话片段等）**判断依据仍然成立，只是执行方式变了**——
这些数据现在也在 `train-data/` 里，等待负责人后续决定是否挪走，不是已经被认定
"没问题"。

## 集合清单（2026-07-12 全量重跑后）

| 集合 | 场次数 | 摄像头 | 备注 |
|---|---|---|---|
| WordWolfExp | 78（G1~G13） | 固定 | 本项目基础数据集 |
| Experiment0312 | 5 | 固定 | boxie 同款设置，WordWolfExp 直接前身 |
| Experiment1126 | 10（EXP1_\*+EXP2_\*） | 固定 | |
| olab_0630 | 13 | 固定 | 实验室多人开会 |
| olab_rev_0630 | 13 | 固定，画面颠倒 | `camera_flip=True` |
| **GRP_meeting** | **44**（原 5，新增 39） | 固定 | 实验室多人开会；新增场次含一场 2.15
  小时超长录音（`2025-07-17-13_06_49`），处理时暴露了一个内存 bug，见下 |
| chat | 3 | 固定 | 两人自由讨论 |
| Testrun0420 | 3 | 固定 | 疑似部署测试录制 |
| Demonstration_Data | 6 | 固定 | 抽帧确认两人/多人对坐交流 |
| Demonstration_Data_nonconv | 4 | 固定 | 抽帧确认无对话轮换（空房间/单人走过） |
| demo_data_0318_becap | 8（原 6，新增 2） | ❌ 会动 | 移动机器人演示场景 |
| ProjectMobileRobot_3f | 4 | ❌ 会动 | 移动机器人 |
| riken_3f | 8（原 1，新增 7） | ❌ 会动 | |
| egoSAS_test_data | 9 | ❌ 会动（第一视角） | 音频消息编码也和其他集合不同 |
| **kitchen** | **2（新集合）** | 固定 | `egoSAS_demo_data/kitchen`，之前没处理过 |
| expo_2025 | 0 | — | 空文件夹，无 `.db3`，无法提取 |

## 最终规模

| | 场次数 | 2Hz 采样点(ticks) | 约合时长 | 磁盘占用(npz) |
|---|---|---|---|---|
| **train-data/** | 210 | 186,193 | 25.86 小时 | 1.7GB |
| **train-data-aux/** | 0 | — | — | — |

（"时长"按 2Hz tick 数 ×0.5s 折算。准确数字以 `train-data/index.csv` 为准，本表格
是某一时刻的加总快照。QC 视频总占用约 13GB，在 `soundmap-videos/`，不参与训练。）

## 本轮重跑记录（2026-07-12）

### 1. 视频格式换成完整的 QC 面板设计

之前版本的 QC 视频只是 exp(x-max) 归一化声图叠加在摄像头画面上（黄色伪彩色）。
这次改成和 `video-generator/bag2video.py` 完全一致的设计：主画面（声图叠加摄像头，
若有 `/head/head_box` + `/room2_audio/vad` 话题则叠加 head-box/speaking-box/
4-label 标注）下方竖直拼接一条滚动面板，显示 room1 RMS 音量包络（灰色）+
silero-VAD 语音段（蓝色）+ 有 4-label 数据时额外显示 Left/Right/Teleoperator/
Others 检测条。`/head/head_box`、`/room2_audio/vad` 只有 WordWolfExp 的 PSSP/DoA/
Tele 模式和 `Testrun0420` 才有，其余集合优雅降级为只有 VAD 条的版本。

### 2. 内存 bug：单声道降混顺序错了

`VideoPanel.__init__` 原来是先把整段音频的全部 16 声道拼接成一个大数组，再
`.astype(float32)`，最后才 `.mean(axis=1)` 降到单声道——对 2.15 小时的
`GRP_meeting_2025-07-17-13_06_49` 这种超长 bag，这一步单独就要 ~33GB
（int16 拼接后 ~11GB + float32 拷贝 ~22GB），直接把整机 OOM 崩溃过一次。**修复**：
改成先对每个音频消息块分别降混到单声道再拼接，拼接结果直接就是最终单声道大小
（~1.36GB），不再产生中间的全声道大数组。同时给每个 bag 的 video-only 重跑用独立
subprocess 隔离（`--video-only-bag` CLI 分支），确保任何单个 bag 的内存占用不会
跨 bag 累积。

### 3. index.csv 曾经漏记 39 行（已修复，教训记录在案）

`process_job()` 的 `write_index()` 只在处理完一个 job 的**全部** `todo_full` 列表
后才调用一次，写入内存里 `existing` 字典的当前状态。如果进程在某个 job 的
`todo_full` 循环中途被打断（这次重跑过程中确实发生过——内存崩溃、以及负责人中途
叫停重来），已经成功 `save_npz()` 落盘的 bag 不会丢，但它们的 index 行永远不会被
写入，因为 `write_index()` 根本没跑到。**这次实际发现**：`GRP_meeting` 44 个 npz
全部在盘上，但 `index.csv` 只有 5 行（原有的 5 个老场次），39 个新场次的 npz 是
"孤儿"——训练脚本如果依赖 `index.csv` 做 split，会静默看不到这 39 个 bag，即使
文件确实存在。**已用每个 npz 自带的 `tick_ts`/`soundmap.shape[0]` 反推
`dur_s`/`n_ticks` 手动补全这 39 行**，不需要重新提取。**没有修复代码本身**（这个
中断窗口很难消除，process_job 每个 bag 处理完就该立即增量写 index 而不是攒到最后
才写一次，是更根本的修复，但这次先手动补齐数据，逻辑改动留给以后需要时再做）。

## 命名约定

`train-data/` 里的文件名：WordWolfExp 用 `{bag}.npz`（如 `G1_game4_PSSP.npz`），
其余所有集合用 `{collection}_{bag}.npz`（如 `Experiment0312_EXP3_PSSP.npz`），
避免不同集合之间的场次命名冲突。`index.csv` 的 `group` 列对应上表的集合名
（WordWolfExp 例外，`group` 是 G 编号 1~13；`Demonstration_Data`/
`Demonstration_Data_nonconv` 共享文件名前缀 `Demonstration_Data_`，靠 `group` 列
区分）。

`soundmap-videos/` 下是每个 bag 对应的一份 mp4，文件名和 npz 同名，不按
train-data/train-data-aux 分子文件夹，用于目视抽查提取质量，不参与训练。格式见
上面"本轮重跑记录 1"。
