# stream-server（rog-server）

OME（所有流的枢纽）和**人物检测节点**（录制触发的发出方），外加**语音转文字**。
局域网内用 `rog-server.local` 寻址（静态地址 192.168.1.10）。
设计见 [../../system-architecture.md](../../system-architecture.md) §3、§6.2。

```
OME(localhost) ──WebRTC──▶ 鱼眼 1080×1080 ──YOLO──▶ 要不要录（bool）
                                                        │ 5 Hz
                                                        ▼
                                          std_msgs/Bool ──▶ robot-pc
                            /<ROBOT_NAME>/record/trigger
```

**发出去的只有一个 bool。** 判断在这边，记录在那边。

| 文件 | 内容 |
|---|---|
| `person_detect.py` | 检测节点本体。**算法参数全在文件顶部的配置段** |
| `config.env` | 部署参数（OME 地址、stream key、ROS 名字、用哪个 Python）。**唯一出处** |
| `ome_receiver.py` | 从 OME 收 WebRTC。**这份是源头** —— robot-pc / vlm-server 各带一份逐字节相同的副本，改完记得同步 |
| `recv.py` | 从 OME 收多路的封装（三条流 → 四路数据）。**这份是源头**，vlm-server 带一份副本 |
| `soundmap_overlay.py` | 鱼眼 ＋ 声音图的叠加流（`rgb_sm`）。独立进程 |
| `asr.py` | **语音转文字**（faster-whisper）。两路音频 → 文字，给 vlm-server 用 |
| `srt_out.py` | 把 BGR 帧推回 OME 的共用出口（SRT） |
| `../run_stream.sh` `../run_overlay.sh` `../run_asr.sh` | 起动，都**在 `rog-pc/` 那一层** |
| —— | 没有自定义 msg。录制触发就一个 `std_msgs/Bool` |

## 能在 OvenPlayer 上看的流

```
ws://rog-server.local:3333/app/<stream key>
```

手边没有页面时，<https://demo.ovenplayer.com/> 填上面这个地址也能看。

| 看什么 | stream key | 内容 | 谁推的 |
|---|---|---|---|
| 鱼眼 ＋ 机体麦克风 | `fisheye` | H.264 1080×1080@30 ＋ AAC | robot-pc |
| 机体麦克风（单独一条） | `onboardmic` | AAC 48 kHz 1ch | robot-pc |
| 声音图 | `soundmap` | H.264 64×64@15，黑底黄斑 | robot-pc |
| RealSense | `realsense` | H.264 848×480@30（仅 color） | robot-pc |
| 导航相机 | `navcam` | H.264 1280×720@30 | robot-pc |
| 操作者语音 | `operatormic` | AAC 48 kHz 1ch | tele-pc |
| **鱼眼 ＋ 声音图** | **`rgb_sm`** | H.264 1080×1080@15 | **stream-server** |
| **鱼眼 ＋ 检测框 ＋ 录制标志** | **`human_detect`** | H.264 1080×1080@5 | **stream-server** |

`operatormic` 是反方向那条：tele-pc 推上来、robot-pc 拉下去放到机体扬声器。
在播放器里打开它听到的是操作者自己说的话，用来确认下行链路通没通。

### 两条合成流

**只是给人看的监视流，不进 bag、不参与任何判断。**

| | `human_detect` | `rgb_sm` |
|---|---|---|
| 谁生成 | `person_detect.py`（顺带） | `soundmap_overlay.py`（独立进程） |
| 起动 | `~/rog-pc/run_stream.sh` | `~/rog-pc/run_overlay.sh` |
| 节奏 | 5 fps，跟着检测走 | 15 fps |
| 画面上有什么 | 每个检测框 ＋ conf；**右上角 REC/IDLE ＋ 当前分数 ＋ 框数**；左上角墙钟时刻 | 鱼眼 ＋ 黄色声音图叠加 |
| 一眼能确认 | 检测在不在工作、现在在不在触发录制 | 声音图的斑点和画面里的人对不对得上 |

