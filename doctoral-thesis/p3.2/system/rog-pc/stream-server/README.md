# stream-server（rog-server）

OME（所有流的枢纽）和**人物检测节点**（录制触发的发出方）。
局域网内用 **`rog-server.local`** 寻址（mDNS，avahi 提供；静态地址是 192.168.1.10）。

**★ 不是你写代码的那台笔电。** 开发机的 hostname 也叫 `ROG`，也是 4070，
也常驻着一个 OME，但它是 192.168.1.100，**实验中完全不参与**。本文里所有
「rog-server 上」都指 `rog-server.local`（192.168.1.10）；在开发机上查不到 venv、权重、GPU 状态，
说明不了任何问题。见 [../../system-architecture.md](../../system-architecture.md) §0。

设计见 [../../system-architecture.md](../../system-architecture.md) §3、§6.2。

```
OME(localhost) ──WebRTC──▶ 鱼眼 1080×1080 ──YOLO──▶ 要不要录（bool）
                                                        │ 5 Hz
                                                        ▼
                                          std_msgs/Bool ──▶ robot-pc
                            /<ROBOT_NAME>/record/trigger
```

**发出去的只有一个 bool。** 不发人数、不发时间戳 —— robot-pc 收到 true 就从
环形缓冲往前 `PREROLL_SEC` 开始写，收到 false 再写 `POSTROLL_SEC` 才停。
判断在这边，记录在那边。

| 文件 | 内容 |
|---|---|
| `person_detect.py` | 检测节点本体。**算法参数全在文件顶部的配置段** |
| `config.env` | 部署参数（OME 地址、stream key、ROS 名字、用哪个 Python）。**唯一出处** |
| `../run_stream.sh` | 起动（**在 `rog-pc/` 那一层**）。source config.env 之后 exec 上面那个 |
| `ome_receiver.py` | 从 OME 收 WebRTC。沿用旧实现（`archive/common/`），只改了 libsoup 的版本适配（见下）。**这份是源头** —— robot-pc / vlm-server 各带一份逐字节相同的副本（它们要能单独部署），改完这里记得同步过去 |
| `soundmap_overlay.py` | 鱼眼 ＋ 声音图的叠加流（`rgb_sm`）。独立进程 |
| `asr.py` | **语音转文字**（faster-whisper）。两路音频 → 文字，给 vlm-server 用。见下面「语音转文字」 |
| `recv.py` | 从 OME 收多路的封装（三条流 → 四路数据）。**这份是源头**，vlm-server 带一份逐字节相同的副本 |
| `../run_asr.sh` | 起 `asr.py`（也在 `rog-pc/` 那一层）。**独立进程、独立 venv** |
| `srt_out.py` | 把 BGR 帧推回 OME 的共用出口（SRT）。上面两个都用它 |
| `../run_overlay.sh` | 起 `soundmap_overlay.py`（也在 `rog-pc/` 那一层） |
| —— | 没有自定义 msg。录制触发这条 topic 就一个 `std_msgs/Bool` |

---

## 能在 OvenPlayer 上看的流

播放地址一律是 OME 的 WebRTC 出口：

```
ws://rog-server.local:3333/app/<stream key>
```

tele-server 的页面里用的就是 OvenPlayer；手边没有页面时，
<https://demo.ovenplayer.com/> 填上面这个地址也能看。

| 看什么 | stream key | 播放地址 | 内容 | 谁推的 |
|---|---|---|---|---|
| 鱼眼 ＋ 机体麦克风 | `fisheye` | `ws://rog-server.local:3333/app/fisheye` | H.264 1080×1080@30 ＋ AAC | robot-pc |
| 声音图 | `soundmap` | `ws://rog-server.local:3333/app/soundmap` | H.264 64×64@15，黑底黄斑 | robot-pc |
| RealSense | `realsense` | `ws://rog-server.local:3333/app/realsense` | H.264 848×480@30（仅 color） | robot-pc |
| 导航相机 | `navcam` | `ws://rog-server.local:3333/app/navcam` | H.264 1280×720@30 | robot-pc |
| 操作者语音 | `operatormic` | `ws://rog-server.local:3333/app/operatormic` | AAC 48 kHz 1ch | tele-pc |
| **鱼眼 ＋ 声音图** | **`rgb_sm`** | `ws://rog-server.local:3333/app/rgb_sm` | H.264 1080×1080@15 | **stream-server** |
| **鱼眼 ＋ 检测框 ＋ 录制标志** | **`detect`** | `ws://rog-server.local:3333/app/detect` | H.264 1080×1080@5 | **stream-server** |

