# robot-pc（机体 miniPC）

传感器采集 → 编码 → 用 SRT 推给 stream-server 上的 OME，外加一条反方向的
操作者语音。设计见 [../system-architecture.md](../system-architecture.md) §2。

```
config.env            所有配置（**唯一出处**，脚本里不写默认值）
start_gstreamer.sh    全部收发，一个脚本管完（解析设备、起进程、看着它们）
cam.py                三路相机：采集 → 编码 → SRT，顺带把帧交给记录
soundmap.py           1-bit 声音图：采集 + 生成 + 全部算法参数
speaker.py            下行：从 OME 收操作者语音，放到机体扬声器
recorder.py           记录：所有 topic 进一个 bag，只在有人的时候录
gst_ros.py            记录那半的公共部分（时刻换算、ROS 节点）
ome_receiver.py       WebRTC 收流（**副本**，见下）
teleop_msgs/          记录用的自定义消息（要 colcon build）
setup.sh              一次性准备：装依赖 + build teleop_msgs + 现场检查
```

**管线都在 Python 里，shell 只做调度。** 采集时刻只有持有管线的进程算得出来
（`running_time + base_time + offset`），而记录要的就是它 —— 所以三路相机也从
`gst-launch` 搬进了 `cam.py`。不开记录（`RECORD_ENABLE=0`）时这些脚本一行 ROS
都不碰，推流部分和以前完全一样。

**部署路径**（和 rog-server 的 `~/rog-pc/` 对称）：代码和运行时数据都在
`~/robot-pc/` 下 ——

```
~/robot-pc/          代码（rsync 上来的）
├── ws/              teleop_msgs 的 colcon workspace（ROS_WS_SETUP）
├── bag/             录下来的 bag（RECORD_DIR）
└── log/             日志（LOG_DIR）
```

**同步代码时不要 `--delete` 整个 `~/robot-pc`** —— `ws/`、`bag/`、`log/` 就在
同一层。代码目录其实放哪都行，脚本都按自己的位置找 `config.env`。

**这个目录能单独扔到机体上跑**，不依赖仓库里别的目录。代价是
`ome_receiver.py` 是一份副本：源头在 [../rog-pc/stream-server/ome_receiver.py](../rog-pc/stream-server/ome_receiver.py)，
两份现在逐字节一样。**改要改源头再拷过来**，两份都在的时候
`diff ome_receiver.py ../rog-pc/stream-server/ome_receiver.py` 一下就知道有没有走样。

## 跑起来

```bash
./start_gstreamer.sh
```

就这一条。脚本会建一个 tmux session（名字在 `config.env` 的 `TMUX_SESSION`）
并把你接进去：

```
窗口 0  Status     实时状态面板。**Ctrl-C 在这个窗口 = 全停**
窗口 1  fisheye    ┐
窗口 2  realsense  │
窗口 3  navcam     │ 每个进程的实时输出
窗口 4  soundmap   │
窗口 5  speaker    │ 这条是下行（从 OME 收）
窗口 6  recorder   ┘ RECORD_ENABLE=1 时才有
```

`Ctrl-b` 加数字切窗口，`Ctrl-b d` 离开但不停。已经在跑的时候再敲一次
`./start_gstreamer.sh` 就是接回去，不会重复起。

状态面板长这样，每 2 秒刷新一次：

```
  robot-pc 流状态                2026-08-22 15:11:32
  OME  rog-server.local:9999  (default/app/*)
  编码 软件 x264
  看门狗 15s 没数据就重起

  进程        状态        已运行     重连    卡死     备注
  --------------------------------------------------------------------------
  fisheye     ● 运行中   00:12:41   0       0       5.0 Mbps 出去 / 记录 300 帧 + 音频 470 块
  realsense   ● 运行中   00:12:41   0       0       4.1 Mbps 出去 / 记录 300 帧
  navcam      ○ 重连中   -          7       3       上次退出 15:10:47
  soundmap    ● 运行中   00:12:38   0       1       输入 1000 buf / 生成 143 帧 25.5 ms/帧
  speaker     ● 运行中   00:12:38   0       0       静默（对面没在推？）
  recorder    ● 运行中   00:12:38   0       0       录制中 boxie_20260823_1058 12191 条 / 143 MB
```

