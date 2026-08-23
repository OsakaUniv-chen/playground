# tele-server（操作 UI 服务）

只做一件事：把操作页面发出去。**媒体流不经过这里** —— 页面里的 OvenPlayer 直接
向 `rog-server.local` 的 OME 建 WebRTC 连接。部署在 rog-server 上，和 OME 同机。

设计见 [../../system-architecture.md](../../system-architecture.md) §4。

```
config.env        所有配置（**唯一出处**）
app.py            Flask ＋ Socket.IO：发页面、把浏览器的指令转成 ROS
templates/        页面（一个 base.html）
static/js/        gamepad.js（手柄 → 指令）、websocket.js（Socket.IO 收发）
static/vendor/    ovenplayer.js、socket.io.min.js
static/css/       样式
../run_tele.sh    起动（在 `rog-pc/` 那一层，和 run_stream.sh 并排）
```

## 页面取哪些流

| 用途 | stream key | 说明 |
|---|---|---|
| 画面 | `rgb_sm` | stream-server 已经把鱼眼和声音图叠好了，**纯视频没有音轨** |
| 声音 | `onboardmic` | robot-pc 直推的纯音频流，页面上是个隐藏的播放器 |

**声音单独走一条是有意的。** `rgb_sm` 要多绕一圈「解码→叠加→重编→回推」，
声音跟着它走就白白慢几百毫秒，而这是对话链路。**代价是口型对不上。**

## 环境

只要 Flask ＋ Flask-SocketIO ＋ rclpy，**不要 torch 也不要 faster-whisper**，
所以是第三个 venv：

```bash
python3 -m venv --system-site-packages ~/rog-pc/venv-tele
~/rog-pc/venv-tele/bin/pip install flask flask-socketio
```

`--system-site-packages` 让 rclpy 从系统继承进来（这台是 24.04）。
`run_tele.sh` 在 `UI_ROS_ENABLE=1` 时会自动 source `ROS_SETUP`。

**vendor 化，不用 CDN** —— 现场没有互联网时走 CDN 会让整个画面开不出来。

## 跑

```bash
cd ~/rog-pc && ./run_tele.sh      # 前台，Ctrl-C 停
```

从 tele-pc 的浏览器打开 `http://rog-server.local:7779/`。

## 状态

**手柄能用。** Chrome 实测，非 secure context 也读得到（Firefox 没测）。

### 还没做

1. **操作指令通路。** `UI_ROS_ENABLE=1` 之后 UI 会发 ROS 指令，但机体那一侧
   还没接。`rover/twist` 用 `geometry_msgs/Twist`（类型到处都有，能发）；
   手臂原本用的 `audio_common_msgs/BoxieMotors` **新结构里没有这个类型**，
   所以 `ARM_ENABLE=0`，置 1 会在启动时明确报错而不是按下按钮才发现没反应。
   机体侧仅存的参考是 [../../robot-pc/start_boxie.sh](../../robot-pc/start_boxie.sh)
   （旧实现原样搬来，读 `~/ros2_ws`，和现在的 config 对不上）。
2. **实机没跑过。** `venv-tele` 还没建，页面没在 rog-server 上开过。
3. 右摇杆空着 —— 头部归 vlm-server 的 VLM 管（架构 §5），要不要留手动介入没定。
