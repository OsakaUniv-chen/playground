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

**管线都在 Python 里，shell 只做调度**（采集时刻只有持有管线的进程算得出来）。
`RECORD_ENABLE=0` 时这些脚本一行 ROS 都不碰。

**部署路径**（和 rog-server 的 `~/rog-pc/` 对称）：

```
~/robot-pc/          代码（rsync 上来的）
├── ws/              teleop_msgs 的 colcon workspace（ROS_WS_SETUP）
├── bag/             录下来的 bag（RECORD_DIR）
└── log/             日志（LOG_DIR）
```

**同步代码时不要 `--delete` 整个 `~/robot-pc`** —— `ws/`、`bag/`、`log/` 在同一层。

`ome_receiver.py` 是**副本**，源头在
[../rog-pc/stream-server/ome_receiver.py](../rog-pc/stream-server/ome_receiver.py)。
**改要改源头再拷过来**，`diff` 一下就知道有没有走样。

## 跑起来

```bash
./start_gstreamer.sh
```

脚本建一个 tmux session（`TMUX_SESSION`）并把你接进去。再敲一次是接回去，
不会重复起。

```
窗口 0  Status     实时状态面板。**Ctrl-C 在这个窗口 = 全停**
窗口 1  fisheye    ┐
窗口 2  realsense  │
窗口 3  navcam     │ 每个进程的实时输出
窗口 4  soundmap   │
窗口 5  speaker    │ 下行（从 OME 收）
窗口 6  recorder   ┘ RECORD_ENABLE=1 时才有
```

`Ctrl-b` 加数字切窗口，`Ctrl-b d` 离开但不停。别的机器
`tmux attach -t boxie_gst` 能看同一个面板。

状态面板每 2 秒刷新，「备注」列是各进程自己每 10 秒打的那行 `[10s] ...`
原样借来显示 —— 加进程不用动面板。**重连和卡死分两列**：

- **重连** = 进程自己退了（多半在 OME / 网络那一侧）。
- **卡死** = 数据看门狗打死的：进程没退、管线还在 PLAYING，但一个 buffer 都
  不往外走（USB 抽风、驱动卡死）。

上行五条：`fisheye`（含机体麦克风）、`onboardmic`（同一份 AAC 单独再推一条，
给操作页面）、`realsense`、`navcam`、`soundmap`，全部 SRT 进 OME。
下行一条：`speaker`，WebRTC 从 OME 收操作者语音。

没接硬件时把 `USE_FAKE_SOURCES` 改成 `1`，设备那头换成测试源，编码和 SRT 还是真的。

## 故障恢复

每个进程由一个监视循环看着，一退出就重起，**设备解析在循环里面** —— 每轮重起
都重新按名字找设备。

| 情况 | 会不会自己恢复 |
|---|---|
| OME 还没起 / 名字解析不了 / 网络瞬断 | **会**，所以启动顺序无所谓 |
| SRT 端口连不上 | **会**（管线都不用退，SRT caller 自己重握手） |
| 相机拔插后 `/dev/videoN` 变号 | **会**，每轮重起重新解析 |
| 机体麦克风掉线 | **会**，退到静音源视频立刻回来；插回去后下次重起用回真麦克风 |
| 机体扬声器掉线 | **会**，插回去之前每 5 秒试一次（重连数会涨，这是对的） |
| 操作者那边没起推流 | **无所谓**，面板显示「静默」，不算故障 |
| 16ch 阵列掉线 | **会**，`soundmap.py` 退出 → 管道断 → 整条重起 |
| **管线活着但没有数据** | **会**，数据看门狗打死它再重起 |

## 数据看门狗

进程还在、管线还在 PLAYING，但一个 buffer 都不往外走 —— 光靠进程退出发现不了，
所以**每个进程自己盯自己**：

| 进程 | 盯什么 | 判死 |
|---|---|---|
| `cam.py` ×3 | `srtsink` 那端出去的包（pad probe） | `STALL_CHECK_SEC × STALL_MISSES`（15 s） |
| `soundmap.py` | 进来的音频 buffer | `INPUT_TIMEOUT_MS`（3 s） |
| `speaker.py` | **不盯**（操作者不说话时本来就没数据） | — |
| `recorder.py` | **不盯** | — |