`rgb_sm` 的叠加式子（和 QC 视频 `soundmap-generator/soundmap-video/bag2video.py` 一致）：

```
sm_color = 黄色化(声音图 INTER_LINEAR 放大到 1080)
blend    = addWeighted(sm_color, 0.6, cam, 0.8, 0)
```

**要定量分析请用 bag 里的 `soundmap/map`**（float32 原始值），不要用这条流 ——
它是有损 H.264、还叠了底图。声音图断了会自动回到素画面。

**★ `rgb_sm` 没有做时间戳对齐** —— 拿到哪帧就叠哪帧。作为监视流够用，
但这条流不能当作有时刻含义的数据（架构 §8）。

### ★ OME 的僵尸流

发布进程死得不干净（SIGKILL、崩溃、掉电）之后 OME 会留下一个同名的流，
之后再推同名流一律被拒：

```
Reject to add stream : there is already an incoming stream (human_detect) with the same name
```

**正常情况会自己恢复**：OME 几秒内自己把僵尸流放掉（SRT 超时），`srt_out.py`
的 5 s 退避正好跨过去，**只重建一次，进程毫发无伤**。只有当日志里一直在
「重建推流（第 N 次）」而 OME 一直回上面那句时，才需要
`sudo systemctl restart ovenmediaengine`。

**★ 只在 GStreamer 1.24 上成立。** 1.20（开发机）里 srtsink 撞上僵尸流会直接
把进程 abort，Python 侧拦不住。生产的 rog-server 是 1.24.2。

## 检测

前向广角鱼眼，人基本是竖直的 → **不做展开，YOLO11 直接跑原图**，取 COCO 的
person 类，**逐帧检测不做跟踪**，后面接一个漏电积分做时间上的迟滞。

**目标是「只要画面里有真人就录」**，按 recall 优先挑参数（10 m 处的人在画面里
只有 60～100 px 高）。

**rog-server 实测**（1080×1080 输入，含前后处理和 NMS，`device=cuda:0`）：

| 组合 | 中位 | 占 200 ms 预算 |
|---|---|---|
| `yolo11m` @ 960 | 29.9 ms | 15% |
| `yolo11m` @ 1088 | 39.5 ms | 20% |
| `yolo11x` @ 960 | 75.8 ms | 38% |
| **`yolo11x` @ 1088（默认）** | **97.8 ms** | **49%** |
| `yolo11x` @ 1280 | 133.9 ms | 67% |

### 判定：漏电积分

检到人加 1 分，没检到漏 `DECAY` 分；升到 `ON_TH` 置位，漏干才落下。

```
每 tick 平均变化 = p × 1.0 − (1−p) × DECAY
盈亏平衡点  p* = DECAY / (1 + DECAY) = 0.20
```

**`DECAY` 这一个旋钮就定义了「检出率低于 20% 的一律当噪声」。** 实测：

| 场景 | 结果 |
|---|---|
| 近处的人走进画面 | 2 个 tick（0.4 s）置位 |
| 待了一阵再走掉 | 40 个 tick（8.0 s）落下 |
| 一闪而过的误检 | 8 个 tick（1.6 s）落下 |
| 检出率 10% / 15% | 落下（当噪声） |
| 检出率 25% / 35% | 撑住（当真人） |

**★ 这条线对两边是同一条** —— 一个真人如果只有 15% 的帧被检到（比如 15 m 外）
同样触发不了。所以 `DECAY` 要卡在两个实测值中间：

```
无人清场时的误检率  <  p*  <  10 m 处真人的检出率
```

这两个数要现场量（人站 5 / 10 / 15 m 各 30 秒，`--save-vis` 看 conf 和检出率）。
**在那之前 `CONF=0.35` / `DECAY=0.25` 是待定值。**

### 换相机的话

现在的做法**只在「人是竖直的」时成立**。

| 相机形态 | 该怎么做 |
|---|---|
| 前向广角，成像圆内切（**现在**） | 原图直接跑 |
| 圆形全向、朝上环视（会议相机） | **必须先展开成全景条带**再检测 |
| 俯视鱼眼（吊装） | 同上；或用 RAPiD（专为俯视鱼眼训练的旋转框检测器） |