**「机体麦克风的声音」没有独立的流。** 它复用在 `fisheye` 里（架构 §1.1）——
操作者要音画同步地听，所以是一条流两个 track。**播 `fisheye` 就同时有画面和
机体那边的声音**，不用再开一条。

**`operatormic` 是反方向的那条**：tele-pc 推上来、robot-pc 拉下去放到机体扬声器。
在播放器里打开它听到的是操作者自己说的话 —— 用来确认这条下行链路通没通。

### 两条合成流（stream-server 自己生成的）

**只是给人看的监视流。不进 bag、不参与任何判断。** 做它们是为了在现场用一个
播放器就能确认系统在正常工作，而不是去翻日志、或者等事后看 bag 才发现问题。

| | `detect` | `rgb_sm` |
|---|---|---|
| 谁生成 | `person_detect.py`（顺带） | `soundmap_overlay.py`（独立进程） |
| 起动 | `~/rog-pc/run_stream.sh` | `~/rog-pc/run_overlay.sh` |
| 节奏 | 5 fps，跟着检测走 | 15 fps（声音图本来就是 15 Hz） |
| 画面上有什么 | 每个检测框 ＋ conf；**右上角 REC/IDLE ＋ 当前分数 ＋ 框数**；左上角墙钟时刻 | 鱼眼 ＋ 黄色声音图叠加 |
| 一眼能确认 | 检测在不在工作、现在到底在不在触发录制 | 声音图的斑点和画面里的人对不对得上 |

`rgb_sm` 的叠加式子和 QC 视频（`soundmap-generator/soundmap-video/bag2video.py`）
以及旧实现一致：

```
sm_color = 黄色化(声音图 INTER_LINEAR 放大到 1080)
blend    = addWeighted(sm_color, 0.6, cam, 0.8, 0)
```

**要定量分析请用 bag 里的 `soundmap/map`**（float32 原始值），不要用这条流 ——
它是有损 H.264、还叠了底图。声音图断了会自动回到素画面（贴着几秒前的旧斑点
比不贴更容易误导）。

### ★ 这两条流有一个共同的坑：OME 的僵尸流

发布进程死得不干净（SIGKILL、崩溃、掉电）之后，**OME 会留下一个同名的流**，
之后再推同名的流一律被拒：

```
Reject to add stream : there is already an incoming stream (detect) with the same name
```

**在 rog-server（GStreamer 1.24.2）上实测过这条路径**：SIGKILL 掉一个发布者
造出僵尸流，再推同名流 ——

```
[warn] 推流 zomb 出错: Failed to write to SRT socket: Socket is broken or closed —— 5 秒后重建 ...
[info] 重建推流（第 1 次） zomb -> srt://127.0.0.1:9999
>>> 正常结束 推 297 丢 0 重建 1
```

**只重建一次就恢复了，进程毫发无伤。** OME 会在几秒内自己把僵尸流放掉（SRT
超时），5 s 的退避正好跨过去。所以正常情况**不需要重启 OME** —— 只有当日志里
一直在「重建推流（第 N 次）」而 OME 一直回上面那句 Reject 时，才需要
`sudo systemctl restart ovenmediaengine`。

`srt_out.py` 为此做了三件事：出错不把调用方带走、自己退避重建（不让 srtsink
每 20 ms 猛敲 OME）、日志里直接把诊断写出来。**这三件在旧版本上还不够** ——
开发机是 GStreamer 1.20.3，那里 srtsink 撞上僵尸流会直接把进程 abort 掉，
Python 侧拦不住。生产用的 1.24 没有这个问题。

---

## 检测用的是什么

