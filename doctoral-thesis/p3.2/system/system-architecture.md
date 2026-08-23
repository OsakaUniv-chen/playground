# 系统架构（Boxie）

## 0. 机器

| 角色 | 文件夹 | 机器（hostname） | 地址 |
|---|---|---|---|
| **robot-pc** | `robot-pc/` | `boxie`，机体 miniPC | `boxie.local` |
| **stream-server** | `rog-pc/stream-server/` | `rog-server` | `rog-server.local` |
| **tele-server** | `rog-pc/tele-server/` | `rog-server`，**和 stream-server 同一台** | 同上 `:7779` |
| **tele-pc** | （无固定文件夹） | 局域网内任意一台，可换人换机器 | 不固定 |
| **vlm-server** | `vlm-server/` | `vlm-server` | 异地，走 tailnet |
| （开发机） | —— | `chen-pc`，**实验中不参与** | —— |

**stream-server 是枢纽。** 所有视频、音频汇聚到它上面的 OvenMediaEngine 再分发。**robot-pc ↔ vlm-server 之间没有直连**，vlm-server 只和 stream-server 通信。

**tele-server 只发操作页面，不碰媒体流**，
tele-pc 是操作者当天用的机器，自己直接和 OME 打交道（浏览器收流、推麦克风）。

**部署布局。** 仓库目录和机器上一一对应：rog-server 的两个服务都在 `~/rog-pc/` 下
（`stream-server/`、`tele-server/` 两个代码目录，启动脚本 `run_stream.sh` /
`run_tele.sh` 在上一层，见 `rog-pc/README.md`）；robot-pc 的代码和运行时数据在
`~/robot-pc/` 下（`ws/` 是 teleop_msgs 的 colcon workspace，`bag/` 录制，`log/`
日志），一次性准备用 `robot-pc/setup.sh`。

**配置的唯一出处是 `config.env`。** 各脚本不自带默认值，未设置就在启动时报错退出。

### 0.1 寻址

**地址只写在 `config.env` 里**，脚本和代码里一个都不出现。

| 从 → 到 | 地址 | 说明 |
|---|---|---|
| robot-pc → stream-server | `rog-server.local` | `robot-pc/config.env` 的 `OME_HOST` |
| tele-pc → stream-server | `rog-server.local` | 收流和推麦克风都指这里 |
| tele-pc → tele-server | `rog-server.local:7779` | 打开网页（`UI_PORT`） |
| stream-server → 自己的 OME | `127.0.0.1` | 同机走 loopback |
| vlm-server → stream-server | tailscale 地址（下表） | `.local` 和 `192.168.1.x` 在那边不通 |
| stream-server → robot-pc（ROS 触发） | DDS discovery | Humble ↔ Jazzy 实测能通，`ROS_DOMAIN_ID` 一致即可 |

`.local` 靠组播 mDNS，客户端要装 `libnss-mdns`。rog-server 的 IP 是 `nmcli`
静态配置的 `192.168.1.10`，所以任何时候都可以把 `OME_HOST` 直接换成 IP。

#### tailnet（vlm-server 走这条）

tailnet 是 **`taildf8663.ts.net`**，各机的 tailscale 名和 hostname 一致。
**robot-pc 不在 tailnet 里**，只走局域网。

| 机器 | tailnet 地址 |
|---|---|
| **stream-server**（rog-server） | **`100.90.179.60`** |
| **vlm-server** | **`100.104.252.121`** |
| 开发机（chen-pc） | `100.80.23.31` |

---

## 1. 传输路径

**进 OME 一律 SRT，出 OME 一律 WebRTC。** 

### 1.1 进 OME

