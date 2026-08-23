# 系统架构（P3.2）

**本文档只涉及 GStreamer 的传输路径与数据记录。** 电机驱动、操作指令这些 ROS
节点不在范围内。唯一的例外是记录 —— 因为用 rosbag2（mcap），那部分会碰到 ROS。

旧实现（以操作者端为枢纽的四机结构、RTMP）在 `archive/`。**只在手边留着，不进
git**（1.9 GB，见仓库根的 `.gitignore`）—— 代码本身在 git 历史里（`f91be30`
之前的 `pc-a-signage/` `pc-b-robot/` `pc-c-operator/` `pc-d-server/`）。
**那份只作参考，现行以本文档为准。** 传输路径和各机职责都是两回事，不要混。

---

## 0. 机器清单

机器一律**用文件夹同名的名字**称呼，不用 A/B/C 编号。

| 名字 | 文件夹 | 实体 | 位置 |
|---|---|---|---|
| **robot-pc** | `robot-pc/` | 机体 miniPC | 与 stream-server 同一路由器 |
| **stream-server** | `rog-pc/stream-server/` | rog-server | 同上。与 vlm-server 走 Tailscale |
| **tele-server** | `rog-pc/tele-server/` | **rog-server（与 stream-server 同一台）** | 只提供网页，不碰媒体流 |
| **tele-pc** | （无固定文件夹） | **局域网内任意一台机器** | 操作者实际坐的机器。浏览器看 tele-server 的页面 ＋ 本地 gst 推麦克风 |
| **vlm-server** | `vlm-server/` | 3090PC | 异地。与 stream-server 同一 tailnet，须为正式成员 |
| （开发机） | —— | ASUS ROG 笔电 | **实验中不参与**。只用来写代码、ssh 别的机器 |

**★ 开发机不是 rog-server，尽管两台长得一模一样。** 这是本项目最容易踩的一个坑：

| | IP | 角色 |
|---|---|---|
| **rog-server** | **`rog-server.local`** = 192.168.1.10（静态） | stream-server ＋ tele-server。**实验中的枢纽** |
| 开发机 | 192.168.1.100 | 只写代码。**媒体路径上不出现，不做任何计算** |

两台都是 ASUS 的 ROG（网卡 MAC 前缀相同，都是 4070 笔电），而且**开发机的
hostname 恰好就叫 `ROG`** —— 所以在开发机上跑 `hostname`、`nvidia-smi`，看到的
东西和「我就在 stream-server 上」完全一致。

**最能骗过人的一条：开发机上也常驻着一个 OME**（`ovenmediaengine.service`，
enabled ＋ active）。因此在开发机本地看到 1935 / 3333 / 9999 在监听，**不能**
说明 rog-server 上的 OME 起来了。同理，`~/rog-pc/venv`、YOLO 权重、README 里那些
实测的推理耗时**都在 192.168.1.10 上**，在开发机上查不到不代表没配。

判断依据只有 IP。要看 stream-server 的状态就 ssh 过去查，或者从远端探端口：

```bash
# 在开发机上确认真正的 rog-server 活着（OME 的 signalling / RTMP）
# **这里故意用 IP 而不是 rog-server.local** —— 要能区分「机器没起来」和
# 「名字解析坏了」。名字单独查。
for p in 3333 1935; do timeout 2 bash -c "echo > /dev/tcp/192.168.1.10/$p" \
  && echo "$p 开着" || echo "$p 不通"; done
getent hosts rog-server.local        # 应该回 192.168.1.10
```

**部署布局。** 仓库里的目录结构和机器上的一一对应：rog-server 上的两个服务合起来
放在 `~/rog-pc/` 下（`stream-server/` 和 `tele-server/` 两个代码目录，启动脚本
`run_stream.sh` / `run_tele.sh` 集中在上一层，见 `rog-pc/README.md`）。
robot-pc 同理：代码和运行时数据都在 `~/robot-pc/` 下（`ws/` 是 teleop_msgs 的
colcon workspace，`bag/` 录制，`log/` 日志），一次性准备用 `robot-pc/setup.sh`。

**tele-server 和 tele-pc 是两回事。** tele-server 是个服务，只负责把操作页面
发出去；媒体流一点都不经过它，所以和 OME 一起跑在 rog-server 上（分成两个
文件夹只是职责不同）。tele-pc 是操作者当天用的那台机器 —— 不固定、可以换人换
机器，它自己直接和 OME 打交道（浏览器收流、推麦克风）。

**stream-server 是枢纽。** 所有视频、音频都汇聚到 stream-server 上的 OME
（OvenMediaEngine），再由它分发。**robot-pc ↔ vlm-server 之间没有直连**，
vlm-server 只和 stream-server 通信。

配置的唯一出处是 `config.env`。各脚本不自带默认值，未设置就在启动时报错退出
（代码里再写一份默认值，改了一边另一边会静默地对不上）。

### 0.1 互相怎么寻址

**局域网内用 `rog-server.local`（mDNS），IP 是文档里的兜底。**
早先这台机器叫 `stream-server.rog`，那个名字哪里都解析不了（`.rog` 不是 mDNS
的 `.local`），所以一度改成硬编码 IP。现在机器改名成 `rog-server`，avahi 把
`rog-server.local` 发出来了，**开发机上实测能解析到 192.168.1.10**，于是换回名字。