发现卡死写一个 `<名字>.stalled` 记号再退出，`__supervise` 据此分两列记账。
`cam.py` 的探针放在**复用之后**，一个探针盖住整条链路。

**★ 探针必须订 buffer list。** `mpegtsmux` 推的是 buffer list 不是单个 buffer，
只订 `PadProbeType.BUFFER` 一次都不会响（表现是每 15 秒杀一条好流）；
而对着 buffer list 调 `get_buffer()` 会一秒刷几百条 GStreamer-CRITICAL。

## speaker.py（下行）

```
OME ──WebRTC(Opus)──▶ ome_receiver ─ appsrc ─ audioconvert ─ volume ─ alsasink(AT-CSP1)
```

```bash
source config.env && python3 speaker.py --fake --seconds 10   # 不碰声卡
```

- **放音必须用 AT-CSP1 本身** —— 它自带硬件回声消除，换个喇叭放就不认识那路信号。
- **caps 不写死**，用第一个 buffer 实际到的格式压给 `appsrc`。写死了 OME 那边
  一变就是 not-negotiated，而且只有这一路静默地死掉。
- **不挂数据看门狗**，面板上「静默」是正常状态。
- 抖动缓冲 `SPEAKER_JITTER_MS` 默认 100 ms。
- 音量是 `SPEAKER_GAIN`（gst 的 `volume`）。**`pactl` 那套无效** —— 管线用
  `hw:CARD=` 直接开 ALSA，绕过了 PulseAudio。

## soundmap.py

**声音图的一切都在这个文件里** —— 采集参数、周期、积分窗、麦克风几何、频带。
`start_gstreamer.sh` 连尺寸和帧率都是问它要的：

```bash
python3 soundmap.py --print-caps
# video/x-raw,format=BGR,width=64,height=64,framerate=15/1
```

**stdout 是数据通道，日志全走 stderr。**

生成器输出的是**原始一致率分数 `[0,1]`**，存进 bag 的就是它。送去显示的黄斑图
另做一层归一化：`max(值 − p99, 0)` 再逐帧 min-max（定稿见
`test-soundmap/final-decision/make_final_1bit_videos.py`）。**这层归一化不可逆**，
所以 bag 里存的必须是原始分数。

单独试（不接阵列）：

```bash
python3 soundmap.py --fake | gst-launch-1.0 fdsrc fd=0 \
  ! rawvideoparse format=bgr width=64 height=64 framerate=15/1 \
  ! videoconvert ! videoscale method=nearest-neighbour \
  ! video/x-raw,width=512,height=512 ! autovideosink
```

实测生成 15～17 ms/帧（15 Hz 的预算是 66.7 ms），N100 上旧实测 25.5 ms。
每 10 秒 stderr 打一行实际帧率，跟不上时带 ★。**跟不上的表现是掉帧不是报错。**

## 记录（recorder.py）

```
各 topic ──▶ 环形缓冲(PREROLL_SEC) ──┐
                                     ├──▶ rosbag2 / mcap（一个录制区间 = 一个目录）
record/trigger（stream-server 5 Hz）─┘ 门控
```

**判断在 stream-server，这边只管录。** 收到的就一个 bool。

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
| trigger ↓ | 继续写 `POSTROLL_SEC`（10 s） |
| postroll 期间又 ↑ | **不关文件，接着录** |
| `TRIGGER_TIMEOUT_SEC` 收不到 trigger | **切成连续录**（fail-open），恢复后回门控 |

- **不用 `ros2 bag record`** —— CLI 做不了前录。这里自己包一层 rosbag2_py 的
  `SequentialWriter`。
- **H.264 从关键帧切。** 吐前录时回退到「切点之前最近的关键帧」。GOP = 1 s，
  最多多录 1 s。
- **写进 bag 的时刻是采集时刻**（gst 的 PTS 换算来的），不是收到的时刻。
  `record/clock_offset` 以 1 Hz 记着 MONOTONIC↔REALTIME 的对应。