后两种要先量出成像圆的 `(cx, cy, R)`。现在这颗（CX-MT500 裁中央 1080×1080）
是 `(540, 540, 540)`。

## 三个要知道的行为

**① 输入断了 → 停止发布，不是发 `false`。** robot-pc 在 `TRIGGER_TIMEOUT_SEC`
（3 s）收不到消息会 fail-open 退化成连续记录，那是我们要的。发 `false` 等于
告诉它「确实不用录」。

**② 必须跑在 GPU 上，起不来也不许退化。** ultralytics 的 `predict` 自己会
`select_device('')`，CUDA 不可用时**默默**挑 cpu 不报错，那时一帧一两秒而日志
看起来全都正常。所以做了三道：启动查 `torch.cuda.is_available()`、每次推理显式
传 `device="cuda:0"`、预热后断言 `predictor.device.type == "cuda"`，
外加中位耗时超 300 ms 的兜底告警。

**③ QoS 两边必须对上。** 这里发 `BEST_EFFORT` ＋ `depth=1`。**robot-pc 的
subscriber 如果用默认的 `RELIABLE`，两边不兼容，一条消息都收不到而且 ROS 不
报错** —— 表现和「检测节点没起来」一样，然后 recorder 一路 fail-open 连续记录，
盘满了才发现。`ros2 topic info -v` 能看出 QoS 对不对得上。

## 跑

**启动脚本在上一层**（`~/rog-pc/`）：

```bash
cd ~/rog-pc
./run_stream.sh              # 生产：从 OME 拉，发 ROS
./run_stream.sh test         # 不发 ROS，只打印

# 没有 OME 时的连调
./run_stream.sh -- --source v4l2 --print-only --save-vis /tmp/vis
./run_stream.sh -- --source file --file a.mp4 --print-only --save-vis /tmp/vis
```

**流水账（jsonl）默认一直写**，生产模式也写，落在
`$LOG_DIR/detect-<起动时刻>.jsonl`。每个 tick 一行：时刻、检到几个、每个框的
位置和 conf、当时的分数和判定值。**这是唯一能回答「我们漏录了吗」的东西**
（漏掉的东西不在 bag 里）。关掉的开关是 `--no-jsonl`，**生产别用**。

## 环境

**gi、torch、rclpy 三样必须在同一个 Python 里。** 做法是建一个
`--system-site-packages` 的 venv：gi 和 rclpy 从系统继承，只把 ultralytics/torch
装进 venv。

```bash
sudo apt install gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
                 gstreamer1.0-libav gstreamer1.0-nice gstreamer1.0-vaapi
sudo apt install gir1.2-gst-plugins-bad-1.0 gir1.2-gst-plugins-base-1.0
sudo apt install python3.12-venv
python3 -m venv --system-site-packages ~/rog-pc/venv
~/rog-pc/venv/bin/python3 -m pip install ultralytics
```

装完过一遍这个（**缺任何一个的表现都是「连上了但没有画面」，不报错**）：

```bash
for e in webrtcbin nicesrc srtsink mpegtsmux h264parse x264enc avdec_h264 vaapih264enc; do
    printf "%-16s " $e; gst-inspect-1.0 $e >/dev/null 2>&1 && echo OK || echo 缺
done
~/rog-pc/venv/bin/python3 -c "
import gi
for n in ('Gst','GstWebRTC','GstSdp'): gi.require_version(n,'1.0')
import torch, ultralytics
print('typelib OK | torch cuda', torch.cuda.is_available(), '| ultralytics', ultralytics.__version__)"
```

哪个包给哪个 element：`plugins-bad` → `webrtcbin` / `srtsink` / `mpegtsmux` /
`h264parse`；`plugins-ugly` → `x264enc`；`libav` → `avdec_h264`；`nice` →
`nicesrc`；`vaapi` → `vaapih264enc`。