**前向广角鱼眼，人基本是竖直的** → 不做任何展开，
YOLO11 直接跑原图，取 COCO 的 person 类，**逐帧检测，不做跟踪**，后面接一个
漏电积分做时间上的迟滞。

**目标是「只要画面里有真人就录，哪怕他在 10 米外走过来」。** 背景里路过的人流
被录进去没关系；要避免的只有「一个人都没有还在傻傻记录」。所以这里按 recall
优先挑参数 —— 10 米处的人在画面里只有 60～100 px 高，是小目标。

**rog-server 上实测**（1080×1080 输入，含前后处理和 NMS，`device=cuda:0`）：

| 组合 | 中位 | 占 200 ms 预算 |
|---|---|---|
| `yolo11m` @ 960 | 29.9 ms | 15% |
| `yolo11m` @ 1088 | 39.5 ms | 20% |
| `yolo11x` @ 960 | 75.8 ms | 38% |
| **`yolo11x` @ 1088（默认）** | **97.8 ms** | **49%** |
| `yolo11x` @ 1280 | 133.9 ms | 67% |

大模型在这里几乎是白拿的（一半预算都没用掉），而漏掉一个人是不可逆的 ——
没录下来的东西事后找不回来。所以直接上 `x`，输入取 1088（1080 原生不缩，
32 的倍数）。功能上验证过：`bus.jpg` 检出 4 个 person，conf 0.73～0.94。

### 为什么不用 ByteTrack

早先用过，理由是「让人数稳」。去掉了 —— 在 5 Hz 这个工作点上它是净负债，
详见 `person_detect.py` 里 `PersonDetector` 的 docstring。一句话版本：
**跟踪器只输出「已确认」的轨迹，而 5 Hz 下关联本来就容易失败，结果是新出现的
人被系统性地延迟甚至吞掉** —— 方向和我们要的（recall 优先）正好反了。
要精确人数就事后拿录下来的 30 fps 鱼眼离线重跑。

### 判定：漏电积分

检到人加 1 分，没检到漏 `DECAY` 分；升到 `ON_TH` 置位，漏干才落下。
**不用「连续 N 帧没人」那种写法** —— 「连续」对偶发误检没有免疫力，每隔几秒
一次误检就能让计数永远凑不齐，`trigger` 一旦置位再也落不下来。

关键性质是能算出来的：某个东西在 `p` 比例的帧里被检到时，

```
每 tick 平均变化 = p × 1.0 − (1−p) × DECAY
盈亏平衡点  p* = DECAY / (1 + DECAY) = 0.20
```

**`DECAY` 这一个旋钮就定义了「检出率低于 20% 的一律当噪声」。** 实测行为：

| 场景 | 结果 |
|---|---|
| 近处的人走进画面 | 2 个 tick（0.4 s）置位 |
| 待了一阵再走掉 | 40 个 tick（8.0 s）落下 |
| 一闪而过的误检 | 8 个 tick（1.6 s）落下（尾巴自适应，比上面短） |
| 检出率 10% / 15% 的东西 | 落下（当噪声） |
| 检出率 25% / 35% 的东西 | 撑住（当真人） |

**★ 代价：这条线对两边是同一条。** 一个真人如果只有 15% 的帧被检到（比如
15 m 外），同样触发不了。所以 `DECAY` 要卡在两个实测值中间：

```
无人清场时的误检率  <  p*  <  10 m 处真人的检出率
```

这两个数要现场量（人站 5 / 10 / 15 m 各 30 秒，`--save-vis` 看 conf 和检出率），
量完回来调 `DECAY` 和 `CONF`。**在那之前 `CONF=0.35` 是个待定值。**

### 换相机的话

**这条前提变了，检测方案要跟着变。** 现在的做法只在「人是竖直的」时成立。

| 相机形态 | 该怎么做 |
|---|---|
| 前向广角，成像圆内切（**现在**） | 原图直接跑。就是现在这样 |
| 圆形全向、朝上环视（会议相机） | **必须先展开成全景条带**再检测。人的朝向随方位角旋转，直接跑边缘全漏 |
| 俯视鱼眼（吊装） | 同上；或者用 RAPiD（专为俯视鱼眼训练的旋转框检测器） |

