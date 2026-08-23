# tele-server（操作 UI 服务）

只做一件事：把操作页面发出去。**媒体流不经过这里** —— 页面里的 OvenPlayer 直接
向 `rog-server.local` 的 OME 建 WebRTC 连接。

部署在 rog-server（**`rog-server.local`** = 192.168.1.10）上，和 OME 同机
（代码分开放，职责不同）。**不是开发机那台 `ROG`（192.168.1.100）** —— 见
[../../system-architecture.md](../../system-architecture.md) §0。

设计见 [../../system-architecture.md](../../system-architecture.md) §4。

```
config.env        所有配置（**唯一出处**）
app.py            Flask ＋ Socket.IO：发页面、把浏览器的指令转成 ROS
templates/        页面（一个 base.html）
static/js/        gamepad.js（手柄 → 指令）、websocket.js（Socket.IO 收发）
static/vendor/    ovenplayer.js、socket.io.min.js
static/css/       样式
../run_tele.sh    起动（**在 `rog-pc/` 那一层**，和 run_stream.sh 并排）
```

---

## 这份代码是搬过来的

搬自旧实现的 `pc-c-operator/`（git `f91be30`）。**旧实现里 UI、OME、浏览器
三者同机**（都在操作者自己那台 PC-C 上）；架构 §4 把它们拆开了：页面由
rog-server 上的这个服务发，浏览器在 tele-pc 上，媒体一点都不经过这里。

搬过来改了这些：

| 改了什么 | 为什么 |
|---|---|
| 页面里的 OME 从 `ws://localhost:3333` 改成 `ws://rog-server.local:3333` | 浏览器和 OME 不再同机 |
| `STREAM_KEY_MAIN` → `STREAM_KEY_FISHEYE` | 新结构里 stream key 改名了 |
| 配置从 `common/config.env` ＋ `pc-c-operator/config.env` 两层变成本目录一份 | `common/` 那一层随四机结构一起没了 |
| 绑定地址从写死 127.0.0.1 变成 `UI_BIND` | 见下面 ★★② |
| 手臂指令默认关掉（`ARM_ENABLE=0`） | 见下面 ★ |
| 起动脚本挪到 `../run_tele.sh` | 和 `run_stream.sh` / `run_overlay.sh` 并排（架构 §0） |
| 注释里的 PC-B/PC-C/PC-D 换成新机器名 | —— |

**没搬过来的两个文件**，它们不属于这里：

- **`head_relay.py`** —— 旧实现里它在 PC-C 上收 PC-D 的头部指令再转成 ROS。
  那正是架构 §5.3 那条 `POST /decision` → ROS 的桥，**属于 stream-server**。
  等写那个桥的时候从 `f91be30` 取它当底子。
- **`operator_mic_send.py`** —— 推操作者麦克风，那是 **tele-pc** 的活，
  而架构 §4.2 明说这部分不在本仓库管（tele-pc 是临时指定的机器）。
  要照着做的话，形态是：`alsasrc(hw:CARD=<耳机>)` → AAC 48 kHz 1ch → SRT
  进 OME，stream key `operatormic`。

---

## 手柄能用（我先前说反了，实测更正）

先前这里写着「Gamepad API 只在 secure context 里可用，所以从 tele-pc 打开
`http://rog-server.local:7779` 读不到手柄」。**那是错的**，来源是旧实现代码里
的一句注释，我没有验证就当成了事实。

实测（Chrome 127，从 `http://192.168.1.100:8123` 这个非 localhost 的 http 源）：

| origin | `isSecureContext` | `navigator.getGamepads` | 调用 | 槽位 |
|---|---|---|---|---|
| `http://192.168.1.100:8123` | **false** | `function` | 不抛异常 | 4 |
| `http://localhost:8123` | true | `function` | 不抛异常 | 4 |

**两者完全一样。** `isSecureContext` 确实是 false，但 Gamepad API 没有被它挡住。
所以 tele-pc 上插手柄、浏览器开 `http://rog-server.local:7779`，是可以工作的。