**★ 装了插件还不够，Python 还要 introspection 的 typelib**（`gir1.2-*`）。
只装插件的话 `gst-inspect-1.0 webrtcbin` 显示 OK、`gst-launch` 也能用，但
Python 里 `gi.require_version("GstWebRTC","1.0")` 会抛 `Namespace not available`。

**★ `gstreamer1.0-nice` 光有 `libnice10` 不够。** ICE 的实体是
`nicesrc`/`nicesink`，缺了只打个警告然后 `create-answer` 静默失败 ——
SDP 交换成功、OME 侧也建了 session，但媒体永远不来。

**libsoup 2.4 和 3.0 是两套 API 且不能同居**（22.04 只有 2.4，24.04 只有 3.0）。
`ome_receiver.py` 开头按实际装了哪个来选。

**ROS 2 Jazzy 在 `/opt/ros/jazzy`**，`run_stream.sh` 会自动 source
（路径在 `config.env` 的 `ROS_SETUP`）。只有 `./run_stream.sh test` 不 source。

**GPU 持久化模式已经开了**（`nvidia-smi -pm 1`，重启后仍在）。不开的话容易出
「`nvidia-smi` 正常但 `torch.cuda.is_available()` 是 False」这种间歇故障
（处理是 `sudo rmmod nvidia_uvm && sudo modprobe nvidia_uvm`）。

`USE_HW_CODEC=1` 时走核显编码（启动日志里会写 `vaapi` 还是 `x264`），两种都
验过能被 OvenPlayer 解出来 —— 前提是 `srt_out.py` 里那两个 profile 写死了。

## 已经验证过的（rog-server 实机）

输入是灌进 OME 的假 `fisheye`（`bus.jpg`）和假 `soundmap`，用真实的
`run_stream.sh` / `run_overlay.sh` 跑的。

| 项 | 结果 |
|---|---|
| GPU | `predictor.device = cuda:0`，RTX 4070 Laptop。预热 1.74 s |
| 检测功能 | `bus.jpg` 检出 4 个 person，conf 0.73～0.94 |
| 推理耗时 | `yolo11x` @ 1088 中位 105 ms，最差 154 ms（首帧）。预算 200 ms |
| 节奏 | 12 s 跑出 60 个 tick = 5.0 Hz |
| 漏电积分 | 置位 2 tick / 久留后落下 40 tick / 误闪落下 8 tick；10-15% 落下、25-35% 撑住 |
| 断流处理 | 起动瞬间画面还没来 → 打警告并停止发布，来了之后自动恢复 |
| **从 OME 收流** | `--source ome` 走通，216 个 tick，推理中位 107-109 ms |
| **两条合成流** | 推回 OME 再拉出来都正常；`rgb_sm` 叠加耗时 3.1-3.9 ms |
| **SRT 推拉保真度** | 灰 60→57、白 255→252、红 (0,0,255)→(0,0,251)，207 帧 0 丢弃 |
| **僵尸流恢复** | SIGKILL 发布者后推同名流：报错一次 → 5 s 后重建 1 次即恢复 |
| **生产模式（发 ROS）** | `ros2 topic echo /boxie/record/trigger` 收到 `data: true` |
| **VA-API 编码** | 装上 `gstreamer1.0-vaapi` 后用 vaapi 编，拉回来能正常解 |

**还没验证的**：真实 robot-pc 推上来的流、机体麦克风复用在 `fisheye` 里的音轨、
新加的 `onboardmic` 那条流、以及 `operatormic` 那条下行链路。

---

## 语音转文字（`asr.py`）

从本机 OME 拉两路音频（loopback），按静音切句，用 faster-whisper（`medium`）
转成文字。**结果给 vlm-server 用**（架构 §5.2）。

```
OME(127.0.0.1) ──WebRTC──▶ fisheye 的音频 pad ──▶ onboard   现场说了什么
                       └──▶ operatormic       ──▶ operator  操作者说了什么
```

`run_asr.sh` 传了 `--no-video`，只接音频 pad，省掉一路 1080×1080 的 H.264 解码。