| 从 → 到 | 地址 | 兜底 | 说明 |
|---|---|---|---|
| robot-pc → stream-server | **`rog-server.local`** | `192.168.1.10` | `robot-pc/config.env` 的 `OME_HOST` |
| tele-pc → stream-server | **`rog-server.local`** | 同上 | 收流和推麦克风都指这里 |
| tele-pc → tele-server | **`rog-server.local:<UI_PORT>`** | 同上 | 同一台机器，只是打开网页 |
| stream-server → 自己的 OME | **`127.0.0.1`** | —— | 同机，走 loopback。**不要绕出去再回来** |
| vlm-server → stream-server | **tailscale 地址** | —— | 异地。**`.local` 和 192.168.1.10 在那边都不通** |
| stream-server → robot-pc（ROS 触发） | DDS discovery | —— | 见 §7.1。**Humble ↔ Jazzy 实测能通** |

**地址只写在 `config.env` 里**，脚本和代码里一个都不出现。

**★ `.local` 靠 mDNS，mDNS 靠组播。** 组播在这个 AP 上是通的（实测，见 §7.1），
但每台客户端还得装 `libnss-mdns` 才认 `.local`。**robot-pc 上还没验过** ——
解析不了的表现是 srtsink 一起来就报名字解析失败、被重连循环无限重试（状态面板
上看得到）。那时候把 `OME_HOST` 换成 `192.168.1.10` 就行：rog-server 的地址是
静态配置的（`nmcli` 的 `ipv4.method=manual`），不会变。

#### tailnet（vlm-server 走这条）

tailnet 是 **`taildf8663.ts.net`**。三台在同一个 tailnet 里，均已确认 online：

| 机器 | tailscale 名 | tailnet 地址 | 局域网地址 |
|---|---|---|---|
| **stream-server**（rog-server） | `stream-server` | **`100.90.179.60`** | `192.168.1.10` |
| **vlm-server**（3090PC） | `chen` | **`100.104.252.121`** | `192.168.3.68`（理研网） |
| 开发机 | `rog` | `100.80.23.31` | `192.168.1.100` |

**★ tailscale 的机器名和这里的角色名对不上，注意别搞反：** 叫 `rog` 的那台是
**开发机**，而 rog-server 在 tailscale 里叫 `stream-server`。

**robot-pc 不在 tailnet 里** —— 它只走局域网，且只和 stream-server 通信。
vlm-server 与 stream-server 之间用上表的 100.x 地址，**局域网的 192.168.1.10
在 vlm-server 那边不通**（不同网段，理研网）。

**mDNS 万一在某台机器上不好使**，替代做法是在那台机器的 `/etc/hosts` 里写一行
`192.168.1.10 rog-server.local`（走单播，不依赖组播），这样 `config.env` 不用改。
**文档里不要再出现 `stream-server.rog`** —— 那个名字连同旧机器名一起废掉了。

---

## 1. 传输路径总览

### 1.1 进 OME（ingest）

| 来源 | stream key | 内容 | transport |
|---|---|---|---|
| robot-pc | `fisheye` | H.264 1080×1080@30 ＋ AAC（AT-CSP1 麦克风） | SRT（`mpegtsmux`） |
| robot-pc | `soundmap` | H.264 64×64@15 | SRT |
| robot-pc | `realsense` | H.264 848×480@30（仅 color） | SRT |
| robot-pc | `navcam` | H.264 1280×720@30 | SRT |
| tele-pc | `operatormic` | AAC 48 kHz 1ch | SRT（`mpegtsmux`） |
| **stream-server** | `rgb_sm` | H.264 1080×1080@15。鱼眼 ＋ 声音图叠加 | SRT（loopback） |
| **stream-server** | `detect` | H.264 1080×1080@5。鱼眼 ＋ 检测框 ＋ 录制标志 | SRT（loopback） |

**后两条是 stream-server 自己生成再推回 OME 的监视流**（§3），**不进 bag、
不参与任何判断** —— 纯粹为了在现场用一个播放器就能确认「检测在不在工作」
「声音图和画面对不对得上」。播放地址的完整表在
`rog-pc/stream-server/README.md`。

**进 OME 一律用 SRT。** RTMP 只能运一组 H.264＋AAC，也没有延迟上限；SRT 可以用
`latency=20` 把延迟钉住，且有 ARQ。OME 的 SRT provider 按
`streamid=<vhost>/<app>/<key>` 接收，写在 URI 的 query 里就行 ——
**已对 rog-server 上的 OME 0.19.0 实测确认**（OME 日志建出
`#default#app/<key>`、SourceType(SRT)，LLHLS 能拉到）。**入口只有一种
transport，抓问题时只需要看一条路径。**

**唯一的物理限制是浏览器不会说 SRT。** 所以麦克风不从操作页面推，而是在 tele-pc
本地单跑一个 gst 进程（§4.2）。除此之外没有例外。

**48 kHz 是硬约束。** OME 给 WebRTC 出口转 Opus 时时钟率固定 48000
（RFC 7587），所以推进去的时候就按 48 kHz 编，免得多一次重采样。
AAC → Opus 这一道转换会带来额外延迟，操作者麦克风这条链上是能感觉到的。

**机体麦克风复用进 fisheye 流。** 操作者要在一个浏览器页面里音画同步地收。
但**麦克风打不开不能连累视频** —— 启动时设备不在，就退回静音的
`audiotestsrc`，推流照常。

### 1.2 出 OME（egress）

**出 OME 一律 WebRTC。**（进 OME 一律 SRT，出 OME 一律 WebRTC —— 两条规则，没有例外。）

| 接收方 | 取什么 |
|---|---|
| tele-pc（浏览器） | `fisheye` `soundmap` `realsense` `navcam`；要看监视流再加 `rgb_sm` `detect` |
| robot-pc（机体扬声器） | `operatormic` |
| stream-server 自身（localhost） | `fisheye` → 人物检测；`fisheye` ＋ `soundmap` → `rgb_sm` 叠加；**`fisheye` 的音轨 ＋ `operatormic` → 语音转文字**（§3） |
| vlm-server | `fisheye` `soundmap`（**只取视频 pad** —— 音频归 stream-server 处理了，§5.1） |

