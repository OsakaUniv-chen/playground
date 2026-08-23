# vlm-server（3090PC）

**这台机器上只有一件事：VLM。** 收鱼眼和声音图、拉转写、判断该朝谁转头。

经 Tailscale 连 stream-server（**局域网的 `rog-server.local` 和 `192.168.1.10`
在这里都不通**，不同网段，用 tailnet 地址）。
设计见 [../system-architecture.md](../system-architecture.md) §5。

**语音转文字不在这里做。** 它跑在 stream-server 上（见
[那边的 README](../rog-pc/stream-server/README.md) 的「语音转文字」）——
3090 的 24 GB 要整块留给 VLM，放不下 whisper 的 1.8 GB
（Qwen2.5-VL-32B-AWQ 约 20 GB，两个加起来顶满）。

```
config.env       所有配置（**唯一出处**，脚本和代码里不写默认值）
run.sh           起动（建 tmux session）。还有 check / recv / transcript / fg
vlm.py           主循环：凑齐输入 → decide()。**decide() 还是空的**
recv.py          收流 ＋ 视频环形缓冲 ＋ 配对取帧（**副本**，见下）
transcript.py    从 stream-server 拉转写的客户端
status.py        tmux 窗口 0 的状态面板
ome_receiver.py  WebRTC 收流（**副本**，见下）
```

**部署路径**（和 `~/robot-pc/` `~/rog-pc/` 对称）：

```
~/vlm-server/       代码（rsync 上来的）
├── venv/           ★ 不进 git。gi ＋ VLM 那套
└── log/            ★ 不进 git（LOG_DIR）
    ├── onboard.txt        ┐ 窗口 1/2 tail 的就是这两个。内容是从
    ├── operator.txt       ┘ stream-server 拉回来镜像到本地的
    ├── vlm.txt            VLM 的判断。decide() 接上之后才有东西
    ├── status.json        状态面板读的
    └── vlm.log            进程本体的输出
```

**同步代码时不要 `--delete` 整个 `~/vlm-server`** —— `venv/`、`log/` 就在同一层。

`ome_receiver.py` 和 `recv.py` 都是**副本**，源头在
[../rog-pc/stream-server/](../rog-pc/stream-server/)，逐字节一致。
**改要改源头再拷过来**（架构 §1.2：每台机器各带一份，运行时不跨目录引用）：

```bash
diff recv.py ../rog-pc/stream-server/recv.py
diff ome_receiver.py ../rog-pc/stream-server/ome_receiver.py
```

---

## 三样输入，两条路

| 要什么 | 怎么来 | 大小 |
|---|---|---|
| 鱼眼画面（多帧） | 从 OME **持续拉 WebRTC**，本地环形缓冲 | 5 Mbps 恒定 |
| 声音图（多帧，和画面配对） | 同上 | 0.5 Mbps 恒定 |
| 最近 N 秒的转写 | `GET /transcript` 找 stream-server 要 | 几 KB / 次 |

**媒体不走 HTTP。** 架构 §1.2 定的是「出 OME 一律 WebRTC」，没有例外 ——
让 stream-server 用 HTTP 再送一遍帧，等于在 OME 之外另开一条媒体通路。
而且多帧的时候那样**也不省带宽**：8 帧 × 756² × 1 Hz ≈ 13.8 Mbps，比持续
拉流的 5.5 Mbps 还贵。持续收流在这台机器上实测只占 **45% 的一个核**（24 核），
不值得为它设计东西。

**只连视频 pad。** `fisheye` 那条流带着机体麦克风的音轨，但音频归 stream-server
处理了，这边不接那个 pad —— 少一路解码。

### 帧怎么取是这边的事

```
收流 30 fps ──抽稀到 BUFFER_FPS──▶ 环形缓冲（BUFFER_SEC 秒）
                                        │
                            frames(n=VLM_FRAMES, span=VLM_SPAN)
                                        │
                        每帧配上时刻最近的声音图 ──▶ decide()
```

| 配置 | 现在 | 是什么 |
|---|---|---|
| `BUFFER_SEC` | 15 | 缓冲留多久 |
| `BUFFER_FPS` | 5 | 按多少帧率往缓冲里塞（收流照收 30 fps） |
| `VLM_FRAMES` | 8 | 每次判断取几帧 |
| `VLM_SPAN` | 4 | 这几帧跨多少秒 |
| `DECIDE_INTERVAL` | 2 | 多久判断一次 [s] |
| `TRANSCRIPT_SECONDS` | 60 | 每次拉多长的转写 |