**独立进程、独立 venv**：venv 分开是因为 ctranslate2 要 CUDA 12 的 cudnn 而
`venv/` 里的 torch 是 cu130；进程分开是因为 ASR 挂了不该连累人物检测。

### 耗时

whisper 在这块 4070 上（float16，一段发话从头到尾）：

| 音频长度 | `medium` | `small` |
|---|---|---|
| 2 s | 237 ms | 76 ms |
| 4 s | 311 ms | 125 ms |
| 8 s | 442 ms | 147 ms |

和 YOLO 并发时检测的耗时（喂的节奏比真实对话还密：每 2.5 秒一段 4 秒发话）：

| | 中位 | p90 | 最差 | 超 200 ms 预算 |
|---|---|---|---|---|
| YOLO 独占 | 93.5 ms | 94.9 ms | 95.3 ms | 0/60 |
| **YOLO ＋ whisper medium** | **93.8 ms** | 133.5 ms | 246.7 ms | **4/60** |

**中位完全没动**，所以用 medium 不降到 small。**★ 但这种拖慢不会触发告警** ——
`person_detect.py` 盯的是**中位**耗时（超 300 ms 才叫），真出问题得看 p90
或超预算的比例。

### 两个窗，是两回事

| | 是什么 | 参数 |
|---|---|---|
| **切分的窗** | 音频在哪里断开送给 whisper。**不按固定长度切** | `ASR_SILENCE_SEC` / `ASR_MIN_SPEECH_SEC` / `ASR_MAX_SEGMENT_SEC` |
| **上下文的窗** | 给 VLM 看多长的转写（现在 60 s） | `ASR_CONTEXT_SEC` |

发话在内存里留 `ASR_KEEP_SEC`（600 s），切多长是**读的时候**才决定的，
所以事后还能拿 `transcript.jsonl` 换别的值重放。

**没人说话时 `text()` 返回空字符串** —— 不在这一侧塞占位符，要不要把静默告诉
VLM 是造 prompt 那一侧的决定。

### 本底噪声

判断「在不在说话」用的是相对本底的阈值，不是绝对值。本底取**最近 30 秒的
5 分位**。

**★ 窗必须远长于一次发话。** 窗太短（早先是 5 秒窗取 10 分位）时，一个人连说
10 秒就会把本底抬到发话的高度，**整段判成静音后丢掉，日志上什么都看不出来**。
30 秒 ＋ 5 分位相当于假设「一段 30 秒里至少有 5% 的时间没人说话」——
接待场景成立但不是铁律，所以周期日志里带了本底、阈值和「当静音丢掉多少秒」。

### 落在磁盘上的（都在 `$LOG_DIR`）

| 文件 | 谁读 |
|---|---|
| `transcript.jsonl` | **机器读。** 一句一行带时刻 —— 换上下文窗重放、和 bag 对时间都靠它 |
| `onboard.txt` / `operator.txt` | **人读。** `tail -f` 就能看现场在说什么 |
| `status.json` | 每 `STATUS_INTERVAL` 秒覆盖一次的状态，不是记录 |

### 环境（`venv-asr`）

```bash
python3 -m venv --system-site-packages ~/rog-pc/venv-asr
~/rog-pc/venv-asr/bin/pip install faster-whisper
# ★ 这一行不能省，见下
~/rog-pc/venv-asr/bin/pip install nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'
```

实机版本：gi 3.48.2（系统继承）/ faster-whisper 1.2.1 / ctranslate2 4.8.1 /
numpy 1.26.4，`ctranslate2.get_cuda_device_count()` = 1。

**★★ ctranslate2 找不到 CUDA 库。** `pip install faster-whisper` **不会**带
CUDA 运行库，装进来之后动态链接器也**不会**去 venv 的 `site-packages/nvidia/`
下找 —— 表现是 `RuntimeError: Library libcublas.so.12 is not found`。

**难发现是因为全绿**：模型能加载、`nvidia-smi` 上看得见显存被占、
`get_cuda_device_count()` 返回 1，**只有编码器第一次真跑才炸**，而静音会被
`vad_filter` 挡在编码器之前，所以不出真人声就测不出来。两道防线：

