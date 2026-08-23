# rog-pc（`rog-server.local` = 192.168.1.10）

rog-server 这台机器上跑的几个服务。**代码分两个文件夹，启动脚本集中在本层。**

```
~/rog-pc/
├── run_stream.sh      起人物检测节点（顺带推 detect 监视流）
├── run_overlay.sh     起声音图叠加（推 rgb_sm 监视流）
├── run_asr.sh         起语音转文字（两路音频 → 文字，给 vlm-server 用）
├── run_tele.sh        起 tele-server（操作 UI）
├── stream-server/     OME ＋ 人物检测 ＋ ASR 的代码和配置
├── tele-server/       操作 UI 的代码（尚未实现）
├── venv/              ★ 不进 git。gi ＋ rclpy ＋ torch（人物检测、叠加）
├── venv-asr/          ★ 不进 git。gi ＋ faster-whisper（**和上面分开，见下**）
├── venv-tele/         ★ 不进 git。flask ＋ rclpy（不要 torch / whisper）
├── weights/           ★ 不进 git。YOLO 权重
├── models/            ★ 不进 git。whisper 权重
└── log/               ★ 不进 git
```

**两个 venv 是有理由的，不是懒得合并。** ctranslate2（faster-whisper 的后端）要
CUDA 12 的 cudnn，而 `venv/` 里的 torch 是 cu130 —— 同一个进程里两套 CUDA
runtime 是已知的麻烦源。分成两个 venv、两个进程之后，它们只共享 GPU，
不共享进程空间，这个问题就不存在了。

**为什么启动脚本不放在各自文件夹里。** 这些服务同机、同时起、共用一台机器的
资源，登上来的人要的是「这台机器上的东西怎么起」，不是「先 cd 进哪个子目录」。
子文件夹里只放代码和该服务自己的 `config.env`。

**★ 不是开发机那台 `ROG`（192.168.1.100）。** 见
[../system-architecture.md](../system-architecture.md) §0 —— 两台都是 ASUS 的
ROG 笔电，开发机的 hostname 恰好也叫 `ROG`，且同样跑着 OME。

---

## 同步代码上去

```bash
rsync -a --exclude __pycache__ \
    stream-server tele-server run_stream.sh run_overlay.sh run_asr.sh run_tele.sh README.md \
    student@rog-server.local:~/rog-pc/
```

**只同步代码子目录，不要 `--delete` 整个 `~/rog-pc`** —— `venv/`、`venv-asr/`、
`venv-tele/`、`weights/`、`models/` 都在同一层，会被一起删掉（venv 重建要装
torch，权重和 whisper 模型要重下）。

---

## 各服务

| 起动 | 内容 | 状态 |
|---|---|---|
| `run_stream.sh` | 人物检测 → `record/trigger`（ROS）＋ `detect` 监视流 | 已实现，实机验证过 |
| `run_overlay.sh` | 鱼眼 ＋ 声音图叠加 → `rgb_sm` 监视流 | 已实现，实机验证过 |
| `run_asr.sh` | 两路音频 → 文字。**给 vlm-server 用**（架构 §5.2） | 已实现 |
| `run_tele.sh` | 操作 UI（只发网页，不碰媒体流） | 代码搬过来了，**实机没跑过**；手柄还用不了（见那边 README 的 ★★①） |

代码都在 [stream-server/](stream-server/)（除了 UI 在 [tele-server/](tele-server/)）。

OME 本身由 systemd 管（`ovenmediaengine.service`），不由这里的脚本起。
装它的脚本是 [stream-server/install_ome.sh](stream-server/install_ome.sh)，
从源码编译，一次 20-40 分钟；源码 tarball 找不到时会自己重下，所以**装完之后
那个 tarball 和 `/opt/src/` 下的构建目录都可以删**。