备注那一列是各进程自己每 10 秒打的那行 `[10s] ...`，面板只是原样借来显示 ——
加一个进程不需要动面板。

**重连和卡死是两个不同的毛病，所以分了两列。**

- **重连** = 进程自己退了。多半在 OME / 网络那一侧（srtsink 连不上就退出）。
- **卡死** = 数据看门狗打死的。进程没退、管线还在 PLAYING，但一个 buffer 都
  不往外走了 —— USB 抽风、驱动卡死就长这样（见下面「数据看门狗」）。

哪一列一直涨都说明那条流有真问题，切到它的窗口看输出。soundmap 那一行还会
显示实际帧率（从 `soundmap.py` 的 stderr 抓的），跟不上时变红。

**用 tmux 是为了两件事**：ssh 起的话断线不会把推流一起带走；别的机器
`ssh` 进这台机器之后 `tmux attach -t boxie_gst` 就能看同一个面板。

上行四条：`fisheye`（含机体麦克风）、`realsense`、`navcam`、`soundmap`，
全部走 SRT 进 OME，用 streamid 区分。下行一条：`speaker`，走 WebRTC 从 OME
收操作者语音（**进 OME 一律 SRT，出 OME 一律 WebRTC**，架构文档 §1.2）。

没接硬件时把 `config.env` 的 `USE_FAKE_SOURCES` 改成 `1`，设备那一头换成测试源，
编码和 SRT 都还是真的。

## 故障恢复

每个进程由一个监视循环看着，一退出就重起，而且**设备解析在循环里面** ——
每轮重起都重新按名字找 `/dev/videoN`、重新看麦克风和扬声器在不在。

| 情况 | 会不会自己恢复 |
|---|---|
| OME 还没起 / 名字解析不了 / 网络瞬断 | **会。** 所以**启动顺序无所谓**，机体可以先于 OME 起来 |
| SRT 端口连不上 | **会**（而且管线都不用退出，SRT caller 自己一直重握手） |
| 相机拔了再插，`/dev/videoN` 变号 | **会。** 每轮重起重新解析，接回新的设备号 |
| 机体麦克风掉线 | **会。** 下一轮退到静音源，视频立刻回来；插回去之后的下一次重起自动用回真麦克风 |
| 机体扬声器掉线 | **会。** `speaker.py` 退出 → 重起时重新解析设备。插回去之前每 5 秒试一次（重连数会涨，这是对的） |
| 操作者那边没起推流 | **无所谓。** OME 返 404，接收端一直重试，所以**起动顺序也无所谓**。面板上显示「静默」，不算故障 |
| 16ch 阵列掉线 | **会。** `soundmap.py` 退出 → 管道断 → 整条重起 |
| **管线活着但没有数据**（USB 抽风、驱动卡死） | **会。** 数据看门狗打死它，然后照常重起。见下面 |
| 相机打开了但一帧都不出 | **会。** 同上 —— 看门狗认的是「有没有数据」，不是「有没有退出」 |

## 数据看门狗

进程还在、管线还在 PLAYING、日志和文件大小看着全都正常，但一个 buffer 都不
往外走 —— USB 抽风和驱动卡死就是这个样子。**光靠进程退出发现不了**，所以
**每个进程自己盯自己**：

| 进程 | 盯什么 | 判死 |
|---|---|---|
| `cam.py` ×3 | `srtsink` 那一端出去的包（pad probe） | `STALL_CHECK_SEC × STALL_MISSES`（默认 15 s） |
| `soundmap.py` | 进来的音频 buffer | `INPUT_TIMEOUT_MS`（3 s） |
| `speaker.py` | **不盯** —— 操作者不说话时本来就没数据 | — |
| `recorder.py` | **不盯** —— 它没有"必须一直有数据"这回事 | — |