| 来源 | stream key | 内容 | transport |
|---|---|---|---|
| robot-pc | `fisheye` | H.264 1080×1080@30 ＋ AAC（AT-CSP1 麦克风） | SRT（`mpegtsmux`） |
| robot-pc | `soundmap` | H.264 64×64@15 | SRT |
| robot-pc | `realsense` | H.264 848×480@30（仅 color） | SRT |
| robot-pc | `navcam` | H.264 1280×720@30 | SRT |
| robot-pc | `onboardmic` | AAC 48 kHz 1ch（AT-CSP1，和 `fisheye` 里那份同一次编码） | SRT（`mpegtsmux`） |
| tele-pc | `operatormic` | AAC 48 kHz 1ch | SRT（`mpegtsmux`） |
| **stream-server** | `rgb_sm` | H.264 1080×1080@15。鱼眼 ＋ 声音图叠加 | SRT（loopback） |
| **stream-server** | `human_detect` | H.264 1080×1080@5。鱼眼 ＋ 检测框 ＋ 录制标志 | SRT（loopback） |

- `rgb_sm` 和 `human_detect` 是 stream-server 生成再推回 OME 的**监视流**（§3），
  **不进 bag、不参与任何判断**。播放地址见 `rog-pc/stream-server/README.md`。
- SRT 用 `latency=20`。OME 的 SRT provider 按 `streamid=<vhost>/<app>/<key>`
  接收，写在 URI 的 query 里（OME 0.19.0）。
- **音频一律 48 kHz**：OME 转 Opus 时时钟率固定 48000（RFC 7587）。
- 机体麦克风**同时走两条路**：复用进 `fisheye`（给 ASR、vlm-server、录制），
  再单独推一条 `onboardmic`（给操作页面）。AAC 只编一次，`tee` 分叉。
  操作页面的画面走 `rgb_sm`，那条要多绕一圈「解码→叠加→重编→回推」；
  声音单独直达才不会跟着慢几百毫秒。**代价是口型对不上**，这是有意的取舍。
- **麦克风打不开不连累视频** —— 设备不在就退回静音的 `audiotestsrc`，推流照常。

### 1.2 出 OME

| 接收方 | 取什么 |
|---|---|
| tele-pc（浏览器） | **`rgb_sm`**（画面，已叠好）＋ **`onboardmic`**（声音）；`realsense`、`navcam`；要看检测再加 `human_detect` |
| robot-pc（机体扬声器） | `operatormic` |
| stream-server 自身（localhost） | `fisheye` → 人物检测；`fisheye` ＋ `soundmap` → `rgb_sm` 叠加；`fisheye` 音轨 ＋ `operatormic` → 语音转文字 |
| vlm-server | `fisheye` `soundmap`，**只接视频轨** |

- gst 侧的接收端统一用 `ome_receiver.py`（多路封装 `recv.py`），源头在
  `stream-server/`。**每台机器各带一份副本，运行时不跨目录引用**；改的时候改
  `stream-server/` 那份再拷过去，副本之间逐字节一致（`diff` 检查）。
- 所有拉流端必须装 `gstreamer1.0-nice`（§7）。
- **不用 LLHLS**（分段协议的 1～2 秒延迟会挤占 §6.2 的 preroll 预算）。

## 2. robot-pc

### 2.1 传感器

| 设备 | 型号 | 采集 | 推流 | 记录 |
|---|---|---|---|---|
| 鱼眼相机 | Xacti **CX-MT500** | MJPG 1920×1080@30 | 裁中央 1080×1080 后 H.264 | ○ |
| 深度相机 | RealSense | color 848×480@30 | H.264 | ○ |
| 导航相机 | nav camera | 1280×720@30 | H.264 | ○ |
| 麦克风／扬声器 | **AT-CSP1** | ALSA `hw:CARD=`，增益 4.0 / 2.0 | AAC（复用进 `fisheye`） | ○ |
| 16ch 阵列 | miniDSP **UMA16v2** | S32LE 16ch 44.1 kHz interleaved | **不推** | ○（S16LE 原始） |

- **RealSense 只开 color**，depth 流根本不打开。
- **16ch 不进 OME**：在机体内算成声音图（64×64、15 Hz）后只推声音图，
  原始 16ch 只存在于 robot-pc 的本地记录。
