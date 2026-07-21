#!/usr/bin/env python3
"""
VLM server —— 部署在远程 3090PC 上, 常驻运行。

监听本地 TCP 端口(默认 127.0.0.1:50007), 接收 [prompt + JPEG 图像] 请求,
跑 VLM 推理, 回文字结果。模型只在启动时加载一次, 之后长期复用。

local 端通过 SSH 端口转发连到这个端口(见 ../local/vlm_client.py), 所以只监听
127.0.0.1 即可, 不对外网暴露。

后端
----
  --backend echo   不加载模型, 直接回一个假标签。用来先验证"部署 + 网络"链路。
  --backend qwen   加载 Qwen2.5-VL 跑真推理(需装 torch/transformers/qwen-vl-utils)。

协议 (与 ../local/vlm_client.py 必须一致)
----
  请求: [4B 总长][4B prompt_len][prompt utf-8][JPEG 图像字节]
  响应: [4B 长度][结果 utf-8 文本]
一个连接上可连续处理多帧(长连接), 直到 client 关闭。

用法
----
  # 先用 echo 验证链路
  python3 vlm_server.py --backend echo
  # 换真模型(默认 Qwen2.5-VL-32B-Instruct-AWQ 4bit, 24GB 显存, 加载 ~1-2 分钟)
  python3 vlm_server.py --backend qwen --max-pixels 602112
"""

import argparse
import io
import socket
import struct
import sys
import time

# 主力模型: 24GB(3090) 上用 32B 的 AWQ 4bit 量化权重(~20GB), 空间推理明显强于 7B。
# AWQ 权重已量化, 标准 from_pretrained 直接加载, 无需 bitsandbytes。
DEFAULT_MODEL = "Qwen/Qwen2.5-VL-32B-Instruct-AWQ"


# ---- 分帧协议 ----------------------------------------------------------------
def recvn(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def recv_msg(sock):
    hdr = recvn(sock, 4)
    if hdr is None:
        return None
    (n,) = struct.unpack(">I", hdr)
    return recvn(sock, n)


def send_msg(sock, data):
    sock.sendall(struct.pack(">I", len(data)) + data)


def parse_request(msg):
    """msg = [4B prompt_len][prompt][image] -> (prompt_str, image_bytes)"""
    (plen,) = struct.unpack(">I", msg[:4])
    prompt = msg[4:4 + plen].decode("utf-8", "replace")
    image = msg[4 + plen:]
    return prompt, image


# ---- 后端 --------------------------------------------------------------------
class EchoBackend:
    """不加载模型, 回一个假标签, 用来验证链路。"""
    name = "echo"

    def __init__(self, **_):
        from PIL import Image
        self._Image = Image

    def infer(self, prompt, image_bytes):
        im = self._Image.open(io.BytesIO(image_bytes))
        return "[echo] %dx%d, %dB image received -> Others" % (
            im.size[0], im.size[1], len(image_bytes))


class QwenBackend:
    """Qwen2.5-VL 真推理。依赖: torch, transformers, qwen-vl-utils, accelerate。"""
    name = "qwen"

    def __init__(self, model=DEFAULT_MODEL, max_new_tokens=64,
                 min_pixels=0, max_pixels=0, **_):
        import torch
        from PIL import Image
        from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
        self._Image = Image
        self._torch = torch
        self.max_new_tokens = max_new_tokens
        print("[qwen] loading %s ..." % model, flush=True)
        print("[qwen] 首次会下载权重(32B-AWQ ~20GB), 加载约 1-2 分钟", flush=True)
        t0 = time.perf_counter()
        self.model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            model, torch_dtype="auto", device_map="auto")
        # min/max_pixels 限制每张图的 visual token 数(pixels = tokens*28*28), 防 OOM 并加速
        proc_kwargs = {}
        if min_pixels:
            proc_kwargs["min_pixels"] = min_pixels
        if max_pixels:
            proc_kwargs["max_pixels"] = max_pixels
        self.processor = AutoProcessor.from_pretrained(model, **proc_kwargs)
        print("[qwen] loaded in %.1fs" % (time.perf_counter() - t0), flush=True)

    def infer(self, prompt, image_bytes):
        from qwen_vl_utils import process_vision_info
        im = self._Image.open(io.BytesIO(image_bytes)).convert("RGB")
        messages = [{"role": "user", "content": [
            {"type": "image", "image": im},
            {"type": "text", "text": prompt},
        ]}]
        text = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = self.processor(text=[text], images=image_inputs,
                                videos=video_inputs, padding=True,
                                return_tensors="pt").to(self.model.device)
        with self._torch.no_grad():
            gen = self.model.generate(**inputs, max_new_tokens=self.max_new_tokens)
        trimmed = [o[len(i):] for i, o in zip(inputs.input_ids, gen)]
        out = self.processor.batch_decode(
            trimmed, skip_special_tokens=True,
            clean_up_tokenization_spaces=False)[0]
        return out.strip()


def make_backend(args):
    if args.backend == "echo":
        return EchoBackend()
    if args.backend == "qwen":
        return QwenBackend(model=args.model, max_new_tokens=args.max_new_tokens,
                           min_pixels=args.min_pixels, max_pixels=args.max_pixels)
    raise SystemExit("unknown backend: %s" % args.backend)


# ---- 主循环 ------------------------------------------------------------------
def serve(args):
    backend = make_backend(args)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(1)
    print("[server] backend=%s listening on %s:%d" % (
        backend.name, args.host, args.port), flush=True)

    while True:
        conn, addr = srv.accept()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print("[server] client connected: %s" % (addr,), flush=True)
        try:
            while True:
                msg = recv_msg(conn)
                if msg is None:
                    break
                prompt, image = parse_request(msg)
                t0 = time.perf_counter()
                try:
                    result = backend.infer(prompt, image)
                except Exception as e:  # 单帧推理失败不拖垮 server
                    result = "[error] %s" % e
                dt = (time.perf_counter() - t0) * 1000.0
                print("[server] infer %.1fms -> %s" % (dt, result[:80]), flush=True)
                send_msg(conn, result.encode("utf-8"))
        finally:
            conn.close()
            print("[server] client disconnected", flush=True)


def main():
    ap = argparse.ArgumentParser(description="VLM server (远程 3090)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=50007)
    ap.add_argument("--backend", choices=["echo", "qwen"], default="echo")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-new-tokens", type=int, default=64)
    ap.add_argument("--min-pixels", type=int, default=0,
                    help="每图最小 visual token 像素数(0=不限)")
    ap.add_argument("--max-pixels", type=int, default=0,
                    help="每图最大 visual token 像素数, 如 602112(=768*784)(0=不限)")
    args = ap.parse_args()
    try:
        serve(args)
    except KeyboardInterrupt:
        print("\n[server] bye", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