后两种要先量出成像圆的 `(cx, cy, R)`，展开就这三个参数。现在这颗
（CX-MT500 裁中央 1080×1080）是 `(540, 540, 540)` —— 成像圆内切于画面，
四角在圆外所以是黑的，约占 21% 的像素。**但换不换方案看的是「人还竖不竖」，
不是黑角占多少**：前向安装时人的朝向和方位角无关，这才是能直接跑原图的理由。

---

## 环境

**gi、torch、rclpy 三样必须在同一个 Python 里** —— 这个进程要同时收 WebRTC、
做推理、发 ROS。做法是建一个 `--system-site-packages` 的 venv：gi 和 rclpy
从系统继承进来，只把 ultralytics/torch 装进 venv。

**rog-server 上这些都已经装好了**，下面是清单和理由（重装机器、或者再配一台时照做）。

```bash
# ---- gst：收流和推流各要一半，缺一不可 ----
sudo apt install gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
                 gstreamer1.0-libav gstreamer1.0-nice gstreamer1.0-vaapi
# ---- Python 的 introspection typelib：和上面是分开的包 ----
sudo apt install gir1.2-gst-plugins-bad-1.0 gir1.2-gst-plugins-base-1.0
# ---- venv ----
sudo apt install python3.12-venv
python3 -m venv --system-site-packages ~/rog-pc/venv
~/rog-pc/venv/bin/python3 -m pip install ultralytics
```

装完一次过一遍这个（**缺任何一个的表现都是「连上了但没有画面」，不报错**）：

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

**★ 装了插件还不够，Python 还要 introspection 的 typelib。** 这是踩过的一个坑：
只装插件的话 `gst-inspect-1.0 webrtcbin` 显示 OK、`gst-launch` 也能用，但 Python
里 `gi.require_version("GstWebRTC", "1.0")` 会抛 `Namespace GstWebRTC not
available` —— **命令行一切正常，`ome_receiver` 一行都跑不了**。

**`gstreamer1.0-nice` 光有 `libnice10` 不够。** ICE 的实体是 `nicesrc`/`nicesink`
这两个 gst element，缺了只会打个警告然后 `create-answer` 静默失败 ——
表现是 SDP 交换成功、OME 侧也建了 session，但媒体永远不来。

**`gstreamer1.0-vaapi` 装了之后 `USE_HW_CODEC=1` 就真的走核显编码**（启动日志里
会写 `vaapi` 还是 `x264`）。两种都实测过能被 OvenPlayer 解出来 —— 前提是
`srt_out.py` 里那两个 profile 写死了（见那个文件的文件头 ①②）。

**libsoup 2.4 和 3.0 是两套 API，而且不能同居。** 22.04 只有 2.4，24.04 只有
3.0，两台机器 distro 不同 —— 所以 `ome_receiver.py` 开头按实际装了哪个来选
（差别只在 `websocket_connect_async` 多一个 `io_priority` 参数）。旧实现写死了
2.4，直接搬到 24.04 上会在 `require_version` 就抛。

**ROS 2 Jazzy 在 `/opt/ros/jazzy`。** `rclpy` 要 source 过 `setup.bash` 才 import
得到 —— **`run_stream.sh` 会自动 source**（路径在 `config.env` 的 `ROS_SETUP`），
不用手动做。只有 `./run_stream.sh test` 不 source，因为它根本不发 ROS。

**GPU 的持久化模式已经开了**（`nvidia-smi -pm 1`，`nvidia-persistenced` 是
`WantedBy=sys-bus-pci-drivers-nvidia.device`，驱动一加载就起，重启后仍在）。
不开的话空闲时驱动会卸掉设备状态，除了每次建 CUDA 上下文变慢，还容易出
「`nvidia-smi` 正常但 `torch.cuda.is_available()` 是 False」这种间歇故障
（在开发机上遇到过，处理是 `sudo rmmod nvidia_uvm && sudo modprobe nvidia_uvm`）。

---

## 跑

**启动脚本在上一层**（`~/rog-pc/`），不在本目录：