理由：浏览器只会 WebRTC，这一路没得选；而 gst 那几路可以**共用同一份
`ome_receiver.py`**（源头在 `stream-server/`，已经处理了 libsoup 2.4/3.0 的
版本差异）—— 一份接收端实现覆盖全部，比按链路各挑各的省事得多。
多路一起收的封装是 `recv.py`（也在 `stream-server/`），stream-server 用它收
两路音频、vlm-server 用它收两路视频。

**但每台机器各带一份副本，运行时不跨目录引用。** robot-pc 和 vlm-server 都要
能单独部署到那台机器上，跨目录 import 等于把三台机器的代码绑成一坨。改的时候
改 `stream-server/` 那份再拷过去，副本之间保持逐字节一致（`diff` 就是检查）。

**代价是所有拉流端都依赖 `gstreamer1.0-nice`**（§7）。缺了不会报错，只会
静默失败，所以每台机器都要 `gst-inspect-1.0 nicesrc` 确认过。

**不要用 LLHLS。** 人物检测的延迟直接吃掉录制的 preroll 预算（§6.2），
分段协议动辄 1～2 秒，preroll 就得跟着从 10 s 往上加。

## 2. robot-pc

### 2.1 传感器

| 设备 | 型号 | 采集方式 | 推流 | 记录 |
|---|---|---|---|---|
| 鱼眼相机 | Xacti **CX-MT500** | MJPG 1920×1080@30（**只出 MJPG**） | 裁中央 1080×1080 后 H.264 | ○ |
| 深度相机 | RealSense | color 848×480@30 | H.264 | ○ |
| 导航相机 | nav camera | 1280×720@30 | H.264 | ○ |
| 麦克风／扬声器 | **AT-CSP1** | ALSA `hw:CARD=` | AAC（复用进 fisheye） | ○ |
| 16ch 阵列 | miniDSP **UMA16v2** | S32LE 16ch 44.1 kHz interleaved | **不推** | ○（S16LE 原始） |

**RealSense 只取 color。** depth 既不推流也不记录，管线里根本不打开那个流 ——
不是"采了不用"，是不采。原始 16-bit depth 有 195 Mbps，一旦打开整个存储方案都得
重做。需要深度的时候再单独议。

**只有 16ch 不进 OME。** 在机体内先算成 1-bit 声音图（64×64、15 Hz），
**只推声音图**。原始 16ch 只存在于 robot-pc 的本地记录里。

**★ 设备一律按名字引用。** `/dev/videoN` 和 `hw:N` 都随 USB 枚举顺序变化，
重插一次就可能静默地打开另一台设备。相机查
`/sys/class/video4linux/*/name`，ALSA 用 `hw:CARD=<名字>,DEV=0`。

**★ USB 口要分开。** Xacti（MJPG 1080p30 需 24～40 Mbps）、UMA16v2
（22.6 Mbps）、RealSense 都是等时传输，挂在同一个控制器上会互相抢带宽导致
掉帧。用 `lsusb -t` 确认分在不同的根路径上。

### 2.2 推流管线的形状

```
v4l2src(MJPG) → jpegdec → videocrop(左右各420px) → videoflip(180°)
              → h264enc → tee ─┬→ mpegtsmux → srtsink   (送 OME)
                               └→ appsink → ROS publish (送记录)
```

**启动顺序无所谓。** 每条流都套了重连循环 —— srtsink 连不上（OME 没起、
名字解析不了、网络瞬断）会直接退出且不自己重试，套上之后机体可以先于 OME 起来。
旧实现要求"必须先起 OME"，这条约束没有了。

**只编码一次。** `tee` 分叉的是已经编好的 H.264，推流和记录不重复编码。
条件允许就压到核显（VA-API）—— 目的不是提速，而是**腾 CPU**。缺 vaapi
element 时自动退回 software。

**相机是倒装的**，所以要转 180°。裁切位置 `[:, 420:1500]`。

### 2.3 声音图

`alsasrc(16ch)` → appsink → 生成器 → appsrc → H.264 → SRT。中间夹了生成过程，
一条 `gst-launch` 写不出来，要用 Python 把 appsink 和 appsrc 接起来。

| 项 | 值 | 理由 |
|---|---|---|
| 周期 | **15 Hz** | 旧实现是 10 Hz。见下面两条 |
| 积分窗 | 464 ms | 比周期长，每次滑动取（滑窗）。窗长决定信噪比和空间分辨率 |
| 分辨率 | 64×64 | **原样推出去。** 拉大也不会增加信息量，放大交给接收端 |
| 码率 | 500 kbps | 64×64 这个够用还有富余 |
| 归一化 | `exp(值 − 最大值)` | 生成器的 GAIN 是按这个变换标定的。**换成 min-max 标定就失效**，安静场景下也会满屏发亮，读不出"哪里在响" |

**周期和积分窗是两回事**，不要混。

**★ 15 Hz 在旧实测的上限（N100 最大 27 Hz）之内，但仍要在实机上确认。**
那个 27 Hz 是在负担轻得多的时候测的 —— 当时没有 RealSense 和 nav cam 的编码，
也没有 FLAC 和 bag 写入。**生成跟不上时的表现是掉帧而不是报错**，所以要盯
生成侧的实际帧率，别只看接收端。压不上去就退回 10 Hz。

积分窗 464 ms 远长于 67 ms 的周期，相邻两帧共用约 86% 的样本 —— 15 Hz 给出的是
更平滑的时间序列，不是更多的独立观测。这不是问题，只是别指望靠提高周期
"看得更细"：真要提高时间分辨率得同时缩短窗长，代价是信噪比和空间分辨率。

