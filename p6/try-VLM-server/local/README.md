# local —— 运行在本机

VLM client: 把 word-wolf 音图压成 JPEG 发到远程 3090 的 VLM server, 收文字结果。
自动用嵌套 sshpass 双跳建立 SSH 端口转发, 无需手动开隧道。

## 前提

- wolf 环境(依赖 Pillow): `/home/chen/.virtualenvs/wolf/bin/python`
- 远程已启动 server(见 `../server/`)。

## 用法

```bash
# 批量发图库前 N 张
/home/chen/.virtualenvs/wolf/bin/python vlm_client.py --count 5

# 单张
/home/chen/.virtualenvs/wolf/bin/python vlm_client.py --image /path/to/soundmap.png
```

常用参数:
- `--images-dir` 图库目录(默认 word-wolf `trial-2/images`)
- `--quality 70` JPEG 质量(实测 q70 在保真与延迟间最优)
- `--ip 192.168.3.68` 远程 IP
- `--local-port 50017` / `--remote-port 50007` 端口转发两端

密码默认取脚本常量, 可用环境变量覆盖: `RIKEN_GRP_PASS` / `PC3090_CHEN_PASS`。

## 输出

每帧一行: 文件名 · 往返延迟(ms) · JPEG 大小 · server 返回的文字结果。