```bash
cd ~/rog-pc
./run_stream.sh              # 生产：从 OME 拉，发 ROS
./run_stream.sh test         # 不发 ROS，只打印

# 没有 OME 时的连调（直接调本体）
./run_stream.sh -- --source v4l2 --print-only --save-vis /tmp/vis
./run_stream.sh -- --source file --file a.mp4 --print-only --save-vis /tmp/vis
```

`--save-vis` 把标注过的帧存成 jpg，调 `CONF` 时看这个。

**流水账（jsonl）默认一直写**，生产模式也写，落在
`$LOG_DIR/detect-<起动时刻>.jsonl`。每个 tick 一行：时刻、检到几个、每个框的
位置和 conf、当时的分数和判定值。**这是唯一能回答「我们漏录了吗」的东西** ——
漏掉的东西不在 bag 里，事后翻 bag 时「规则太严没触发」和「真的没人」长得一模
一样。有了它才能事后重放不同参数、找漏检时段、统计一整天来过多少人。
关掉的开关是 `--no-jsonl`，**生产别用**。

---

## 三个要知道的行为

**① 输入断了 → 停止发布，不是发 `false`。**
robot-pc 的 recorder 在 `TRIGGER_TIMEOUT_SEC`（3 s）收不到消息时会 fail-open
退化成连续记录 —— 那是我们要的。发 `false` 是在告诉它「我看清楚了，确实不用
录」，它就会安安静静地什么都不录。OME 掉了、robot-pc 停推了、收流线程卡死了，
每一种都会走到这条路径上。

**② 必须跑在 GPU 上，起不来也不许退化。**
`.to("cuda")` 只挪了 nn.Module，**真正决定推理设备的是 predictor** ——
ultralytics 的 `predict` 自己会 `select_device('')`，CUDA 不可用时它**默默**
挑 cpu，不报错。那时候一帧一两秒，5 Hz 的循环一直落后，而日志、tick 数、判定值
看起来全都正常。所以节点做了三道：启动查 `torch.cuda.is_available()`、每次推理
显式传 `device="cuda:0"`、预热后断言 `predictor.device.type == "cuda"`
（日志里会打出来），外加中位耗时超 300 ms 的兜底告警。

**③ QoS 两边必须对上。** 这里发的是 `BEST_EFFORT` ＋ `depth=1` —— 5 Hz 常发的
状态量，丢一帧不值得重传（下一帧 200 ms 后就来）。**robot-pc 的 subscriber 如果
用默认的 `RELIABLE`，两边不兼容，一条消息都收不到，而且 ROS 不报错** ——
表现和「检测节点没起来」一模一样，然后 recorder 一路 fail-open 连续记录，
盘满了才发现。`ros2 topic info -v` 能看出 QoS 对不对得上。

---

## 已经验证过的（rog-server 实机）

| 项 | 结果 |
|---|---|
| GPU | `predictor.device = cuda:0`，RTX 4070 Laptop。预热 1.74 s |
| 检测功能 | `bus.jpg` 检出 4 个 person，conf 0.73～0.94 |
| 推理耗时 | `yolo11x` @ 1088 中位 105 ms，最差 154 ms（首帧）。预算 200 ms |
| 节奏 | 12 s 跑出 60 个 tick = 5.0 Hz |
| 漏电积分 | 置位 2 tick / 久留后落下 40 tick / 误闪落下 8 tick；10-15% 检出率落下、25-35% 撑住 |
| 流水账 | 60 行对 60 个 tick，boxes ＋ conf ＋ score 齐全 |
| 断流处理 | 起动瞬间画面还没来 → 打警告并停止发布，来了之后自动恢复 |
| **从 OME 收流** | `--source ome` 走通：WebRTC 拉 `fisheye` → 检测 → 216 个 tick，推理中位 107-109 ms |
| **`detect` 合成流** | 推回 OME 再拉出来，1080×1080，框 ＋ conf ＋ REC ＋ score ＋ 时刻都在，亮背景下也看得清 |
| **`rgb_sm` 合成流** | 同上，黄斑叠在底图上，叠加耗时 3.1-3.9 ms |
| **SRT 推拉保真度** | 灰 60→57、白 255→252、红 (0,0,255)→(0,0,251)，207 帧 0 丢弃 |
| **僵尸流恢复** | SIGKILL 发布者后推同名流：报错一次 → 5 s 后重建 1 次即恢复，进程不受影响 |
| **生产模式（发 ROS）** | `./run_stream.sh` 自动 source ROS，`ros2 topic echo /boxie/record/trigger` 收到 `data: true` |
| **VA-API 编码** | 装上 `gstreamer1.0-vaapi` 之后 `detect` 用 vaapi 编，拉回来能正常解 —— profile 那条坑没踩上 |