---

## 3. stream-server（rog-server）

| 常驻进程 | 内容 |
|---|---|
| **OME** | 接收 robot-pc / tele-pc，分发给 tele-pc、vlm-server、robot-pc 和 localhost |
| （注） | 操作页面由 tele-server 提供，**不经过 OME** |
| **人物检测节点** | 从 OME（localhost）拉 `fisheye`，**降到 5 fps** 做检测，结果用 ROS 发给 robot-pc。顺带把带框和录制标志的画面推回 OME（`detect`） |
| **声音图叠加** | 从 OME 拉 `fisheye` ＋ `soundmap`，叠好推回 OME（`rgb_sm`）。**独立进程** —— 检测那条要保证 5 Hz 的判定不被拖慢，这条挂了不该连累它 |
| **语音转文字（ASR）** | 从 OME（localhost）拉两路音频，按静音切句后用 faster-whisper 转成文字。**结果不在这里用** —— 由 vlm-server 拉走（§3.1）。又一个独立进程、独立 venv |
| **HTTP 服务** | 只有两件事：把转写发给 vlm-server、收 vlm-server 的判断转成 ROS（§3.1、§5.3） |

**为什么把人物检测放这里。** robot-pc 的 CPU 已经被 4 路编码、声音图生成和
bag 写入占满，塞不下推理。stream-server 有 GPU，而画面本来就已经到了 OME。
**不需要 30 fps** —— 判断的是有人／没人，5 fps 足够，还能省出 GPU。

检测结果就是 §6.2 的录制触发（`std_msgs/Bool`）。**不是变化时才发，而是
5 Hz 常发**（这样 robot-pc 才能察觉"它停了"并 fail-open）。

**★ 为什么把 ASR 也放这里。** 本来设计在 vlm-server 上，挪过来是因为
**3090 的显存放不下两个**：Qwen2.5-VL-32B-AWQ 约 20 GB ＋ whisper medium
1.8 GB，24 GB 顶满（vLLM 默认 `gpu_memory_utilization=0.9` 就是 22.1 GB）。
挪过来之后 3090 整块给 VLM，而这台的 4070 Laptop 只被 YOLO 用掉
920 MiB / 7808 MiB，显存宽裕。

**代价是 ASR 和检测抢算力**（YOLO 每 200 ms 用 93 ms，whisper 的推理是突发的）。
**实测过了，可以接受**：并发时检测的中位耗时不变（93.5 → 93.8 ms），只有
4/60 个 tick 超过 200 ms 预算、最差 247 ms —— 而判定是按 tick 计数的漏电积分，
晚几十毫秒不改变任何结论。所以用 `medium`，不用降到 `small`。
详细数字见 `rog-pc/stream-server/README.md`。

**三个 GPU 进程各自独立，venv 也分开。** ctranslate2 要 CUDA 12 的 cudnn，
而检测那个 venv 里的 torch 是 cu130 —— 同一个进程里两套 CUDA runtime 是已知的
麻烦源。分开之后它们只共享 GPU，不共享进程空间。

### 3.1 和 vlm-server 之间只有两个 HTTP 端点

**媒体一律走 OME，这里不重复送。** vlm-server 要的画面和声音图是它自己从 OME
拉 WebRTC 的（§1.2「出 OME 一律 WebRTC」，没有例外）。跨 tailnet 的 HTTP
只有这两样，都是纯文本、都很小：

| 端点 | 方向 | 内容 |
|---|---|---|
| `GET /transcript?seconds=N` | vlm-server 拉 | 最近 N 秒的转写（几 KB） |
| `POST /decision` | vlm-server 推 | VLM 的判断 → stream-server 转成 ROS 发给 robot-pc（几百字节） |

**为什么转写是「拉」不是「推」：** 消费的时机就是判断的时机，拉出来天然就是
「截至此刻的最近 N 秒」；窗长变成查询参数，两台机器之间零协调；窗在服务端切，
只有一个时钟参与；vlm-server 挂了也不用在任何地方积压。

**为什么判断要经 stream-server 转发：** DDS 不跨 tailnet（两边 distro 和 RMW
都不一样），而 stream-server 是唯一同时连着 vlm-server 和 robot-pc 的机器。

**两个端点在两个进程里。** `/transcript` 在 ASR 那个进程（转写在它手里），
`/decision` 单独一个（它要 rclpy，而 rclpy 和 libsoup 共存是踩过的坑，见 §7）。
ASR 挂了不该连带让判断送不到机体。

---

## 4. tele-server 与 tele-pc

### 4.1 职责划分

| 机器 | 跑什么 | 碰媒体流吗 |
|---|---|---|
| **tele-server** | UI 服务：把操作页面（HTML/JS）发出去 | **不碰。** 只是个网页服务器 |
| （部署） | tele-server 和 OME **跑在同一台 rog-server 上**，代码仍分两个文件夹 | |
| **tele-pc** | ① 浏览器打开那个页面 ② 本地一个 gst 进程推麦克风 | 碰。收流和推流都是它直接和 OME 做的 |

**tele-pc 不固定。** 局域网里任意一台机器都能当 tele-pc —— 换人、换机器、
同时开两台看，都不需要动 tele-server。页面上的 OvenPlayer 直接向
`rog-server.local` 建 WebRTC 连接，tele-server 不在这条链上。

因此 tele-server 很轻（一个 Flask ＋ 一堆静态文件），**和 OME 同机部署**，
在 rog-server 上当第二个服务跑。代码仍然分成两个文件夹 —— 职责不同，
以后要挪走也方便。

### 4.2 麦克风（跑在 tele-pc 上）