发现卡死就写一个 `<名字>.stalled` 记号再退出，`__supervise` 据此把「卡死」
和「重连」分成两列记账。**判断放在持有管线的进程里**，比在外面猜日志准 ——
而且 `cam.py` 的探针放在**复用之后**：实测掐断音视频任意一路 `mpegtsmux` 就
不再出包，一个探针盖住整条链路（源、编码、复用、以及 sink 堵住不走）。

两个实测踩过的坑记在这里：`mpegtsmux` 推的是 **buffer list** 不是单个 buffer，
只订 `PadProbeType.BUFFER` 的探针一次都不会响（表现是每 15 秒杀一条好流）；
而对着 buffer list 调 `get_buffer()` 会一秒刷几百条 GStreamer-CRITICAL。

## speaker.py（下行）

从 OME 走 WebRTC 收操作者语音，放到机体扬声器：

```
OME ──WebRTC(Opus)──▶ ome_receiver ─ appsrc ─ audioconvert ─ alsasink(AT-CSP1)
```

单独试（不碰声卡，只看收流那一半）：

```bash
source config.env && python3 speaker.py --fake --seconds 10
```

几件要知道的事：

- **放音必须用 AT-CSP1 本身。** 它自带硬件回声消除，操作者的声音从它自己放
  出去，机体麦克风才不会把这段声音收回去又送回操作者那边。换个别的喇叭放，
  回声消除就不认识那路信号了。
- **caps 不写死**，用第一个 buffer 实际到的格式压给 `appsrc`，中途变了也跟着
  变。写死了 OME 那边一变就是 not-negotiated，而且只有扬声器这一路静默地死掉。
- **这条流不挂数据看门狗。** 操作者不说话、或者那边根本没起推流的时候本来
  就没有音频，拿"有没有数据"判死会一直误杀。面板上显示「静默」是正常状态，
  不是故障。真正的故障（声卡打不开、被别人占着）会让进程退出，走重连那一列。
- 抖动缓冲 `SPEAKER_JITTER_MS` 默认 100 ms。这是对话链路，宁可短一点：
  网络抖动时丢音比整段延后好。

## soundmap.py

**声音图的一切都在这个文件里** —— 采集参数、周期、积分窗、麦克风几何、频带、
GAIN。`start_gstreamer.sh` 只管把它 stdout 出来的原始 BGR 帧编码送走，
连尺寸和帧率都是问它要的：

```bash
python3 soundmap.py --print-caps
# video/x-raw,format=BGR,width=64,height=64,framerate=15/1
```

**stdout 是数据通道，日志全走 stderr。** 往 stdout 打印一个字都会让下游解析失败。

单独试（不接阵列）：

```bash
python3 soundmap.py --fake | gst-launch-1.0 fdsrc fd=0 \
  ! rawvideoparse format=bgr width=64 height=64 framerate=15/1 \
  ! videoconvert ! videoscale method=nearest-neighbour \
  ! video/x-raw,width=512,height=512 ! autovideosink
```

实测：生成 15～17 ms/帧（15 Hz 的预算是 66.7 ms），N100 上旧实测 25.5 ms。
每 10 秒 stderr 会打一行实际帧率，跟不上时带 ★ 警告。

## 记录（recorder.py）

```
各 topic ──▶ 环形缓冲(PREROLL_SEC) ──┐
                                     ├──▶ rosbag2 / mcap（一次录制 = 一个目录）
record/trigger（stream-server 5 Hz）─┘ 门控
```

**判断在 stream-server，这边只管录。** 收到的就一个 bool，连"为什么要录"都
不知道 —— 将来往那个信号里 OR 别的触发源（操作者按钮之类），这边一行不用改。

谁往里发（都由 `RECORD_ENABLE=1` 时的 `--publish` 打开）：

