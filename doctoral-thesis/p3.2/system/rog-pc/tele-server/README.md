# tele-server（操作 UI 服务）

只做一件事：把操作页面发出去。**媒体流不经过这里** —— 页面里的 OvenPlayer 直接
向 `rog-server.local` 建 WebRTC 连接。

部署在 rog-server（**`rog-server.local`** = 192.168.1.10）上，和 OME 同机（代码分开放，职责不同）。
**不是开发机那台 `ROG`（192.168.1.100）** —— 见
[../../system-architecture.md](../../system-architecture.md) §0。

设计见 [../../system-architecture.md](../../system-architecture.md) §4。

代码放在 `rog-pc/tele-server/`，**启动脚本放在上一层**（`~/rog-pc/run_tele.sh`），
和 stream-server 的 `run_stream.sh` 并排 —— 两个服务同机，启动入口集中在一处。

尚未开始实现。要做的三件事：

1. **发页面。** Flask ＋ 静态文件，页面里的 OvenPlayer 拉 `fisheye` / `soundmap` /
   `realsense` / `navcam`（要看监视流再加 `rgb_sm` / `detect`，地址表在
   [../stream-server/README.md](../stream-server/README.md)）。声音图用 screen
   混合叠在鱼眼上，强度给个滑块（§4.3）。
2. **`run_tele.sh`**，放在上一层 `~/rog-pc/`，和 `run_stream.sh` 并排。
3. **`config.env`**（`UI_PORT` 等），和别处一样是配置的唯一出处。

**★ 操作指令的通路还没有设计。** 架构文档开头就把「电机驱动、操作指令这些 ROS
节点」划到了范围外，所以从游戏手柄到机体的 `rover` / 头部电机这一段，
**现在整个系统里没有任何一处写着它怎么走**（旧实现是操作者端 Flask ＋
socket.io ＋ ROS 中继，在 `archive/` 里）。真要动手写 UI 之前得先把这段定下来。