```
alsasrc(hw:CARD=<耳机>) → audioconvert → audioresample
    → voaacenc → aacparse → mpegtsmux → srtsink
```

浏览器不会说 SRT，所以麦克风不从页面推，而是在 tele-pc 上单跑这个 gst 进程。
**这不是额外负担** —— tele-pc 本来就是一台正常的机器，浏览器旁边多跑一个脚本
是自然的事。

顺带解决一个真实问题：**`getUserMedia` 会挑到系统默认输入，也就是笔记本内置
麦克风**，而且不报错，现场很难察觉。gst 用 `hw:CARD=<名字>` 把耳机麦钉死，
设备不在就直接启动失败 —— 这比静悄悄录到内置麦克风好。

**这部分代码不在本仓库管。** tele-pc 是临时指定的机器，推麦克风的脚本
在那台机器上自行准备。本文档只约定它送进 OME 的形态：stream key `operatormic`、
AAC 48 kHz 1ch、SRT。

**麦克风常开，没有 PTT。** 进程一起来，这个设备的声音就一直流向机体扬声器。

### 4.3 声音图怎么显示

**视频和声音图保持两路独立，在显示时叠加**（这样 vlm-server 才能单独取出
声音图）。robot-pc 推的是**黑底黄斑**的画面，操作界面用 screen 混合叠到视频上：
黑色部分让视频原样透过，只有黄斑浮上来。强度用界面上的滑块调。

---

## 5. vlm-server（3090PC）

**这台机器上只有一件事：VLM。** 语音转文字挪到 stream-server 去了（§3），
所以这里不装 whisper，24 GB 显存整块给 VLM。

### 5.1 输入

三样东西，**两条不同的路**：

| 要什么 | 怎么来 | 为什么 |
|---|---|---|
| 鱼眼画面 | 从 OME **持续拉 WebRTC**，本地缓冲 | §1.2「出 OME 一律 WebRTC」。媒体不另开通路 |
| 声音图 | 同上 | 同上 |
| 最近 N 秒的转写 | `GET /transcript` 找 stream-server 要（§3.1） | 纯文本，几 KB |

**画面和声音图只连视频 pad。** `fisheye` 那条流虽然带着机体麦克风的音轨，
但音频现在归 stream-server 处理，这边不接那个 pad —— 少一路解码。

**帧怎么用是 vlm-server 自己的事。** VLM 会用多帧输入，所以这边留一个
**有界的环形缓冲**，判断的时候按需要取 N 帧、跨多长时间，并按时刻把声音图
和鱼眼配成对。要几帧、多大尺寸、缓冲多久，全是本机的 config —— 改了不用碰
stream-server。

**为什么不让 stream-server 把帧打包好发过来。** 试过这个方案，不成立：
① 它等于在 OME 之外另开一条媒体通路，破坏 §1.2 那条规则；
② 多帧的时候「拉」并不省带宽 —— 8 帧 × 756² × 1 Hz 约 13.8 Mbps，
比持续拉流的 5.5 Mbps 还贵；
③ 服务端得先猜好缓冲深度、帧率、尺寸，而那些恰恰是消费方要反复调的。
持续收流在 3090 上实测只占 **45% 的一个核**（24 核），不值得为它设计东西。

**代价要认：** 帧的时刻是「到达 vlm-server 的时刻」，过了广域网。鱼眼和声音图
各自抖动是独立的，而声音图 15 fps 一帧才 67 ms —— **实测配对差落在
±76～125 ms**，正好和一帧的间隔同量级。在 stream-server 上配会准一些
（只受局域网和 OME 的抖动）。现在先这样，**如果发现声音图的斑点和画面里的人
对不上，这是头号嫌疑** —— 每一对的时刻差都报出来了，面板上超过 200 ms 标红。

### 5.2 转写从哪来

转写本身在 stream-server 上做（§3 有全部细节：按静音切、本底自适应、
两路分开）。这边只是在判断的那一刻去拉：

```
GET http://<stream-server tailnet>:<port>/transcript?seconds=60
```

**窗长是这边的选择**（查询参数），窗在服务端切（只有一个时钟参与）。
**没人说话就是空的列表** —— 那是「确实没人说话」，和「ASR 坏了」要分得开，
所以响应里每一项自带 `ok`。要不要把静默告诉 VLM、怎么措辞，是造 prompt
这一侧的决定。

### 5.3 输出

VLM 的判断用 `POST /decision` 发给 stream-server，由它转成 ROS 消息（§3.1）。
**DDS 不跨 tailnet**（两边 distro 和 RMW 都不一样，而要跨的只是一条低频消息）。
判断逻辑本身不在本文档范围内。

---

## 6. 数据记录

### 6.1 录在哪 —— **录在 robot-pc，不外传**

三个理由。

1. **采样时刻只存在于 robot-pc。** 过了 OME，SRT/WebRTC 那一层会重新打时间戳，
   capture time 就没了。在 robot-pc 上可以直接从 GStreamer 管线里取到。
2. **原始 16ch 只存在于 robot-pc。** 它没有推出去，别处根本录不到。将来要换声音图
   生成算法重跑，这是唯一的源数据。
3. **记录不依赖网络。** Wi-Fi 或 tailnet 断了，robot-pc 照录不误。

格式用 **rosbag2 / mcap**。gst 来的数据也封成 ROS 消息，所以视频、声音、指令
**进同一个 bag**。将来要加 ROS 节点的 topic 时也进同一个 bag，记录侧不用改。

**"一个文件"指的是"一个 mcap 会话"。** 内部用 `--max-bag-size` 切片（默认 2 GB）。
崩溃时最多丢末尾一片，而任何读取端（mcap CLI、Foxglove、rosbag2）都把它当成
一段连续的记录。

### 6.2 只在有人的时候录