**★ 后四个都还没定**，要接上 VLM 之后拿实际效果调。**全是本机的 config，
改了不用碰 stream-server** —— 缓冲留得够长，取法怎么变都不影响那边。

**只按时间抽稀，不缩尺寸。** 1080×1080×3 = 3.5 MB/帧，5 fps × 15 s = 75 帧
≈ 262 MB（这台 62 GB，很宽裕）。存原始的好处是要多大尺寸是**造 prompt 时**
才决定的，这里先缩了就回不去了。

### ★ 配对差要盯着

两条流各自到达、各自抖动，`frames()` 按时间戳把声音图配到鱼眼上，每对都报
`dt`。**这里的时刻是「到达本机的时刻」，过了广域网** —— 鱼眼 30 fps、
声音图 15 fps（一帧 67 ms），实测 `dt` 落在 **±76～125 ms**，和一帧的间隔
同量级。

在 stream-server 上配会准一些（只受局域网和 OME 的抖动）。现在先这样，
**如果发现声音图的斑点和画面里的人对不上，这是头号嫌疑** —— 面板上那个
「配对差最大」超过 200 ms 会标红。

---

## 跑

```bash
./run.sh check        # 先查：gst element、OME 通不通、转写那条链通不通
./run.sh recv         # 只验收流，20 秒
./run.sh transcript   # 只验转写那条链（拉一次打出来）
./run.sh              # 起。建 tmux session 并把你接进去
```

**`check` 会把两条路分开查** —— 媒体走 WebRTC、转写走 HTTP，一条通不代表
另一条通。

### tmux 的窗口

和别的机器一样跑在一个 tmux session 里（`TMUX_SESSION`）—— ssh 起的话断线
不会把它带走，别的机器 `tmux attach -t vlm` 就能看同一个面板。

```
窗口 0  Status     状态面板。**Ctrl-C 在这个窗口 = 全停**
窗口 1  onboard    机体麦克风识别出来的文字
窗口 2  operator   操作者麦克风识别出来的文字
窗口 3  VLM        判断内容。**decide() 还是空的**，窗口先占着
窗口 4  vlm        进程本体的输出（报错都在这），外面套着重起循环
```

**只有一个进程在干活**（`vlm.py`）。窗口 1/2/3 是 `tail -f` 它写出来的文件。
窗口 1/2 的内容是每轮从 stream-server 拉回来、**按 (音源, 起始时刻) 去重**后
镜像到本地的（每轮拉的都是最近 N 秒，同一句会被拉到很多次）。

状态面板长这样：

```
  vlm-server 状态                2026-08-23 16:30:41
  OME  100.90.179.60:3333  (app/*)   tailnet，只取视频
  已运行 00:02:36        第 77 轮，每 2.0s 一轮

  输入        状态       收到        最新     形状
  --------------------------------------------------------------
  fisheye     ● 正常     4657 帧     0.0s     1080x1080
  soundmap    ● 正常     2326 帧     0.1s     64x64

  缓冲   鱼眼 68 帧 / 声音图 59 帧，跨 14.9s   上限 15s @ 5fps
  取帧   要 8 帧跨 4.0s，实际拿到 8 帧   配对差最大 -125ms

  转写   ● 通   最近 60s 有 8 句
         http://100.90.179.60:8770/transcript  成功 77 / 失败 0

  VLM    decide() 还是空的   接上之后判断写在窗口 3
```

**「形状」那一列是实际收到的，不是配置里写的。** 转写那行会把「确实没人说话」
（`有 0 句` ＋ 灰字提示）和「拉不到」（红字 ＋ 原因）分开显示 —— 这两种在
prompt 里该有不同的说法。

面板不认识任何一路的含义：`vlm.py` 每轮写一次 `status.json`，`status.py`
只负责摆出来。加一路输入、加一个统计量，改 `vlm.py` 的 `write_status()` 就行。

---

## 接 VLM 的时候

只要填 `vlm.py` 里的 `decide()` 那一个函数：

```python
def decide(frames, transcript):
    # frames: [{"t":.., "video": Frame, "pair": Frame|None, "dt": 秒|None}, ...]
    #         video 是鱼眼、pair 是时刻最近的声音图，Frame.array() 出 (h,w,3) uint8
    # transcript: TranscriptClient.fetch() 的返回
    #   utterances 空 ＋ ok 为真 = 确实没人说话
    #   ok 为假                 = 转写那一路坏了
    #   **这两种在 prompt 里该有不同的说法**（架构 §5.2）
    ...
    return {...}     # 返回非 None 就会写进 vlm.txt（窗口 3）
```

返回的东西之后要 `POST /decision` 给 stream-server 转成 ROS（架构 §5.3，
**两边都还没写**）。

