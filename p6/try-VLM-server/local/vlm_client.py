#!/usr/bin/env python3
"""
VLM client —— 运行在本机, 把 word-wolf 音图发到远程 3090PC 的 VLM server, 收文字结果。

流程
----
  1. 用嵌套 sshpass 双跳建立 SSH 端口转发(本机 LOCAL_PORT -> 3090 的 server 端口)。
     ProxyJump 两跳两个不同密码, 所以外层 sshpass 喂 chen 密码给 3090PC,
     ProxyCommand 内层喂 grp 密码给 Riken 网关。
  2. 连本机转发端口, 把 [prompt + JPEG] 发过去(默认 JPEG q70, 实测在保真与延迟间最优)。
  3. 收回文字结果, 打印每帧延迟。

前提: 远程已启动 server (见 ../server/vlm_server.py), 例如:
      python3 vlm_server.py --backend echo

协议 (与 ../server/vlm_server.py 必须一致)
----
  请求: [4B 总长][4B prompt_len][prompt utf-8][JPEG 图像字节]
  响应: [4B 长度][结果 utf-8 文本]

用法
----
  /home/chen/.virtualenvs/wolf/bin/python vlm_client.py --image <一张图>
  /home/chen/.virtualenvs/wolf/bin/python vlm_client.py --images-dir <目录> --count 10
"""

import argparse
import glob
import io
import os
import shlex
import socket
import struct
import subprocess
import sys
import time

from PIL import Image

# ---- 默认配置 (与 test/latency_test.py 一致) --------------------------------
DEFAULT_IP = "192.168.3.68"
RIKEN_ALIAS = "Riken"
GRP_PASS = os.environ.get("RIKEN_GRP_PASS", "make rob")
CHEN_PASS = os.environ.get("PC3090_CHEN_PASS", "1")
DEFAULT_IMAGES = "/home/chen/Documents/Playground/p6/try-VLM/trial-2/images"
DEFAULT_PROMPT = ("You are a Word Wolf facilitator. Look at this sound map and "
                  "decide who should speak next. Answer with one label.")


# ---- SSH 端口转发隧道 --------------------------------------------------------
def start_tunnel(ip, local_port, remote_port):
    """嵌套 sshpass 双跳建立 -L 端口转发, 返回后台 ssh 进程。"""
    proxy = ("ProxyCommand=sshpass -p " + shlex.quote(GRP_PASS) +
             " ssh -W %h:%p"
             " -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
             + RIKEN_ALIAS)
    cmd = [
        "sshpass", "-p", CHEN_PASS, "ssh", "-N", "-T",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "User=chen",
        "-o", proxy,
        "-L", "%d:127.0.0.1:%d" % (local_port, remote_port),
        ip,
    ]
    return subprocess.Popen(cmd)


def wait_port(port, timeout=25.0):
    """轮询直到本机 port 可连(隧道就绪)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1.0)
            s.close()
            return True
        except OSError:
            time.sleep(0.3)
    return False


# ---- 分帧协议 ----------------------------------------------------------------
def recvn(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def send_request(sock, prompt, image_bytes):
    p = prompt.encode("utf-8")
    payload = struct.pack(">I", len(p)) + p + image_bytes
    sock.sendall(struct.pack(">I", len(payload)) + payload)


def recv_response(sock):
    hdr = recvn(sock, 4)
    if hdr is None:
        return None
    (n,) = struct.unpack(">I", hdr)
    body = recvn(sock, n)
    return body.decode("utf-8", "replace") if body is not None else None


# ---- 图像编码 ----------------------------------------------------------------
def encode_jpeg(path, quality):
    im = Image.open(path).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _human(n):
    if n >= 1024:
        return "%.0fKB" % (n / 1024)
    return "%dB" % n


def main():
    ap = argparse.ArgumentParser(description="VLM client (本机)")
    ap.add_argument("--image", help="单张图片路径")
    ap.add_argument("--images-dir", default=DEFAULT_IMAGES, help="图库目录(批量)")
    ap.add_argument("--count", type=int, default=5, help="批量发送张数(默认 5)")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--quality", type=int, default=70, help="JPEG 质量(默认 70)")
    ap.add_argument("--ip", default=DEFAULT_IP)
    ap.add_argument("--local-port", type=int, default=50017)
    ap.add_argument("--remote-port", type=int, default=50007)
    args = ap.parse_args()

    if args.image:
        paths = [args.image]
    else:
        paths = sorted(glob.glob(os.path.join(args.images_dir, "*.png")))[:args.count]
    if not paths:
        print("!! 没有图片可发", file=sys.stderr)
        return 1

    print("建立 SSH 端口转发 %d -> 3090:%d (via Riken) ..." % (
        args.local_port, args.remote_port))
    tunnel = start_tunnel(args.ip, args.local_port, args.remote_port)
    try:
        if not wait_port(args.local_port):
            print("!! 隧道未就绪(超时)。确认远程 server 已启动、密码/网络正常。",
                  file=sys.stderr)
            return 1

        sock = socket.create_connection(("127.0.0.1", args.local_port))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print("已连接 server, 开始发送(JPEG q%d)\n" % args.quality)

        for path in paths:
            img = encode_jpeg(path, args.quality)
            t0 = time.perf_counter()
            send_request(sock, args.prompt, img)
            result = recv_response(sock)
            dt = (time.perf_counter() - t0) * 1000.0
            if result is None:
                print("!! server 关闭了连接", file=sys.stderr)
                break
            print("%-16s %6.0fms  %5s  -> %s" % (
                os.path.basename(path), dt, _human(len(img)), result))
        sock.close()
    finally:
        tunnel.terminate()
        try:
            tunnel.wait(timeout=5)
        except Exception:
            tunnel.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