判断在 stream-server（§3），记录在 robot-pc。robot-pc 不做判断。

```
/<ROBOT_NAME>/record/trigger    # stream-server 以 5 Hz 发布
    std_msgs/Bool               #   就一个 bool：要不要录
```

**只发一个 bool，不发人数也不发时间戳。** 早先设计过一个带 `src_stamp`
（那一帧在 robot-pc 上的采集时刻）的自定义 msg，用来精确定位前录的切点 ——
去掉了：它要求两机 NTP 同步、还要减一个实测的链路延迟，而整条链路的延迟
（0.2～0.5 s）加上判定迟滞（约 1 s）本来就被 10 s 的 preroll 整个吃掉。
去掉之后 §6.4「基准时钟只有 robot-pc 一个」才真的成立。

topic 叫 `record/trigger` 而不是 `person/presence`，是因为 robot-pc 不需要知道
「为什么」—— 将来往这个信号里 OR 别的触发源（比如操作者按一下按钮），
robot-pc 一行都不用改。人数、每个框的位置这些留在 stream-server 自己的流水账
里（见 `rog-pc/stream-server/README.md`）。

robot-pc 的 recorder 用自建节点包一层 `rosbag2_py` 的 `SequentialWriter`
（`ros2 bag record` 的 CLI 做不了前录）。

| 阶段 | 动作 |
|---|---|
| ① 常态 | 在内存环形缓冲里持续囤 `PREROLL_SEC`（默认 10 s）。27 Mbps × 10 s ≈ 34 MB，miniPC 也无压力 |
| ② 收到 `true` | 先把环形缓冲吐出去，再转入实时写入 |
| ③ 收到 `false` | 继续写 `POSTROLL_SEC`（默认 15 s） |
| ④ postroll 期间又收到 `true` | **不关闭，延长。** 人来来回回就开开关关的话，文件会被切碎，对话也被切碎 |

**切点按 robot-pc 自己收到的时刻定。** 判断是走完「编码 → SRT → OME → WebRTC
→ 解码 → 推理」之后的结果，送到时已经旧了 0.2～0.5 s，再加上判定迟滞
（近处的人约 0.4 s，远处的可能 1～3 s）。这些**全部由 preroll 吸收** ——
10 s 的前录减掉最坏约 3 s，仍有 7 s 的「人出现之前的上下文」，够用。

**H.264 必须从 GOP 边界开始。** 吐环形缓冲时，要回退到
`收到 true 的时刻 − PREROLL_SEC` **之前最近的那个关键帧**再切。从 GOP 中间开始
写，开头几十帧解不出来。`keyframe-period=30`（= 1 s），所以最多多录 1 s。
音频和声音图没有关键帧的概念，按时刻精确切。

**任何短于 `POSTROLL_SEC + PREROLL_SEC`（约 25 s）的离开都不会留下空洞** ——
短于 postroll 的由 ④ 吸收（同一个文件），再长一点的由下一段的 preroll 补上
（两个文件，内容首尾相接）。

**失效时向开放侧倒（fail-open）。** 如果 `TRIGGER_TIMEOUT_SEC`（默认 3 s）
内没收到 `record/trigger`，**切换到连续记录**。因为 stream-server 挂了、LAN
断了、检测节点卡死了 —— 无论哪种，"悄无声息地什么都没录到"都是最坏结果。
恢复后再回到门控状态。**门控状态本身也要写进 bag**，事后才能解释"这段为什么
是空的"。

### 6.3 记录的 topic

省略前缀 `/<ROBOT_NAME>/`。

| topic | 类型 | 来源 | 频率 |
|---|---|---|---|
| `fisheye/video` | `foxglove_msgs/CompressedVideo` | gst 的 `tee` | 30 fps |
| `realsense/video` | 同上 | gst 的 `tee` | 30 fps |
| `navcam/video` | 同上 | gst 的 `tee` | 30 fps |
| `onboard_mic/audio` | `teleop_msgs/AudioChunk` | gst 的 `tee` | 100 msg/s |
| `mic_array/audio` | `teleop_msgs/AudioChunk`（S16LE 原始） | gst | 100 msg/s |
| `soundmap/map` | `teleop_msgs/SoundMap` | gst | 15 Hz |
| `operator_mic/audio` | `teleop_msgs/AudioChunk` | 从 OME 接收 | 到达即写 |
| `record/clock_offset` | `teleop_msgs/ClockOffset` | robot-pc | 1 Hz |
| `record/gate` | `teleop_msgs/RecordGate` | robot-pc | 状态变化时 |
| `record/trigger` | `std_msgs/Bool` | **stream-server** | 5 Hz |

**为什么要自定义 msg。** 旧实现被两个类型卡过：`Float32MultiArray` 没有 header，
采样时刻无处安放；`AudioDataStamped` 不带 channels / rate，解码得靠一份外部表格。
两个 msg 定义就能解决：

```
teleop_msgs/SoundMap                teleop_msgs/AudioChunk
    std_msgs/Header header          std_msgs/Header header
    uint32  width                   string  encoding    # "S16LE" | "FLAC"
    uint32  height                  uint16  channels
    float32[] data                  uint32  sample_rate
                                    uint32  samples     # 每声道的样本数
                                    uint8[] data
```

这样**光靠 bag 就能解码**，同时甩掉对 `audio_common_msgs` 的依赖
（旧实现被"各发行版类型定义不一样"折腾过）。

### 6.4 时间