以上都是在 rog-server 上、用真实的 `run_stream.sh` / `run_overlay.sh` 跑的，
输入是灌进 OME 的假 `fisheye`（`bus.jpg`）和假 `soundmap`。

**还没验证的**：真实 robot-pc 推上来的流（现在还没接）、机体麦克风复用在
`fisheye` 里的音轨、以及 `operatormic` 那条下行链路。

---

## 语音转文字（`asr.py`）

从本机 OME 拉两路音频（loopback），按静音切句，用 faster-whisper 转成文字。
**结果给 vlm-server 用**（架构 §5.2）。

```
OME(127.0.0.1) ──WebRTC──▶ fisheye 的音频 pad ──▶ onboard   现场说了什么
                       └──▶ operatormic       ──▶ operator  操作者说了什么
```

**机体麦克风没有独立的流** —— 它复用在 `fisheye` 里（架构 §1.1），所以那条流
要连，但 `run_asr.sh` 传了 `--no-video`，只接音频 pad，省掉一路 1080×1080 的
H.264 解码。

### ★ 为什么在这台机器上，不在 vlm-server

原来是设计在 vlm-server 的。挪过来是因为 **3090 的显存不够两个都放**：
Qwen2.5-VL-32B-AWQ 约 20 GB，whisper medium 1.8 GB，24 GB 顶满了
（vLLM 默认 `gpu_memory_utilization=0.9` = 22.1 GB，再加 whisper 直接爆）。
挪过来之后 3090 整块给 VLM。

这台的 4070 Laptop 是 7808 MiB，YOLO11x 只占 920 MiB，显存宽裕。
**代价是和 YOLO 抢算力** —— 这条实测过了，结论是**可以接受**。

whisper 在这块 4070 上（float16，一段发话从头到尾）：

| 音频长度 | `medium` | `small` |
|---|---|---|
| 2 s | 237 ms | 76 ms |
| 4 s | 311 ms | 125 ms |
| 8 s | 442 ms | 147 ms |

和 YOLO 并发时检测的耗时（**喂的节奏比真实对话还密**：每 2.5 秒一段 4 秒发话）：

| | 中位 | p90 | 最差 | 超 200 ms 预算 |
|---|---|---|---|---|
| YOLO 独占 | 93.5 ms | 94.9 ms | 95.3 ms | 0/60 |
| **YOLO ＋ whisper medium** | **93.8 ms** | 133.5 ms | 246.7 ms | **4/60** |

**中位完全没动**，只有约 7% 的 tick 晚了几十毫秒，最差 247 ms。5 Hz 的判定是按
tick 计数的漏电积分，晚几十毫秒不改变任何判断；robot-pc 那边的
`TRIGGER_TIMEOUT_SEC` 是 3 秒，差着两个数量级。**所以用 medium，不用降到 small。**

**★ 但要注意：这种拖慢不会触发告警。** `person_detect.py` 盯的是**中位**耗时
（超 300 ms 才叫），而中位根本没变 —— 真出问题的话得看 p90 或者超预算的比例。

**独立进程、独立 venv。** venv 分开是因为 ctranslate2 要 CUDA 12 的 cudnn，
而 `venv/` 里的 torch 是 cu130；进程分开是因为 ASR 挂了不该连累人物检测
（那条 5 Hz 的判定管着录制触发）。

### 两个窗，是两回事

| | 是什么 | 参数 |
|---|---|---|
| **切分的窗** | 音频在哪里断开送给 whisper。**不按固定长度切**（会从词中间切断，接缝处丢字和重复） | `ASR_SILENCE_SEC` / `ASR_MIN_SPEECH_SEC` / `ASR_MAX_SEGMENT_SEC` |
| **上下文的窗** | 给 VLM 看多长的转写。现在 60 s | `ASR_CONTEXT_SEC` |

