# try-VLM-server

把 VLM 放到远程 3090PC 上跑, 本机(local)实时把 word-wolf 音图发过去、拿回文字决策。
验证网络可行性 + 提供可用的 client/server 骨架。

## 结构

```
try-VLM-server/
├── test/     网络延迟测试(已完成): latency_test.py —— 测原图 vs JPEG vs 64x64 的
│             传输延迟和编解码耗时, 结论见下
├── server/   部署在远程 3090PC: vlm_server.py (echo / qwen 后端) + requirements.txt
└── local/    运行在本机: vlm_client.py —— 建 SSH 隧道, 发图收结果
```

两台机器都会部署整个 p6 文件夹; 在本机写代码, 同步到 server。
`local/` 与 `server/` 各自独立、内联相同的分帧协议, 互不依赖。

## 网络方案

3090PC 在 Riken 内网, 需经网关 `Riken` 两跳 ProxyJump, 且两跳密码不同
(网关 grp / 目标 chen)。单个 sshpass 只能喂一个密码, 所以用**嵌套 sshpass**:
外层喂 chen 密码给 3090PC, `ProxyCommand` 内层喂 grp 密码给网关。全自动、远程零安装。

- server 只监听 `127.0.0.1:50007`(不对外), local 通过 SSH `-L` 端口转发连过去。
- 密码默认取脚本常量, 可用环境变量覆盖: `RIKEN_GRP_PASS` / `PC3090_CHEN_PASS`。

## 端到端跑法

```bash
# 1) 远程(3090)启动 server —— 先用 echo 验证链路
python3 server/vlm_server.py --backend echo
#    换真模型(默认 Qwen2.5-VL-32B-Instruct-AWQ 4bit, ~20GB, 加载 1-2 分钟):
#    python3 server/vlm_server.py --backend qwen --max-pixels 602112

# 2) 本机运行 client(自动建隧道)
/home/chen/.virtualenvs/wolf/bin/python local/vlm_client.py --count 5
```

## 延迟测试结论 (test/latency_test.py, 真实 word-wolf 音图)

| 模式 | 上行 | 压缩 | 解压 | 网络 RTT(mean) |
|------|------|------|------|------|
| 原图 PNG | 474KB | 0 | 9ms | 166ms |
| **JPEG q70** | 38KB | ~1ms | ~1ms | **67ms** |
| JPEG q50 | 29KB | ~1ms | ~1ms | 49ms |
| 64×64 | 6KB | ~1ms | ~0ms | 29ms |

- **压缩/解压几乎免费**(~1ms), 但把网络延迟砍掉一半以上。
- **默认用 JPEG q70**: 保真与延迟最优, 单帧网络往返 ~67ms。
- 瓶颈只剩 VLM 推理本身, 网络不再是问题。