- **★ 设备一律按名字引用**（`/dev/videoN`、`hw:N` 随 USB 枚举顺序变化）。
  相机查 `/sys/class/video4linux/*/name`，ALSA 用 `hw:CARD=<名字>,DEV=0`。
- **★ Xacti / UMA16v2 / RealSense 要挂在不同的 USB 控制器上**（都是等时传输，
  同控制器会抢带宽掉帧）。用 `lsusb -t` 确认根路径不同。
- **AT-CSP1 的增益加在 gst 管线里**（`volume` element，`ONBOARD_MIC_GAIN=4.0` /
  `SPEAKER_GAIN=2.0`）。采集侧的增益在 `tee` 之前，所以推流和记录是同一份。
  **`pactl` 那套音量设置对这条链路无效** —— 管线用 `hw:CARD=` 直接开 ALSA，
  绕过了 PulseAudio。

### 2.2 推流管线

```
v4l2src(MJPG) → jpegdec → videocrop(左右各420px) → videoflip(180°)
              → h264enc → tee ─┬→ mpegtsmux → srtsink   (送 OME)
                               └→ appsink → ROS publish (送记录)
```

- 相机倒装，转 180°；裁切 `[:, 420:1500]`。
- **只编码一次**：`tee` 分叉的是已编好的 H.264。有 VA-API 就用核显，
  缺 vaapi element 时自动退回 software。
- **每条流套重连循环**，启动顺序无所谓，机体可以先于 OME 起来。

### 2.3 声音图

`alsasrc(16ch)` → appsink → 生成器 → appsrc → H.264 → SRT（用 Python 把 appsink
和 appsrc 接起来）。

| 项 | 值 | 备注 |
|---|---|---|
| 周期 | **15 Hz** | 跟不上时表现为掉帧而非报错，`soundmap.py` 每 10 s 报一次超时帧数 |
| 积分窗 | 464 ms | 滑窗，比周期长 |
| 分辨率 | 64×64 | 原样推出去，放大交给接收端 |
| 码率 | 500 kbps | |
| 生成器输出 | 原始一致率分数 `[0,1]` | 存进 bag 的就是这个值，不做面向显示的变换 |
| 显示归一化 | `max(值 − p99, 0)` 再逐帧 min-max | 减 p99 是本底抑制；直接 min-max 的话安静场景会整片发亮 |
| 频带 / 滤波器 | 2000–8000 Hz，4 阶 | |
| 麦克风对 / 延迟 | 全部 120 对，整数精确延迟 | |

---

## 3. stream-server（rog-server）

| 常驻进程 | 内容 |
|---|---|
| **OME** | 接收 robot-pc / tele-pc，分发给 tele-pc、vlm-server、robot-pc 和 localhost |
| **人物检测节点** | 从 OME（localhost）拉 `fisheye`，**降到 5 fps** 做检测，结果用 ROS 发给 robot-pc；把带框和录制标志的画面推回 OME（`human_detect`） |
| **声音图叠加** | 从 OME 拉 `fisheye` ＋ `soundmap`，叠好推回 OME（`rgb_sm`）。独立进程 |
| **语音转文字（ASR）** | 从 OME（localhost）拉两路音频，按静音切句后用 faster-whisper（`medium`）转成文字。结果由 vlm-server 拉走（§3.1）。独立进程、独立 venv |
| **HTTP 服务** | 两件事：把转写发给 vlm-server、收 vlm-server 的判断转成 ROS（§3.1、§5.3） |

- 检测结果就是 §6.2 的录制触发（`std_msgs/Bool`）。**不是变化时才发，而是 5 Hz
  常发**，这样 robot-pc 能察觉它停了并 fail-open。
- **三个 GPU 进程各自独立，venv 也分开**（ctranslate2 要 CUDA 12 的 cudnn，
  检测那个 venv 里的 torch 是 cu130）。它们只共享 GPU，不共享进程空间。
- 操作页面由 tele-server 提供，不经过 OME。

### 3.1 和 vlm-server 之间的两个 HTTP 端点

媒体一律走 OME，这里不重复送。跨 tailnet 的 HTTP 只有这两样：