参考 P6 的探针结果（32B-AWQ 在声音图上读方向 91.7%，`p6/try-VLM-server`）。
**显存要算账**：32B-AWQ 约 20 GB / 24 GB，用 vLLM 的话
`gpu_memory_utilization` 别用默认的 0.9。

---

## 环境

**这台是 Ubuntu 20.04，`/usr/bin/python3` 是 3.8，系统的 `python3-gi` 也只有
3.8 版。** 收流要 gi，所以要自己造一个新一点的 Python 再把 gi pip 进去：

```bash
# 已经做完了，下面是重装时照抄的
pyenv install 3.10.21                      # 已有
~/.pyenv/versions/3.10.21/bin/python3 -m venv ~/vlm-server/venv
~/vlm-server/venv/bin/pip install --upgrade pip wheel
~/vlm-server/venv/bin/pip install "PyGObject==3.48.2"   # ★ 要钉版本，见下
```

**PyGObject 要钉在 3.52 以下。** 3.52 起改用 girepository-2.0（GLib 2.80），
而 20.04 只有 1.64 —— 不钉的话 pip 拿最新版（3.54.x），编译期报一堆找不到
`girepository-2.0`。编译要 `libgirepository1.0-dev` 和 `libcairo2-dev`（已有）。

**不在 anaconda 里。** 这台机器上有 `~/anaconda3`（base 是 Python 3.10.9），
但里面 gi / numpy 一个都没有，`envs/` 是空的 —— 找过了，别再去那儿找。
另外 `~/venvs/ome` 是早先试 OME 收流建的（同样 pyenv 3.10.21 + PyGObject
3.48.2，只有 gi），和 `~/vlm-server/venv` 版本一致，省得以后自己骗自己。

**`vlmenv` 这个 shell 函数**（在 `~/.bashrc` 里）进这个环境。
**故意不自动激活** —— `.bashrc` 里 `~/ros2_ws` 的 overlay 和 colcon 要用
`/usr/bin/python3`。跑系统本身不需要它：`run.sh` 永远用 `config.env` 里的
`VLM_PYTHON`。

**★ rog-server 那台不能照抄这个做法** —— 那台是 24.04，系统 gi 就是
Python 3.12 的，`--system-site-packages` 直接继承就行。

---

## 已经验证过的（3090 实机，2026-08-23）

输入是从开发机灌进 rog-server OME 的假流；`operatormic` 那条推的是**真语音**
（仓库里那批 TTS 生成的英语片段拼成 63 秒）。

| 项 | 结果 |
|---|---|
| `./run.sh check` | 全绿：gst 4/4、tmux、OME 3333 通、**转写 HTTP 跨 tailnet 通**、typelib |
| tailnet | 3090 ↔ stream-server 是 `direct` 不是 DERP |
| 收流 | 只连两路视频，fisheye 4657 帧 / soundmap 2326 帧，形状 1080×1080 和 64×64 |
| **环形缓冲** | 68 帧跨 14.9 s（= `BUFFER_SEC=15` @ `BUFFER_FPS=5`），内存稳定 |
| **配对取帧** | `frames(8, 4)` 每次都拿到 8 帧，配对差 −76 ～ −125 ms |
| **拉转写** | 77 轮 77 成功 0 失败；真语音时「最近 60s 有 8 句」 |
| **镜像到窗口** | 拉回来的发话按音源去重写进 `operator.txt`，窗口 2 实时显示 |
| **空 vs 坏分得开** | 没人说话时 `ok=true` ＋ 0 句（灰字），拉不到时红字带原因 |
| tmux | 五个窗口都在，`remain-on-exit` 生效，重起循环带快失败退避 |

**转写内容不对是意料之中的** —— 素材是英语，而 `ASR_LANGUAGE=ja`
（出来是 `ブー、YES、YES`）。这一轮验的是**管路**，不是转写质量。

---

## 还没做

1. **`decide()`** —— VLM 本体。见上面「接 VLM 的时候」。
2. **`POST /decision`** —— 判断回传给 stream-server 转 ROS（架构 §5.3）。
   **stream-server 侧的接收端也还没写**（见
   [那边的「还没解决的」](../rog-pc/stream-server/README.md)）。
3. **取帧参数定值** —— `VLM_FRAMES` / `VLM_SPAN` / `DECIDE_INTERVAL` /
   `TRANSCRIPT_SECONDS` 现在的值都是待定的起点。
4. **拿真人日语验一次转写质量** —— 那是 stream-server 那边的活，但结果直接
   决定这边 prompt 里的转写有多可信。
