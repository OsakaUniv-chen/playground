# 博论主线（当前状态）

> 记录博论故事主线的最新结论，随讨论更新，非正式文档，可大改。
> 最后更新：2026-08-04，依据 2026-07-31 组会（`meetings/recordings/2026-07-31/2026-07-31 16-30-40.summary.md`）及其整理稿 `reports/report1/proposal.md`（P3.2 提案 v3）。
> 本文件取代旧的 `proposal1/proposal2` 下 `mainline-notes.md`（已归档，可通过 `git show 3d933e2:doctoral-thesis/proposal1/mainline-notes.md` 找回）。

---

## 1. 当前章节结构（P1 → P2 → P3.1 → P3.2）

| # | 内容 | 贡献 | 出处/状态 |
|---|---|---|---|
| **P1** | 商业设施接客 android，视觉 + 专家（远程操作）示范的 IRL | 在真实现场达到人类操作者水平 | RA-L 2022 |
| **P2** | 同任务，视觉 + 自主探索的 RL，现场自我改善 | **超越熟练人类操作者** | RA-L 2023 |
| **P3.1** | 确立音图（sound map）为可与视觉融合的新模态 | 携带对话相关的空间线索 | IEEE Access 2025 |
| **P3.2** | 真实可变人数场景下，视觉·空间音响多模态控制则的学习（本提案，Bystander Robot / Robotic Signage） | 音图补上视觉/语言解决不了的对象选择；聚焦真实部署新问题 | 新规（拟投稿），提案 v3 见 `reports/report1/proposal.md` |

**编号说明**：2026-07-31 组会决定章节编号从旧的「1.2.3.4」改为「1.2 / 3.1 / 3.2」。P4（JSAI 2026，word wolf pilot）与 P5（word wolf 受控实验：13 组/39 人，核心发现「行为↔知觉乖离」）在新编号下是否单独成章、还是作为 P3.1→P3.2 之间的支撑性工作，**尚未明确，待下次组会确认**。完整结果分析见 `word-wolf-exp-eval/report2/report.md`。

## 2. P3.2 现状速览

- **场景**：收敛为「旁观者机器人 (bystander robot) / Robotic Signage」——机器人站在已有交互界面（触摸屏/菜单牌/自制简化数字标牌）旁，帮助路人意识到"这个东西可以互动"，而非自己成为信息源。
- **方法**：主路线＝预训练 VLM（不 finetune）prompt 决策；备选＝沿用 P1-2 的 IRL/RL 路线；VLA 暂不做。
- **已撤回**：此前「VLM 读声图正解率 91.5%/91.7%」不再作为声图价值的证据——该评测被指出是数据泄露式评测（本质是颜色/位置匹配 + 人脸检测，与声学理解无关）。
- **场景定位**：由"他人数"改为"**可変人数**"（0 到多人动态变化），因为 P1/P2 是固定人数场景。
- **下一步 TODO**（详见 `reports/report1/proposal.md` §8）：
  1. Bystander robot 最小 demo（天气预报截图喂 VLM）
  2. 声图在新场景中具体如何起作用（待细化）
  3. 与フランコ君对接テレオペ底层系统
  4. VLM/VLA 文献调研

## 3. 博论标题

**当前拟定稿**（尚未与导师确认，可能因"过于一般化"被打回，届时再调整）：

- JP：実世界・可変人数環境における社会ロボットの行動決定学習
- EN：Learning Behavior Decision-Making for Social Robots in Real-World, Variable-Population Environments

删除了"能動的"（过于宏大笼统）、"音響情報/音響マップ"（会把纯视觉的 P1/P2 排除在外）；"他人数"改为"可変人数"。若后续觉得过于泛化，可考虑加副标题点出具体贡献（如"対話相手選択"/"多感覚知覚"等方向，讨论见对话记录，未采纳）。

## 4. 参考

> 以下是给 AI（未来协作会话）看的索引和背景存档，人类读者可跳过本节。

- `reports/report1/proposal.md` — P3.2 最新完整提案（v3，2026-07-31 组会整合版），本文件 §1-2 的详细依据
- `meetings/recordings/2026-07-31/2026-07-31 16-30-40.summary.md` — 本次结构调整的会议记录
- `word-wolf-exp-eval/report3/next-study/proposal-japanese2.md` — P3.2 v2（已被 v3 取代）
- `word-wolf-exp-eval/report3/next-study/context.md` — 更早背景与已否决方向完整记录
- `word-wolf-exp-eval/report2/report.md` — P5 word wolf 受控实验完整结果分析
- 历史 mainline-notes（P1-P5 完成工作详细速览、旧版"banner+内线"两层框架，已被本文件取代）：`git show 3d933e2:doctoral-thesis/proposal1/mainline-notes.md` 与 `proposal2/mainline-notes.md`
- **已否决方向存档（避免重蹈）**：会话 facilitator、点单机器人、抢答机器人——均因"本质是识别问题"或"应用不吸引人"被否；分发纸巾场景已并入 Robotic Signage。更早一轮的否决记录见 `word-wolf-exp-eval/report3/next-study/context.md` §5.2。
