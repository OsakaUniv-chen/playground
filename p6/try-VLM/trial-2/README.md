# trial-2 — VLM 能否在真实 Word Wolf 场景里读出音源方向（4-label）

trial-1 用抽象的「几点方向」测最底层的读图能力（结论：本地 3B 读不了）。**trial-2 换成
P5 Word Wolf 实验的真实数据**，用它天然的 **4-label** 直接问「谁是音源」，更贴近 P6 实任务。

## 任务

- **输入**：一张图 = **鱼眼灰度画面 + jet 音图叠加**（红=最响）。画面里左右各一名本地玩家，
  下方中央（桌子/机器人处）是**远程操作者**的固定区域。
- **提示词**（`02_probe.py PROMPT`，即你的设计）：告诉 VLM「音图红峰落在下方中央区域＝远程操作者在说话」，
  然后让它在 4-label 里选音源。
- **输出**：`Left / Right / Teleoperator / Others` 之一。
- **判定**：与真值 `gt_label` 比，算 **4-label 精度**（随机基线 25%）。

## 4-label 是什么

来自 `word-wolf-exp-eval/utils/labeling.py`（从实机代码原样搬出）：把音图 resize 到 1080，
在三个区域取分位数指标、取最大者定标签——
- **Left / Right**：两名玩家的**头框**（MediaPipe 检测，存在 tick 表 `hb_*`）
- **Teleoperator**：固定的 speaking box `(377,645,330,330)`（下方中央）
- **Others**：都不在上面时兜底

## 数据与忠实性（关键）

tick 表 `behavior-analysis/results/ticks/{bag}.parquet` 里已有 `gt_label`、头框、`vad_active`，
但**没存音图和相机帧**（extract.py 去掉了 --save-sm）。所以每个采样 tick **按实机方式重生成音图**
（`SoundMapAPI`，和算 gt_label 时同一套）并抓相机帧。

**喂给 VLM 的音图 = GT 标注器看到的同一张**：`label_current_sm` 会
①当 `vad_active=False` 时**遮蔽（置零）远程操作者的 speaking box**，②再 `exp(sm-max)` 变换。
trial-2 把这张**遮蔽+变换后**的音图以 jet 叠在灰度图上——所以这是「你能不能像几何规则一样读出音源」
的公平对照。图上**不画任何框**（画框会泄露 Left/Right），远程操作者区域只在提示词里用文字说明。

## 文件

```
common2.py    连 word-wolf 仓库 / 开 bag / 重生成音图 / mask+transform / jet 叠加渲染
01_sample.py  跨 bag 按 4-label 均衡采样 -> manifest2.csv + images/ (+ qc_sheet.png 人工核对)
02_probe.py   Qwen2.5-VL-3B(4-bit) 4-label 探针 -> results/probe_results.csv
03_analyze.py 4-label 混淆矩阵 + 精度
```

## 跑法

```bash
source ~/.virtualenvs/wolf/bin/activate
python 01_sample.py                 # 采样+重生成+渲染（要挂载装 bag 的外置 SSD）
python 02_probe.py                  # VLM 打 4-label（4-bit）
python 03_analyze.py                # 混淆矩阵
```

## 结果（2026-07-21，Qwen2.5-VL-3B-Instruct, 4-bit）

160 帧、4-label 各 40（Left/Right/Teleoperator/Others 均衡）。

| 模型 / prompt | 4-label 精度 | Left | Right | Teleoperator | Others |
|---|---|---|---|---|---|
| 3B, v1（末行有 `<one of ...>` 占位符） | 28.7%（随机 25） | 0/40 | 0/40 | 20/40 | 26/40* |
| **3B, v2（清理占位符）** | **25.0% ＝纯随机** | **0/40** | **0/40** | **40/40** | **0/40** |
| **7B, v2** | **28.1% ≈随机** | **0/40** | **5/40** | **40/40** | **0/40** |

**结论：3B 完全塌缩成常数 `Teleoperator`；7B 也基本塌缩（155/160 答 Teleoperator），
但露出一丝真实能力——它答 `Right` 的 5 次全对（precision 5/5），响应写明"red peak is located
over the right player"。即更大模型有微弱的读图能力，但远不可用（96% 仍默认 Teleoperator，
Teleoperator 的 precision 只有 26%＝底噪，Left/Others 恒 0）。**

- **v2 对全部 160 帧都答 `Teleoperator`**（`results/probe_v2.csv`）。精度 25% 就是「40 个 Tele 真值 ÷ 160」的底噪，**零信息**。混淆矩阵一整列（见 `results/probe_v2_confusion.png`）。
- **它在幻觉、没看图**：GT=Left/Right（红峰明明压在左/右玩家身上）时，它照样输出
  「The red peak is located in the lower-centre region … the remote Teleoperator is the sound source」。
  ＝ 它没读图像，只是**挑了提示词里描述最多、最显眼的那个选项**（Teleoperator 的说明最长）。
- **v1 的 28.7% 是假象**：其中 86/160 根本没按格式作答（照抄了 `<one of ...>` 占位符，被我旧解析器错当成 Others），61/160 是裸 `Teleoperator`，同样从不选 Left/Right。清理占位符后（v2）真面目就是"永远 Teleoperator"。
- **Left/Right 恒为 0**：和 trial-1「读不出左右方位」完全一致——换成真实场景 + 灰度上下文 + 区域提示，也救不回来。

## 与 trial-1 的关系 & 下一步

trial-1（纯热力图问几点）和 trial-2（真实场景问 4-label）**双双确认**：本地 Qwen2.5-VL-3B
没有「读出音源方位」的能力，会塌缩成固定答案、无视图像。所以：

1. **本地 3B / 7B 这一档对 P6「音图喂 VLM」都不可用**（7B 只比 3B 多出 5 个正确 Right）。
2. **要回答「到底有没有 VLM 能读懂音图」，得上前沿 API**（Claude / GPT-4o / Gemini）。
   7B 已证明 8GB 本地档不够；数据/渲染/提示词管线已就绪，换 API 版推理即可复用这 160 帧。
3. 若前沿模型也塌缩 → 图像叠加表示判死，退回 proposal §4.0 的「文本方位角」表示。
4. caveat 同 trial-1：本结果是 4-bit，GPU 空时可 `--bf16` 复跑排除量化混淆。

\* v1 的 Others 26/40 主要是解析假象（占位符回显被当成 Others），非真实能力。
