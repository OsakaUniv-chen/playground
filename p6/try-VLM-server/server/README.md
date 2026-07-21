# server —— 部署在远程 3090PC

常驻 VLM server: 监听 `127.0.0.1:50007`, 收 [prompt + JPEG] → 推理 → 回文字。
模型只在启动时加载一次。local 端经 SSH 端口转发连过来(见 `../local/`)。

## 依赖

```bash
pip install -r requirements.txt   # echo 后端只需 Pillow
# qwen 后端另需: torch(按 CUDA 版本) transformers>=4.49 accelerate qwen-vl-utils autoawq
```

## 启动

```bash
# 先验证链路(不加载模型)
python3 vlm_server.py --backend echo

# 真模型(默认 Qwen2.5-VL-32B-Instruct-AWQ 4bit, 24GB 显存, 加载 ~1-2 分钟)
python3 vlm_server.py --backend qwen --max-pixels 602112

# 想省显存/快速迭代可换 7B bf16
python3 vlm_server.py --backend qwen --model Qwen/Qwen2.5-VL-7B-Instruct
```

参数: `--port 50007`  `--max-new-tokens 64`  `--min-pixels/--max-pixels`(限制每图
visual token, pixels=tokens*28*28; 768×768 音图建议 `--max-pixels 602112` 左右防 OOM)

## 协议 (与 ../local/vlm_client.py 必须一致)

```
请求: [4B 总长][4B prompt_len][prompt utf-8][JPEG 图像字节]
响应: [4B 长度][结果 utf-8 文本]
```
一个连接可连续处理多帧(长连接), 直到 client 断开。

## 说明

- 只监听 127.0.0.1, 不对外网暴露; 访问一律经 local 端的 SSH 隧道。
- 单帧推理异常不会拖垮 server(捕获后回 `[error] ...`)。
- 当前为单连接串行处理(实验场景足够); 若需并发再加线程。