| 项 | 方针 |
|---|---|
| 单位 | UNIX 时间（纳秒） |
| **基准时钟只有 robot-pc 一个** | 保存的都是 robot-pc 采集或接收到的东西，不需要和别的机器做 NTP 同步 |
| gst 来源 | `unix_ns = segment.to_running_time(pts) + pipeline.get_base_time() + offset`，`offset = CLOCK_REALTIME − CLOCK_MONOTONIC`。**不要用 publish 那一刻的时刻** |
| 管线时钟 | **保持 MONOTONIC。** 切成 REALTIME 的话，NTP step 会让 PTS 跳变 |
| 怎么写进 mcap | `log_time` 和 `publish_time` 都填采样时刻，**并且在 payload 的 `header.stamp` 里再填一遍**。这样无论用哪个读取端都拿得到采样时刻 |
| 从 stream-server 来的 `record/trigger` | **没有时间戳**（就一个 bool）。robot-pc 按自己收到的时刻处理 —— 两机不需要 NTP 同步，这条也就不再是个问题 |
| clock offset | 以 1 Hz 记录整条序列。事后看它平滑就说明换算可信，有台阶就说明那里 NTP step 过 |
| 校验 | 开始录制时拍一下手。鱼眼、机体麦克风、16ch 会同时收到，可用来验证换算 |

### 6.5 数据量

以下是**录制期间**（即有人在场时）的值。

**码率取自 `robot-pc/config.env`**（`*_BITRATE`）—— 改那边的话这张表也要跟着改。
操作者麦克风那行按**单声道**算（源头是 AAC 48 kHz 1ch）；`speaker.py` 不写死
caps，OME 真要给回立体声的话这一行翻倍，实机上确认一次。

| 对象 | 码率 | 每小时 |
|---|---|---|
| 鱼眼 H.264 1080×1080 | 5 Mbps | 2.25 GB |
| RealSense color H.264 | 4 Mbps | 1.8 GB |
| nav cam H.264 | 3 Mbps | 1.35 GB |
| **16ch 阵列（S16LE 原始）** | **11.3 Mbps** | **5.1 GB** |
| 机体麦克风（S16LE 48 kHz 1ch） | 0.77 Mbps | 0.35 GB |
| 声音图 64×64 float32 @15 Hz | 2.0 Mbps | 0.88 GB |
| 操作者麦克风（Opus 解出来的 S16LE） | 0.77 Mbps | 0.35 GB |
| **合计** | **约 26.8 Mbps** | **约 12.1 GB** |

**16ch 原样存，不压。** 本来打算用 FLAC（能减半），但 **FLAC 最多 8 声道**，
16ch 要拆成两组各编一路、解码端再按时刻拼回去 —— 那是给"唯一的源数据"加一层
要长期维护的约定，而且编码要占 miniPC 的 CPU（那台机器同时还在编三路视频和
生成声音图）。多出来的 2.6 GB/小时不值得换这个复杂度。**真嫌大的话事后离线
转 FLAC**，`AudioChunk.encoding` 这个字段就是为那时候留的，读的人不用改。

**只在有人时录，所以实际消耗取决于占空比。** 按 20% 算，每小时墙钟时间约
2.4 GB，8 小时/天约 19 GB —— 1 TB 撑七周。连续记录的话 8 小时就是 97 GB/天
（1 TB 只够 10 天），**门控相当于 5 倍。** 启动时打印 `df`，低于阈值就告警。

**对外的带宽约 13 Mbps**（鱼眼 5 ＋ RealSense 4 ＋ nav 3 ＋ 声音图 0.5 ＋ 音频）。
这和记录的 26.8 Mbps 是两回事：16ch 不出机体，而声音图出去的是 H.264
（500 kbps 封顶），记录的是 float32 原值（2.0 Mbps）。

**记录用的 ROS 消息不占对外带宽。** 实测：同机 20 秒搬 32 MB，两个网卡上的
增量都在 0.1 MB 量级（Fast DDS 走共享内存）。出网的只有发现协议的心跳，
以及**别人主动订阅**的那一刻 —— 所以现场纪律是：活动期间不要从别的机器
`ros2 topic echo` 机体上的视频 topic，那会真的从 Wi-Fi 拉 5 Mbps 走。

---

## 7. 实现中会踩的坑