**没测的：Firefox。** headless 下它不发探针请求，测不到。真要用 Firefox 的话
现场确认一下 —— 打开 devtools 敲 `navigator.getGamepads()` 就知道。

---

## 操作页面没有认证

它绑在局域网上（`UI_BIND=0.0.0.0`，tele-pc 要能打开），而这个页面**能开动
机体**。`UI_SECRET` 是 Flask 的会话密钥，不是门禁。同一个 AP 上谁打开这个地址
都能开车。现场是封闭的 AP，暂按可接受处理。

`UI_ROS_ENABLE` 默认是 **0**（只发页面，指令不发出去）—— 但那是因为下面那条
（操作指令通路还没设计、机体那一侧还没接），不是因为这条。

---

## ★ 手臂指令现在发不出去

旧实现用 `audio_common_msgs/BoxieMotors` 发手臂角度 —— 那个类型来自旧实现搬来
的一份第三方消息包，**新结构里没有**（`teleop_msgs` 只有记录用的四个类型）。

而且**整条操作指令通路本来就还没设计**：架构文档开头把「电机驱动、操作指令
这些 ROS 节点」划在范围外，所以从手柄到机体的 `rover` / 头部电机这一段，
现在系统里没有任何一处写着它怎么走。机体那一侧仅存的参考是
[../../robot-pc/start_boxie.sh](../../robot-pc/start_boxie.sh)（旧实现原样搬来的，
读的是 `~/ros2_ws`，和现在的 config 对不上）。

现在的状态：

| | 用什么类型 | 能不能发 |
|---|---|---|
| `rover/twist` | `geometry_msgs/Twist` | **能**（到处都有这个类型） |
| `arm/command` | `audio_common_msgs/BoxieMotors` | **不能**，`ARM_ENABLE=0` |

`ARM_ENABLE=1` 会在**启动时**明确报错告诉你缺什么，而不是等按下按钮才发现
什么都没发生。

---

## 环境

只要 Flask ＋ Flask-SocketIO ＋ rclpy，**不要 torch，也不要 faster-whisper** ——
所以是第三个 venv（`venv/` 是检测和叠加的，`venv-asr/` 是转写的）：

```bash
python3 -m venv --system-site-packages ~/rog-pc/venv-tele
~/rog-pc/venv-tele/bin/pip install flask flask-socketio
```

`--system-site-packages` 是为了让 rclpy 从系统继承进来（这台是 24.04，
系统 gi / rclpy 就是 Python 3.12 的）。`run_tele.sh` 在 `UI_ROS_ENABLE=1` 时
会自动 source `ROS_SETUP`。

**vendor 化，不用 CDN。** `ovenplayer.js` 和 `socket.io.min.js` 都放在
`static/vendor/` 里 —— 现场没有互联网的话，走 CDN 会让整个画面开不出来。

---

## 跑

```bash
cd ~/rog-pc && ./run_tele.sh      # 前台，Ctrl-C 停
```

然后从 **tele-pc** 的浏览器打开 `http://rog-server.local:7779/`。

只想在 rog-server 本机试页面的话，把 `UI_BIND` 改成 `127.0.0.1`，
在那台机器上开 `http://localhost:7779/`（顺带手柄也能用 —— 那正是 ① 的出路之一）。

---

## 还没做

1. **操作指令通路要设计**（上面 ★）—— 消息类型、机体那一侧谁来收、
   头部指令和 VLM 的判断怎么合流。这是现在真正卡着的一条。
2. **实机没跑过。** `venv-tele` 还没建，页面没在 rog-server 上开过，
   从 tele-pc 打开有没有画面、手柄读不读得到、`rover/twist` 有没有真的到
   robot-pc，都没验过。
3. 页面上的右摇杆现在空着 —— 头部归 vlm-server 的 VLM 管（架构 §5），
   要不要留一个手动介入还没定。