| 端点 | 方向 | 内容 |
|---|---|---|
| `GET /transcript?seconds=N` | vlm-server 拉 | 最近 N 秒的转写（几 KB） |
| `POST /decision` | vlm-server 推 | VLM 的判断 → stream-server 转成 ROS 发给 robot-pc（几百字节） |

**两个端点在两个进程里。** `/transcript` 在 ASR 那个进程，`/decision` 单独一个
（它要 rclpy，见 §7）。ASR 挂了不影响判断送到机体。

---

## 4. tele-server 与 tele-pc

### 4.1 职责

| 机器 | 跑什么 | 碰媒体流吗 |
|---|---|---|
| **tele-server** | UI 服务（Flask ＋ 静态文件），把操作页面发出去 | **不碰** |
| **tele-pc** | ① 浏览器打开那个页面 ② 本地一个 gst 进程推麦克风 | 碰，直接和 OME 做 |

页面上的 OvenPlayer 直接向 `rog-server.local` 建 WebRTC 连接，tele-server 不在
这条链上。局域网里任意一台机器都能当 tele-pc，同时开两台看也行。

**手柄在 tele-pc 上能用**（Chrome 实测，非 secure context 也不受影响）。

---

## 5. vlm-server

这台机器上只有 VLM，24 GB 显存整块给它。

### 5.1 输入

| 要什么 | 怎么来 |
|---|---|
| 鱼眼画面 | 从 OME 持续拉 WebRTC，本地缓冲 |
| 声音图 | 同上 |
| 最近 N 秒的转写 | `GET /transcript` 找 stream-server 要（§3.1） |

- **只接视频轨**：`fisheye` 这条流里视频和机体麦克风的音轨是分开的两条轨，
  vlm-server 只订阅视频那条（`recv.py` 的 `fisheye` 频道），不订阅音轨
  （`onboard` 频道）—— 音频归 stream-server 处理。
- **有界的环形缓冲**：判断时按需要取 N 帧、跨多长时间，并按时刻把声音图和鱼眼
  配成对。帧数、尺寸、缓冲深度都是本机的 config，改了不用碰 stream-server。
- 帧的时刻是**到达 vlm-server 的时刻**，不是采集时刻，实测配对差
  **±76～125 ms**。每一对的时刻差都报出来，**面板上超过 200 ms 标红**。
  → **待检讨**（§8）。

### 5.2 转写

转写在 stream-server 上做（§3），这边在判断的那一刻去拉：

```
GET http://<stream-server tailnet>:<port>/transcript?seconds=60
```

窗长是查询参数。**没人说话就是空列表**，响应里每一项自带 `ok`，用来区分
「确实没人说话」和「ASR 坏了」。

### 5.3 输出

VLM 的判断用 `POST /decision` 发给 stream-server，由它转成 ROS 消息。
判断逻辑本身不在本文档范围内。

---

## 6. 数据记录

### 6.1 录在 robot-pc，不外传

格式用 **rosbag2 / mcap**。gst 来的数据也封成 ROS 消息，视频、声音、指令
**进同一个 bag**；将来加 ROS 节点的 topic 也进同一个 bag。

**一个录制区间 = 一个 bag。** 每次门控从关到开就新建一个目录
（`boxie_<YYYYmmdd_HHMMSS>`），关闭时收尾；下一个区间是新的一个。
`BAG_SPLIT_MB`（默认 2048）是**这个目录内部**的分片，和区间边界无关 ——
任何读取端都把一个目录当成一段连续记录。

### 6.2 只在有人的时候录

判断在 stream-server（§3），记录在 robot-pc。**robot-pc 不做判断。**

```
/<ROBOT_NAME>/record/trigger    # stream-server 以 5 Hz 发布
    std_msgs/Bool               #   就一个 bool：要不要录
```

只发一个 bool，不发人数也不发时间戳；人数和框的位置留在 stream-server 自己的
流水账里（见 `rog-pc/stream-server/README.md`）。recorder 是自建节点，包一层
`rosbag2_py` 的 `SequentialWriter`（`ros2 bag record` 的 CLI 做不了前录）。