| 坑 | 内容 |
|---|---|
| **x264enc 的 PTS** | 为了避免负 DTS，PTS 上会加 **3600000 秒**的固定偏移。直接用 `buffer.pts` 会让记录的时刻差 41 天（真踩过）。过一道 `segment.to_running_time()` 就没了 |
| **x264enc 的 profile** | 上游不是 I420 时它会选 High 4:4:4。浏览器解不出来，而 OME 是 bypass 转发，SDP 里照样写着 baseline。表现为**协商成功但就是不出画面**。推流侧要固定 `video/x-h264,profile=baseline` |
| **`gstreamer1.0-nice`** | 从 OME 收 WebRTC 必装。光有 `libnice10` 不够 —— ICE 的实体是 `nicesrc`/`nicesink`，缺了只会打个警告然后 `create-answer` 静默失败。用 `gst-inspect-1.0 nicesrc` 确认。**robot-pc、stream-server、vlm-server 都要装** |
| **rclpy 和 libsoup 共存** | 一旦 `import rclpy`，libsoup 的 WebSocket 会让进程崩掉。GIO 调 libproxy 抛的 C++ 异常，被 rclpy 加载的 libunwind 劫持后栈展开失败直接 abort。**单独跑没事，一放进 ROS 节点就崩**，看上去像是 signalling 侧的问题。在文件开头设 `GIO_USE_PROXY_RESOLVER=dummy` 规避 |
| **gst 与 ROS 的 Python** | gst 节点同时需要 `rclpy` 和 `gi`，要在 ROS 的 Python 环境里跑 |
| **vlm-server 的 Python** | **三台机器的做法不一样，别照搬。** rog-server 是 24.04，`--system-site-packages` 的 venv 就能继承系统的 gi；**vlm-server 是 20.04，系统 `python3-gi` 只有 3.8 版，而 faster-whisper 要 ≥3.9** —— 继承不过来，要 pyenv 编一个 3.10 再 pip 装 PyGObject（**钉在 3.52 以下**：3.52 起要 girepository-2.0，20.04 只有 1.64。用 3.48.2）。实测可行，见 `vlm-server/README.md` |
| **UMA16v2** | 只能以 S32LE、16ch、interleaved 打开（仅支持 MMAP/RW_INTERLEAVED）。用 `audioconvert` 降到 S16LE 再记录 |
| **Xacti** | **只出 MJPG**（不出 H.264）。OME 的入口运不了 MJPG，所以到 H.264 的转换是绕不开的 |
| **DDS 跨 distro** | robot-pc 是 Humble、rog-server 是 Jazzy，**实测能通**（§7.1），不用做任何事。但官方不保证，**任何一边升级大版本后要重测** —— 这条不通录制触发就到不了，而且是静默的（→ fail-open 连续记录，盘满了才发现） |
| **组播与多网卡** | 组播过这个 AP 没问题。但**发送端必须选对网卡** —— 开发机上有 `tailscale0`，组播默认发到它上面，表现和「AP 挡组播」一模一样（§7.1）|
| **ctranslate2 找不到 CUDA 库** | `pip install faster-whisper` 不带 CUDA 运行库，装了 `nvidia-cublas-cu12` 之后链接器也不去 venv 的 `site-packages/nvidia/` 找。**表现极阴**：模型加载成功、`nvidia-smi` 上显存占着、`get_cuda_device_count()` 返回 1 —— 全绿，**只有编码器第一次真跑才炸**，而静音会被 `vad_filter` 挡在编码器之前，所以不出真人声测不出来。两台机器上都踩了。对策：启动脚本设 `LD_LIBRARY_PATH`，＋ `Transcriber` 启动时硬跑一次编码器预热 |
| **ASR 和检测抢 GPU** | 同一块 4070 上并发实测：检测中位不变（93.5 → 93.8 ms），4/60 个 tick 超 200 ms 预算，最差 247 ms —— **可以接受**（判定是按 tick 计数的，晚几十毫秒不改变结论）。**但注意检测节点盯的是「中位」耗时，这种拖慢不会触发它的告警** |
| **Tailscale（stream-server ↔ vlm-server）** | **节点共享（Share device）不行**，必须作为正式成员加入同一 tailnet。它先走 DERP、后台打洞成功后才静默升级为直连，所以正式开始前要先通信一会儿，等 `tailscale status` 显示 `direct` 再开始 |

### 7.1 DDS 实测（开发机 Humble ↔ rog-server Jazzy）

**结论：通。** 默认的 Fast DDS、两边 `ROS_DOMAIN_ID` 一致，不需要任何特殊配置。

开发机是 Ubuntu 22.04 / **Humble**，和 robot-pc 同 distro、挂在同一个 AP 上，
所以拿它当 robot-pc 的替身测。用**生产的确切配置**（`/boxie/record/trigger`、
`std_msgs/Bool`、发布端 BEST_EFFORT depth=1、订阅端 BEST_EFFORT depth=5、5 Hz）：

| | 结果 |
|---|---|
| 25 秒收到 | **124 条**（期望 125） |
| 首条延迟 | **0.2 s**（discovery 几乎瞬间） |
| `demo_nodes_cpp` talker/listener 对照 | 同样通（C++ 和 Python 订阅端都试过） |

所以**跨 distro 这一条不用做任何事**。ROS 2 官方确实不保证跨 distro 互通，
Humble(Fast DDS 2.6) ↔ Jazzy(Fast DDS 2.14) 恰好是能用的组合 —— 但**这是运气，
不是保证**：任何一边升级大版本之后都要重跑一次上面这个测试。

**★ 测这件事本身踩了两个坑，比结论更值得记。** 前后失败了四五次，全是测试方法
的问题，差点得出「跨 distro 不通」的错误结论：

1. **`nohup cmd &` 放在 `ssh host "..."` 里，会话一关进程就死。** 要写成
   `setsid nohup cmd > log 2>&1 < /dev/null &`，三样重定向一个都不能少。
2. **`pgrep -f <模式>` / `pkill -f <模式>` 会匹配到 ssh 自己那条命令行** ——
   因为模式字符串本身就在那条命令行里。表现是：「进程还活着吗」的检查**永远
   回答活着**（它匹配到了自己），而 `pkill -f` 会把自己的 shell 连同被测进程
   一起杀掉。**按 PID 杀**（`echo $! > pidfile`），或者 `pkill -x <进程名>`。

**组播的观察（仍然有效）**：`ros2 multicast send` 从开发机发，rog-server 收不到；
反向能收。原因是**开发机有 `tailscale0`，组播默认发到了它上面** —— 显式指定
LAN 网卡就通。ROS 通信不受影响（rog-server 的公告能到开发机，订阅端据此发起
单播就够了），但**多网卡的机器上这个坑要记得，它的表现和「AP 挡组播」一样**。
robot-pc 不在 tailnet 里，大概率没有这个问题。

**CycloneDDS 试过一次，反而不行**（两边都换 `rmw_cyclonedds_cpp`：topic 名字
能过去，但 `ros2 topic info -v` 显示 `Publisher count: 0`，Jazzy 侧刷
`Failed to parse type hash ... USER_DATA '(null)'`）。既然默认的 Fast DDS 本来
就通，**不要换 RMW**。两台机器上装了的 `rmw_cyclonedds_cpp` 留着无害 ——
`RMW_IMPLEMENTATION` 哪里都没设，默认仍是 Fast DDS。