- **门控状态自己也进 bag**（`record/gate`）。
- topic 带 `/<ROBOT_NAME>/` 前缀进 bag。

**现场纪律：活动期间不要从别的机器 `ros2 topic echo` 机体上的视频 topic。**
平时这些消息走共享内存不上网，但 DDS 是"有人订就发"——  echo 一下鱼眼，
5 Mbps 就真的从 Wi-Fi 拉走。`ROS_DOMAIN_ID` 也因此设成了不常用的值。

**开关是 `RECORD_ENABLE`。** 置 1 时 `start_gstreamer.sh` 自动 source
`ROS_SETUP` / `ROS_WS_SETUP`、给各进程加 `--publish`、多起一个 recorder 窗口。

```bash
./setup.sh            # 装 + build + 查（要 sudo 密码）
./setup.sh --check    # 只查，不装不用 sudo —— 现场怀疑哪里不对先跑这个

source config.env
python3 recorder.py --force                                # 不等 trigger，连续录
python3 recorder.py --topics soundmap/map,record/trigger    # 只订这几条
```

缺哪个消息类型会**直接报错退出**并告诉你装什么，不做静默降级。

## 现场要确认的（config.env 里标了 ★）

| 项 | 怎么查 |
|---|---|
| `OME_HOST` | 填 `rog-server.local`，解析不了就换 `192.168.1.10`。查法：`getent hosts $OME_HOST` 再 `timeout 2 bash -c 'echo > /dev/tcp/$OME_HOST/3333'` |
| `ONBOARD_MIC_NAME` / `SPEAKER_NAME` | **部分匹配** `/proc/asound/cards`。AT-CSP1 的卡 id 由内核从 USB 产品名生成（`AT-CSP1` → `ATCSP1`），填 `CSP1` 两边都命中 |
| `ONBOARD_MIC_CHANNELS` / `_RATE` | AT-CSP1 已按厂商 spec 填好（1ch / 48 kHz / 16 bit）。换设备用 `arecord -D hw:CARD=<id>,DEV=0 --dump-hw-params` |
| `ONBOARD_MIC_GAIN` / `SPEAKER_GAIN` | 4.0 / 2.0。gst 的 `volume`，不是 `pactl` |
| `REALSENSE_FORMAT` / `NAVCAM_FORMAT` | `v4l2-ctl -d <dev> --list-formats-ext` |
| `CAM_NAVCAM_NAME` | `cat /sys/class/video4linux/*/name` |
| USB 口分开了没有 | `lsusb -t`。Xacti / UMA16v2 / RealSense 都是等时传输 |
| WebRTC 收流的依赖 | `gst-inspect-1.0 nicesrc`。**缺 `gstreamer1.0-nice` 的话只有操作者语音静默地不出声**。`speaker.py` 起来时会挡一道 |

## 还没做

代码这边闭合了，**剩下的全是只能在机体上做的事**：

1. `./setup.sh` 跑一次，然后 `./setup.sh --check` 全绿。
2. `BAG_STORAGE=mcap` 实跑一次 —— 开发机上没有 mcap 插件，这边全部是用
   sqlite3 验的。
3. 接真设备后确认 `config.env` 里剩下的 ★。
4. **开录拍手**，用 Foxglove 打开 bag：视频/机体麦克风/16ch 三条的波峰应当
   对齐（验时刻换算），视频能直接播（验 GOP 对齐）。
5. 量一次 N100 上的 CPU 和写盘速度。开发机上发布端多花约 1.5 个百分点，
   recorder 约 7%（sqlite3）。
6. 新加的 `onboardmic` 那条流实机没验过。

[start_boxie.sh](start_boxie.sh) 是从旧实现原样搬来的 ROS 那半（`rover` /
`keigan_motor`），属于**操作指令**那条链，而那条链还没设计（见
[../rog-pc/tele-server/README.md](../rog-pc/tele-server/README.md)）。
它读的是 `~/ros2_ws`，和这里的 config 对不上。