前者是声音的断点，后者是判断所需的上下文长度，别混。发话在内存里留
`ASR_KEEP_SEC`（600 s），切多长是**读的时候**才决定的，所以事后还能拿
`transcript.jsonl` 换别的值重放。

**没人说话时 `text()` 返回空字符串 —— 定下来就这样。** 不在这一侧塞
「（无发话）」之类的占位符：要不要把静默告诉 VLM、怎么措辞，是造 prompt
那一侧的决定。

### 本底噪声那条线（踩过一次）

判断「在不在说话」用的是相对本底的阈值，不是绝对值 —— 换个麦克风、换个会场，
本底差一个数量级。本底取**最近 30 秒的 5 分位**。

**窗必须远长于一次发话。** 早先是 5 秒窗取 10 分位，结果一个人连说 10 秒，
窗里全是发话的能量，本底被抬到发话的高度，**从第 1 秒起整段判成静音**，
然后被整段丢掉 —— 声音凭空消失，日志上什么都看不出来。30 秒窗 ＋ 5 分位
相当于假设「一段 30 秒里至少有 5% 的时间没人说话」，接待场景里成立，
但**不是铁律**，所以周期日志里带了本底、阈值和「当静音丢掉多少秒」三个数。

### 落在磁盘上的（都在 `$LOG_DIR`）

| 文件 | 谁读 |
|---|---|
| `transcript.jsonl` | **机器读。** 一句一行带时刻 —— 换上下文窗重放、和 bag 对时间都靠它 |
| `onboard.txt` / `operator.txt` | **人读。** `tail -f` 这两个就能看现场在说什么 |
| `status.json` | 每 `STATUS_INTERVAL` 秒覆盖一次的状态，不是记录 |

### 环境（`venv-asr`）

**这台是 24.04，系统的 gi 就是 Python 3.12 的**，所以
`--system-site-packages` 能把 gi 继承进来，只要把 faster-whisper 装进去：

```bash
python3 -m venv --system-site-packages ~/rog-pc/venv-asr
~/rog-pc/venv-asr/bin/pip install faster-whisper
# ★ 这一行不能省，见下
~/rog-pc/venv-asr/bin/pip install nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'
```

实机版本：gi 3.48.2（系统继承）/ faster-whisper 1.2.1 / ctranslate2 4.8.1 /
numpy 1.26.4，`ctranslate2.get_cuda_device_count()` = 1。

### ★★ ctranslate2 找不到 CUDA 库 —— 踩过的最阴的一个

`pip install faster-whisper` **不会**把 CUDA 的运行库装进来（ctranslate2 4.8
的 `Requires` 里只有 numpy/pyyaml/setuptools）。装进来之后，动态链接器也**不会**
去 venv 的 `site-packages/nvidia/` 下找。两件事叠起来的表现是：

```
RuntimeError: Library libcublas.so.12 is not found or cannot be loaded
```

**为什么难发现：** 模型能加载、`nvidia-smi` 上看得见显存被占了 1.8 GB、
`import faster_whisper` 一点事没有、`ctranslate2.get_cuda_device_count()` 返回 1
—— 全绿。**只有编码器第一次真的跑起来才炸**，而测试信号是静音、被
`vad_filter` 挡在编码器之前，所以**不出真人声就永远测不出来**。

这个坑在两台机器上都踩了，包括我们以为已经验证过的那台。两道防线：

1. **`run_asr.sh` 自己设 `LD_LIBRARY_PATH`**（指到 venv 的
   `nvidia/cublas/lib` 和 `nvidia/cudnn/lib`），并在库不在时直接报错退出。
   **所以要用 `run_asr.sh` 起动，别直接跑 `asr.py`。**
2. **`Transcriber` 启动时拿 0.5 秒噪声、关掉 `vad_filter` 硬跑一遍编码器**
   （日志里那行 `预热 0.4s —— 编码器确认能跑`）。库不全就在启动时退出，
   而不是等到现场第一个人说话。顺带把 CUDA kernel 预热了。