1. **`run_asr.sh` 自己设 `LD_LIBRARY_PATH`**（指到 venv 的 `nvidia/cublas/lib`
   和 `nvidia/cudnn/lib`），库不在时直接报错退出。**所以要用 `run_asr.sh` 起动，
   别直接跑 `asr.py`。**
2. **`Transcriber` 启动时拿 0.5 秒噪声、关掉 `vad_filter` 硬跑一遍编码器**
   （日志里那行 `预热 0.4s —— 编码器确认能跑`）。

**★ vlm-server 那台不能照抄** —— 那台是 20.04，系统 `python3-gi` 只有 3.8 版
而 faster-whisper 要 ≥3.9。见 `vlm-server/README.md`。

### `GET /transcript`

ASR 进程顺带开一个 HTTP 口（`ASR_HTTP_HOST` / `ASR_HTTP_PORT`，现在
`0.0.0.0:8770`）。**只有这一个端点，而且是「拉」不是「推」**（架构 §3.1）。

```bash
curl -s "http://127.0.0.1:8770/transcript?seconds=60"
```

```json
{"ok": true, "t": 1787469797.1, "seconds": 60.0,
 "sources": {
   "onboard":  {"ok": true, "age": 0.016, "shape": "16000Hz 1ch", "n": 6362},
   "operator": {"ok": true, "age": 0.015, "shape": "16000Hz 1ch", "n": 6363}},
 "utterances": [{"source": "operator", "text": "...", "t_start": .., "t_end": ..}]}
```

**每一路自带状态，不用整体的 503：**

| 情况 | 长什么样 |
|---|---|
| 确实没人说话 | `utterances: []`，而 `sources.*.ok` 为真 |
| 某一路音频断了 | 那一路 `ok: false`，其余照给 |
| 整个服务没了 | 连不上 —— 客户端返回 `{"ok": false, "error": ...}` |

这三种在 prompt 里该有不同的说法（架构 §5.2）。**窗长是查询参数**，窗在这边切。

**绑 0.0.0.0 而不是 tailscale 地址** —— 那个地址要等 tailscale 起来才有，绑死
的话 tailscale 没起这个服务就起不来。代价是局域网内也看得见。

```bash
cd ~/rog-pc && ./run_asr.sh      # 前台，Ctrl-C 停
./run_asr.sh -- --no-http        # 只看转写，不给 vlm-server 用
```

### 已经验证过的（rog-server 实机，2026-08-23）

输入是灌进 OME 的假音频流（AAC 48k 1ch，形状和 tele-pc 推的一致）。

| 项 | 结果 |
|---|---|
| 环境 | gi 3.48.2、faster-whisper 1.2.1、ctranslate2 4.8.1、CUDA 设备 1 |
| 模型 | medium 加载 3.9 s，**预热 0.4 s 确认编码器能跑** |
| 收流 | 60 秒收到 onboard 2685 块 / operator 2684 块 |
| **音频形状** | 两路都是 `16000 Hz 1ch` —— `audio_caps` 的转换穿过 OME 的 Opus 之后仍然成立 |
| 推理耗时 / 并发 | 见上面两张表 |

**还没验的：转写质量。** 用的是 TTS 生成的英语语音，只够量**时间**。日语的
准确度、真实会场的本底阈值，都要等真人说话。

---

## 还没解决的

| 项 | 内容 |
|---|---|
| **`CONF` / `DECAY` 还没标定** | 现在的 0.35 / 0.25 是待定值。要拿现场素材量「10 m 处真人的检出率」和「无人时的误检率」，把 `p*` 卡在中间 |
| **VLM → ROS 的桥还没写** | 架构 §5.3 派给这台机器：vlm-server 的判断 `POST /decision` 送过来，由这边转成 ROS 发给 robot-pc。本目录还没有对应的文件 |
| **`rgb_sm` 的时间戳对齐** | 见上面「两条合成流」和架构 §8 |