| 阶段 | 动作 |
|---|---|
| ① 常态 | 在内存环形缓冲里持续囤 `PREROLL_SEC`（默认 10 s）≈ 34 MB |
| ② 收到 `true` | 先把环形缓冲吐出去，再转入实时写入 |
| ③ 收到 `false` | 继续写 `POSTROLL_SEC`（默认 10 s） |
| ④ postroll 期间又收到 `true` | **不关闭，延长** |

- **切点按 robot-pc 自己收到的时刻定。** 链路延迟 0.2～0.5 s ＋ 判定迟滞
  （近 0.4 s，远 1～3 s）全部由 preroll 吸收。
- **H.264 必须从 GOP 边界开始。** 吐环形缓冲时回退到
  `收到 true 的时刻 − PREROLL_SEC` **之前最近的关键帧**再切；
  `keyframe-period=30`（1 s），最多多录 1 s。音频和声音图按时刻精确切。
- 任何短于 `POSTROLL_SEC + PREROLL_SEC`（20 s）的离开都不会留下空洞。
- **fail-open：** `TRIGGER_TIMEOUT_SEC`（默认 3 s）内没收到 `record/trigger`
  就**切换到连续记录**，恢复后回到门控状态。**门控状态本身也写进 bag。**

### 6.3 记录的 topic

省略前缀 `/<ROBOT_NAME>/`（`ROBOT_NAME=boxie`，所以实际是 `/boxie/…`）。

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

自定义 msg（`robot-pc/teleop_msgs/`，`setup.sh` 软链进 `~/robot-pc/ws/src/` 编译）：

```
teleop_msgs/SoundMap                teleop_msgs/AudioChunk
    std_msgs/Header header          std_msgs/Header header
    uint32  width                   string  encoding    # "S16LE" | "FLAC"
    uint32  height                  uint16  channels
    float32[] data                  uint32  sample_rate
                                    uint32  samples     # 每声道的样本数
                                    uint8[] data
```

光靠 bag 就能解码，不依赖 `audio_common_msgs`。

### 6.4 时间

| 项 | 方针 |
|---|---|
| 单位 | UNIX 时间（纳秒） |
| **基准时钟只有 robot-pc 一个** | 不和别的机器做 NTP 同步 |
| gst 来源 | `unix_ns = segment.to_running_time(pts) + pipeline.get_base_time() + offset`，`offset = CLOCK_REALTIME − CLOCK_MONOTONIC`。**不要用 publish 那一刻的时刻** |
| 管线时钟 | **保持 MONOTONIC**（切成 REALTIME 会被 NTP step 弄跳变） |
| 怎么写进 mcap | `log_time`、`publish_time` 和 payload 的 `header.stamp` 都填采样时刻 |
| `record/trigger` | 没有时间戳，robot-pc 按自己收到的时刻处理 |
| clock offset | 以 1 Hz 记录整条序列 |
| 校验 | 开始录制时拍一下手，鱼眼／机体麦克风／16ch 会同时收到 |

### 6.5 数据量

**录制期间**的值。码率取自 `robot-pc/config.env`（`*_BITRATE`），改那边这张表
也要跟着改。操作者麦克风按**单声道**算（源头是 AAC 48 kHz 1ch）；`speaker.py` 不
写死 caps，OME 真给回立体声的话这一行翻倍，**要在实机上确认一次**。

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

- **16ch 原样存，不压**（FLAC 最多 8 声道）。真嫌大就事后离线转，
  `AudioChunk.encoding` 这个字段就是为那时候留的。
- 按 20% 占空比算，8 小时/天约 19 GB，1 TB 撑七周。启动时打印 `df`，低于阈值告警。
- **对外带宽约 13 Mbps**（鱼眼 5 ＋ RealSense 4 ＋ nav 3 ＋ 声音图 0.5 ＋ 音频），
  和记录的 26.8 Mbps 是两回事。