| topic | 谁发 | 内容 |
|---|---|---|
| `fisheye/video` `realsense/video` `navcam/video` | `cam.py` | `foxglove_msgs/CompressedVideo`，H.264 Annex-B 一帧一条 |
| `onboard_mic/audio` | `cam.py`（fisheye 那条） | **AAC 之前的原始 S16LE**，0.77 Mbps |
| `mic_array/audio` | `soundmap.py` | 16ch 原始 S16LE，100 msg/s |
| `soundmap/map` | `soundmap.py` | 生成器出来的原始 float32，15 Hz |
| `operator_mic/audio` | `speaker.py` | 到达即发 |
| `record/clock_offset` `record/gate` | `recorder.py` 自己 | |

| 阶段 | 动作 |
|---|---|
| 常态 | 内存环形缓冲里囤 `PREROLL_SEC`（10 s ≈ 34 MB） |
| trigger ↑ | **先把环形缓冲吐进 bag**，再转实时写入 |
| trigger ↓ | 继续写 `POSTROLL_SEC`（15 s） |
| postroll 期间又 ↑ | **不关文件，接着录**（人来来回回会把文件和对话都切碎） |
| `TRIGGER_TIMEOUT_SEC` 收不到 trigger | **切成连续录**（fail-open）。恢复后回到门控 |

几处值得知道的：

- **不用 `ros2 bag record`** —— CLI 做不了前录。这里自己包一层 rosbag2_py 的
  `SequentialWriter`。
- **H.264 从关键帧切。** 吐前录时回退到「切点之前最近的关键帧」，从 GOP 中间
  开始写的话开头几十帧解不出来。GOP = 1 s，所以最多多录 1 s。
- **写进 bag 的时刻是采集时刻**（消息 header 里那个，gst 的 PTS 换算来的），
  不是收到的时刻。`record/clock_offset` 以 1 Hz 记着 MONOTONIC↔REALTIME 的
  对应，事后看它平滑就说明换算可信。
- **门控状态自己也进 bag**（`record/gate`），事后才解释得了某一段为什么是空的。
- topic 名字带 `/<ROBOT_NAME>/` 前缀进 bag，`ros2 bag play` 放出来和现场一致。

**现场纪律：活动期间不要从别的机器 `ros2 topic echo` 机体上的视频 topic。**
平时这些消息走共享内存、一个字节都不上网（实测 20 秒 32 MB，网卡增量 0.1 MB
量级），但 DDS 是"有人订就发"—— 谁在另一台机器上 echo 一下鱼眼，5 Mbps 就
真的会从 Wi-Fi 拉走，和推流抢带宽。`ROS_DOMAIN_ID` 也因此设成了不常用的值。

**开关是 `config.env` 的 `RECORD_ENABLE`。** 置 1 时 `start_gstreamer.sh` 会
自动 source `ROS_SETUP` 和 `ROS_WS_SETUP`、给各进程加 `--publish`、并多起一个
recorder 窗口；置 0 就完全不碰 ROS（没装 ROS 的机器上照样跑全部推流）。

跑之前先做一次准备（装依赖、build 消息、把该确认的都查一遍）：

```bash
./setup.sh            # 装 + build + 查（要 sudo 密码）
./setup.sh --check    # 只查，不装、不用 sudo —— 现场怀疑哪里不对就先跑这个
```

`--check` 会把「现场要确认的」那张表全部跑一遍：ROS 那套齐不齐、gst 的
element 缺没缺、三个相机和三张声卡按名字找不找得到、录制盘还剩多少、
USB 拓扑长什么样。有 ★ 就是还没就绪，退出码非 0。

单独试（不接任何发布端，只验门控和写盘）：

```bash
source config.env
python3 recorder.py --force                       # 不等 trigger，直接连续录
python3 recorder.py --topics soundmap/map,record/trigger   # 只订这几条
```

