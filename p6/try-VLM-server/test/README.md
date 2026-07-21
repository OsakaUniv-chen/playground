# try-VLM-server / test

测试"本机 ⇄ 远程 3090PC"实时传图片的网络延迟，为把 VLM 放到 remote server 上做可行性验证。

真实路径：**音图(上行, 大) + 提示文字 → 远程处理 → 结果文字(下行, 小)**。
`latency_test.py` 只测这条链路的纯网络开销（远程跑一个回声服务，不跑真 VLM）。

## 数据

用 word-wolf 实验的真实音图：`p6/try-VLM/trial-2/images/`（160 张 768×768 RGBA PNG，~450KB/张）。
对比两种上行大小：

- **original** —— 原图 PNG 原始字节（~450KB）
- **64×64** —— 缩放并重编码 PNG（~几 KB）
- **text-only** —— 不带图，作为纯 RTT 下限参考

## 连接

ProxyJump 两跳、两个不同密码（网关 grp / 目标 chen），单个 sshpass 只能喂一个密码。
所以用**嵌套 sshpass**：外层喂 chen 密码给 3090PC，`ProxyCommand` 内层喂 grp 密码给 Riken 网关。
全自动，无需手动认证，也无需在远程装任何东西。每次运行只建立一条 SSH 通道（认证一次），
所有往返复用它。目标 IP 默认 `192.168.3.68`。

## 用法

需要 wolf 环境（依赖 Pillow）：

```bash
/home/chen/.virtualenvs/wolf/bin/python latency_test.py

# 可选
/home/chen/.virtualenvs/wolf/bin/python latency_test.py --count 50 --num-images 30
```

密码默认取自脚本常量，可用环境变量覆盖：`RIKEN_GRP_PASS`、`PC3090_CHEN_PASS`。

输出每种模式的 RTT（min/mean/p50/p95/max, 毫秒）和有效上行吞吐(MB/s)。