**★ vlm-server 那台不能照抄这个做法** —— 那台是 20.04，系统 `python3-gi`
只有 Python 3.8 版，而 faster-whisper 要 ≥3.9，继承不过来。见
`vlm-server/README.md`。

### `GET /transcript` —— vlm-server 从这里拿转写

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

**每一路自带状态，不用整体的 503。** 一样坏了不该让另外的也拿不到：

| 情况 | 长什么样 |
|---|---|
| 确实没人说话 | `utterances: []`，而 `sources.*.ok` 为真 |
| 某一路音频断了 | 那一路 `ok: false`，其余照给 |
| 整个服务没了 | 连不上 —— 客户端那边返回 `{"ok": false, "error": ...}` |

这三种在 prompt 里该有不同的说法，所以要分得开（架构 §5.2）。

**窗长是查询参数**（`seconds`），窗在这边切 —— 只有一个时钟参与，
vlm-server 改窗长不用动这边。

**绑 0.0.0.0 而不是 tailscale 地址**：那个地址要等 tailscale 起来才有，
绑死的话 tailscale 没起这个服务就起不来。代价是局域网内也看得见，
和同机的 OME（3333/9999 也全开）在同一个信任域里。

### 跑

```bash
cd ~/rog-pc && ./run_asr.sh      # 前台，Ctrl-C 停
./run_asr.sh -- --no-http        # 只看转写，不给 vlm-server 用
```

### 已经验证过的（rog-server 实机，2026-08-23）

输入是从开发机灌进 OME 的假音频流（AAC 48k 1ch，形状和 tele-pc 推的一致）。

| 项 | 结果 |
|---|---|
| 环境 | gi 从系统继承 3.48.2、faster-whisper 1.2.1、ctranslate2 4.8.1、CUDA 设备 1 |
| 模型 | medium 加载 3.9 s（权重已在本地），**预热 0.4 s 确认编码器能跑** |
| 收流 | 从 `127.0.0.1` 的 OME 拉两路，60 秒收到 onboard 2685 块 / operator 2684 块 |
| **音频形状** | 两路都是 `16000 Hz 1ch` —— `audio_caps` 的转换穿过 OME 的 Opus 之后仍然成立 |
| 推理耗时 | 见上面那张表（2/4/8 秒音频 → 237/311/442 ms） |
| **和 YOLO 并发** | 检测中位不变（93.5 → 93.8 ms），4/60 个 tick 超 200 ms，最差 247 ms |

**还没验的：转写质量。** 上面用的是 TTS 生成的英语语音，只够量**时间**。
日语的准确度、真实会场的本底阈值，都要等真人说话。

---

## 还没解决的

| 项 | 内容 |
|---|---|
| **`CONF` / `DECAY` 还没标定** | 现在的 0.35 / 0.25 是待定值。要拿现场素材量「10 m 处真人的检出率」和「无人时的误检率」，把 `p*` 卡在中间。见上面「判定：漏电积分」 |
| ~~Humble ↔ Jazzy~~ **已验证能通** | robot-pc 是 Humble、rog-server 是 Jazzy，默认 Fast DDS ＋ `ROS_DOMAIN_ID=32` 实测能通（25 s 收到 124 条，首条 0.2 s）。详见 `system-architecture.md` §7.1。**官方不保证跨 distro，任何一边升大版本后要重测。不要换成 CycloneDDS** —— 换了反而不通 |
| **`teleop_msgs` 还是要建** | 这条 topic 不再需要它了，但 robot-pc 侧记录用的 `AudioChunk` / `SoundMap` / `ClockOffset` / `RecordGate` 仍然要（见 `system-architecture.md` §6.3）。那是 robot-pc 那边的活 |
| **VLM → ROS 的桥还没写** | 架构 §5.3 把这件事派给了这台机器：vlm-server 的判断用 TCP/JSON 送过来，由 stream-server 转成 ROS 消息发给 robot-pc（**DDS 不跨 tailnet**）。本目录还没有对应的文件，等 vlm-server 那半开始写的时候一起做 |