缺哪个消息类型会**直接报错退出**并告诉你装什么 —— 少录一路而没人发现是最坏的
结果，所以这里不做静默降级。

## 现场要确认的（config.env 里标了 ★）

| 项 | 怎么查 |
|---|---|
| `OME_HOST` 指对了没有 | 现在填的是 `rog-server.local`（mDNS）。**解析不了就换成 `192.168.1.10`** —— rog-server 的静态地址。**别填成开发机那台 `ROG`（192.168.1.100）** —— 它也跑着一个 OME，会连上去然后整场都推给一台不参与实验的机器。查法：`getent hosts $OME_HOST` 再 `timeout 2 bash -c 'echo > /dev/tcp/$OME_HOST/3333'` |
| `ONBOARD_MIC_NAME` / `SPEAKER_NAME` | 一般不用改。是**部分匹配**，脚本拿它比 `/proc/asound/cards` 的两行；AT-CSP1 的卡 id 由内核从 USB 产品名生成（`AT-CSP1` → `ATCSP1`），填 `CSP1` 两边都命中。换了设备就 `cat /proc/asound/cards` 挑一段唯一的填 |
| `ONBOARD_MIC_CHANNELS` / `_RATE` | AT-CSP1 已按厂商 spec 填好（1ch / 48 kHz / 16 bit）。换了设备用 `arecord -D hw:CARD=<id>,DEV=0 --dump-hw-params`；对不上时 fisheye 窗口会打一行 ★（设备实际声道数从 `/proc/asound` 读） |
| `REALSENSE_FORMAT` / `NAVCAM_FORMAT` | `v4l2-ctl -d <dev> --list-formats-ext` |
| `CAM_NAVCAM_NAME` | `cat /sys/class/video4linux/*/name` |
| USB 口分开了没有 | `lsusb -t`。Xacti / UMA16v2 / RealSense 都是等时传输，同一个控制器会互相抢 |
| WebRTC 收流的依赖 | `gst-inspect-1.0 nicesrc`。**缺 `gstreamer1.0-nice` 的话只有操作者语音静默地不出声**（signalling 会通，answer 里没有 ICE）。`speaker.py` 起来时会挡一道 |

## 还没做

代码这边闭合了 —— 三路相机、声音图、操作者语音都在发，recorder 在录，
`start_gstreamer.sh` 一条命令全起。**剩下的全是只能在机体上做的事**：

1. `./setup.sh` 跑一次（装 foxglove-msgs / rosbag2-storage-mcap、build teleop_msgs，
   要 sudo 密码），然后 `./setup.sh --check` 全绿。
2. `BAG_STORAGE=mcap` 实跑一次 —— 开发机上没有 mcap 插件，这边全部是用
   sqlite3 验的（两者只差一个 storage id，但没在机体上跑过就是没跑过）。
3. 接真设备之后确认 `config.env` 里剩下的 ★：相机名、RealSense 的 `YUY2`、
   AT-CSP1 的卡 id、USB 有没有分口。
4. **开录拍手**，然后用 Foxglove 打开 bag：视频/机体麦克风/16ch 三条的波峰
   应当对齐（验时刻换算），视频能直接播（验 GOP 对齐）。
5. 量一次 N100 上的 CPU 和写盘速度。开发机上：发布端多花约 1.5 个百分点，
   recorder 约 7%（sqlite3），乘 2~3 是机体的量级。

另外 [start_boxie.sh](start_boxie.sh) 是从旧实现原样搬来的 ROS 那半
（`rover` / `keigan_motor`），和这套推流记录没有关系 —— 它属于**操作指令**那条
链，而那条链现在整个系统里都还没有设计（见
[../rog-pc/tele-server/README.md](../rog-pc/tele-server/README.md)）。
留着是因为它是机体这一侧仅存的参考；那条链定下来之前别动它，
也别指望它和这里的 `config.env` / `~/robot-pc/ws` 对得上（它读的是 `~/ros2_ws`）。