- **记录用的 ROS 消息不占对外带宽**（Fast DDS 走共享内存）。但**别人主动订阅**
  就会真的走 Wi-Fi —— 现场纪律：活动期间不要从别的机器 `ros2 topic echo`
  机体上的视频 topic。

---

## 7. 实现约束

| 项 | 约束 |
|---|---|
| **x264enc 的 PTS** | PTS 上有 **3600000 秒**的固定偏移。取时刻必须过 `segment.to_running_time()`，不能直接用 `buffer.pts` |
| **x264enc 的 profile** | 推流侧固定 `video/x-h264,profile=baseline`。否则上游非 I420 时会选 High 4:4:4，浏览器解不出来，而 OME 是 bypass 转发、SDP 里照样写 baseline —— 表现为协商成功但不出画面 |
| **`gstreamer1.0-nice`** | 从 OME 收 WebRTC 必装（光有 `libnice10` 不够），缺了 `create-answer` 静默失败。**robot-pc、stream-server、vlm-server 都要装**，用 `gst-inspect-1.0 nicesrc` 确认 |
| **rclpy ＋ libsoup** | 同一进程里会 abort。在文件开头设 `GIO_USE_PROXY_RESOLVER=dummy` |
| **gst ＋ ROS 的 Python** | gst 节点同时要 `rclpy` 和 `gi`，在 ROS 的 Python 环境里跑 |
| **venv 的做法两台不一样** | rog-server 是 24.04，`--system-site-packages` 的 venv 就能继承系统的 gi。**vlm-server 是 20.04**，系统 `python3-gi` 只有 3.8 而 faster-whisper 要 ≥3.9 —— 继承不过来，要 pyenv 装 3.10 ＋ pip 装 PyGObject **3.48.2**（3.52 起要 girepository-2.0，20.04 只有 1.64）。见 `vlm-server/README.md` |
| **UMA16v2** | 只能以 S32LE、16ch、interleaved 打开。用 `audioconvert` 降到 S16LE 再记录 |
| **Xacti** | 只出 MJPG，到 H.264 的转码绕不开 |
| **DDS 跨 distro** | robot-pc 是 Humble、rog-server 是 Jazzy，用默认 Fast DDS ＋ 两边 `ROS_DOMAIN_ID` 一致，**实测能通**。**不要换 RMW**（CycloneDDS 实测不通） |
| **组播与多网卡** | 发送端必须选对网卡。机器上有 `tailscale0` 时组播默认发到它上面，表现和「AP 挡组播」一样 |
| **ctranslate2 的 CUDA 库** | faster-whisper 不带 CUDA 运行库，要装 `nvidia-cublas-cu12`，而链接器**不会**去 venv 的 `site-packages/nvidia/` 找。所以启动脚本必须设 `LD_LIBRARY_PATH`，并在 `Transcriber` 启动时硬跑一次编码器预热 —— 否则模型加载、显存占用、`get_cuda_device_count()` 全绿，只有真人声进到编码器才炸 |
| **Tailscale** | 必须作为正式成员加入同一 tailnet，**节点共享（Share device）不行**。它先走 DERP，正式开始前要先通信一会儿，等 `tailscale status` 显示 `direct` |

---

## 8. 待检讨

已知没做、但暂时不影响跑起来的事。

| 项 | 现状 |
|---|---|
| **`rgb_sm` 没有做时间戳对齐** | `soundmap_overlay.py` 拿到哪帧就叠哪帧，鱼眼和声音图之间没有按时刻配对。作为监视流够用，但**这条流不能当作有时刻含义的数据**。要用的话得先补对齐 |
| **vlm-server 拿不到采集时刻** | 帧的时刻是到达时刻（§5.1），跨广域网。要拿到真正的采集时刻，得让时刻跟着媒体一起走（OME 的扩展头 / 单独的旁路 topic），还没定用哪条 |
| **操作指令通路** | `UI_ROS_ENABLE=1` 之后 UI 会发 ROS 指令，但**机体那一侧还没接** |
