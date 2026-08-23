# rog-pc（`rog-server.local` = 192.168.1.10）

rog-server 上跑的几个服务。**代码分两个文件夹，启动脚本集中在本层。**

```
~/rog-pc/
├── run_stream.sh      人物检测（顺带推 human_detect 监视流）
├── run_overlay.sh     声音图叠加（推 rgb_sm）
├── run_asr.sh         语音转文字（两路音频 → 文字，给 vlm-server 用）
├── run_tele.sh        tele-server（操作 UI）
├── stream-server/     OME ＋ 人物检测 ＋ ASR 的代码和配置
├── tele-server/       操作 UI 的代码
├── venv/              ★ 不进 git。gi ＋ rclpy ＋ torch（人物检测、叠加）
├── venv-asr/          ★ 不进 git。gi ＋ faster-whisper
├── venv-tele/         ★ 不进 git。flask ＋ rclpy（不要 torch / whisper）
├── weights/           ★ 不进 git。YOLO 权重
├── models/            ★ 不进 git。whisper 权重
└── log/               ★ 不进 git
```

**`venv/` 和 `venv-asr/` 必须分开**：ctranslate2 要 CUDA 12 的 cudnn，而 `venv/`
里的 torch 是 cu130，同一进程装不下两套 CUDA runtime。

## 各服务

| 起动 | 内容 | 状态 |
|---|---|---|
| `run_stream.sh` | 人物检测 → `record/trigger`（ROS）＋ `human_detect` 监视流 | 已实现，实机验证过 |
| `run_overlay.sh` | 鱼眼 ＋ 声音图叠加 → `rgb_sm` | 已实现，实机验证过 |
| `run_asr.sh` | 两路音频 → 文字，给 vlm-server 拉（架构 §5.2） | 已实现 |
| `run_tele.sh` | 操作 UI（只发网页，不碰媒体流） | **实机没跑过** |

OME 本身由 systemd 管（`ovenmediaengine.service`），不由这些脚本起。装它用
[stream-server/install_ome.sh](stream-server/install_ome.sh)，从源码编译，
一次 20–40 分钟；装完之后源码 tarball 和 `/opt/src/` 下的构建目录都可以删。

## 同步代码上去

```bash
rsync -a --exclude __pycache__ \
    stream-server tele-server run_stream.sh run_overlay.sh run_asr.sh run_tele.sh README.md \
    student@rog-server.local:~/rog-pc/
```

**只同步代码子目录，不要 `--delete` 整个 `~/rog-pc`** —— `venv*/`、`weights/`、
`models/` 都在同一层，会被一起删掉。
