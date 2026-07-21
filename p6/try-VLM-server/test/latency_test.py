#!/usr/bin/env python3
"""
图片传输网络延迟测试 (image transmission latency test)

目的
----
测量"本机 -> 远程 3090PC"实时传输一张 word-wolf 音图、并拿回一个小结果
(模拟 VLM 文字输出) 的端到端开销。这条路径就是未来 VLM-server 的真实网络路径:
    音图(上行) + 提示文字  ==>  远程处理  ==>  结果文字(下行, 小)

本脚本不跑真正的 VLM, 只在远程做一个"收到多少字节就回个 ok"的回声服务,
从而把纯网络开销单独测出来。

每种模式测三样开销:
    enc  : 本机把图压成该格式的耗时 (实时管线里每帧都要做)
    dec  : 把压缩字节解码回图像的耗时 (远程 VLM 端拿到后要做; 本机测值作参考,
           3090PC 更快)
    RTT  : 网络往返 (上行图 + 下行小结果)

对比模式:
    original   : 原图 PNG 原始字节 (768x768, ~450KB) —— 直接转发, 无编码
    jpeg-qNN   : 转 JPEG 各质量档 (通常几十 KB)
    64x64      : 缩到 64x64 重编码 PNG (~几 KB)
    text-only  : 不带图, 纯 RTT 下限参考

连接方式
--------
ProxyJump 两跳、两个不同密码 (网关 grp / 目标 chen), 单个 sshpass 只能喂一个密码。
所以用嵌套 sshpass: 外层喂 chen 密码给 3090PC, ProxyCommand 内层喂 grp 密码给
Riken 网关。全自动, 远程零安装。每次运行只建立一条 SSH 通道(认证一次), 往返复用它。

用法
----
    /home/chen/.virtualenvs/wolf/bin/python latency_test.py
    ... --count 30 --num-images 20 --jpeg 90,70,50

密码默认取自常量, 可用环境变量覆盖: RIKEN_GRP_PASS / PC3090_CHEN_PASS
"""

import argparse
import io
import os
import glob
import shlex
import struct
import subprocess
import sys
import time

from PIL import Image

# ---- 默认配置 ----------------------------------------------------------------
DEFAULT_IP = "192.168.3.68"
RIKEN_ALIAS = "Riken"
GRP_PASS = os.environ.get("RIKEN_GRP_PASS", "make rob")
CHEN_PASS = os.environ.get("PC3090_CHEN_PASS", "1")
DEFAULT_IMAGES = "/home/chen/Documents/Playground/p6/try-VLM/trial-2/images"

REMOTE_CODE = r"""
import sys, struct
r = sys.stdin.buffer.read
w = sys.stdout.buffer.write
def readn(n):
    b = b''
    while len(b) < n:
        c = r(n - len(b))
        if not c:
            return None
        b += c
    return b
while True:
    h = readn(4)
    if h is None:
        break
    (n,) = struct.unpack('>I', h)
    if n == 0:
        break
    d = readn(n)
    if d is None:
        break
    resp = ('ok:%d' % len(d)).encode()
    w(struct.pack('>I', len(resp)))
    w(resp)
    sys.stdout.buffer.flush()
"""

PROMPT = (b"You are a Word Wolf facilitator. Look at this sound map and decide "
          b"who should speak next. Answer with one label.\x00")


# ---- SSH ---------------------------------------------------------------------
def build_ssh_cmd(ip):
    """嵌套 sshpass 双跳: 外层 chen 密码 -> 3090PC, 内层 grp 密码 -> Riken 网关。
    直接以 IP 为目标(不用别名), 避免 config 的 ProxyJump 与自定义 ProxyCommand 冲突。"""
    proxy = ("ProxyCommand=sshpass -p " + shlex.quote(GRP_PASS) +
             " ssh -W %h:%p"
             " -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
             + RIKEN_ALIAS)
    remote_cmd = "python3 -u -c " + shlex.quote(REMOTE_CODE)
    return [
        "sshpass", "-p", CHEN_PASS, "ssh", "-T",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "User=chen",
        "-o", proxy,
        ip,
        remote_cmd,
    ]


def readn(f, n):
    b = b""
    while len(b) < n:
        c = f.read(n - len(b))
        if not c:
            return None
        b += c
    return b


def round_trip(proc, payload):
    t0 = time.perf_counter()
    proc.stdin.write(struct.pack(">I", len(payload)))
    proc.stdin.write(payload)
    proc.stdin.flush()
    hdr = readn(proc.stdout, 4)
    if hdr is None:
        raise RuntimeError("远程连接中断(no header) —— 检查密码/网络/远程 python3")
    (n,) = struct.unpack(">I", hdr)
    if readn(proc.stdout, n) is None:
        raise RuntimeError("远程连接中断(no body)")
    return time.perf_counter() - t0


def pctl(sv, p):
    if not sv:
        return 0.0
    k = (len(sv) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sv) - 1)
    return sv[lo] + (sv[hi] - sv[lo]) * (k - lo)


def _human(n):
    if n >= 1024 * 1024:
        return "%.1fMB" % (n / (1024 * 1024))
    if n >= 1024:
        return "%.0fKB" % (n / 1024)
    return "%dB" % n


def _time(fn):
    t0 = time.perf_counter()
    r = fn()
    return (time.perf_counter() - t0) * 1000.0, r


# ---- 编码 / 解码 -------------------------------------------------------------
def encode_original(path, pil):
    # "原格式": 直接用磁盘上的原 PNG 字节, 无压缩开销
    return open(path, "rb").read()


def make_jpeg_encoder(q):
    def enc(path, pil):
        buf = io.BytesIO()
        pil.save(buf, "JPEG", quality=q)
        return buf.getvalue()
    return enc


def make_downscale_encoder(side):
    def enc(path, pil):
        buf = io.BytesIO()
        pil.resize((side, side), Image.BILINEAR).save(buf, "PNG")
        return buf.getvalue()
    return enc


def prepare(encoder, paths, pil_images, do_encode_timing):
    """返回 (payloads, avg_payload_bytes, avg_enc_ms, avg_dec_ms)。
    payloads 已含 PROMPT 前缀; enc/dec 各图取 3 次最小值再平均。"""
    payloads, sizes, enc_ms, dec_ms = [], [], [], []
    for path, pil in zip(paths, pil_images):
        if do_encode_timing:
            best = min((_time(lambda: encoder(path, pil)) for _ in range(3)),
                       key=lambda t: t[0])
            enc_ms.append(best[0])
            raw = best[1]
        else:
            raw = encoder(path, pil)          # 原格式模式: 无编码开销, 不计时
        sizes.append(len(raw))
        best = min((_time(lambda: Image.open(io.BytesIO(raw)).load()) for _ in range(3)),
                   key=lambda t: t[0])
        dec_ms.append(best[0])
        payloads.append(PROMPT + raw)
    avg = lambda x: sum(x) / len(x) if x else 0.0
    return payloads, avg(sizes), avg(enc_ms), avg(dec_ms)


# ---- 测量与输出 --------------------------------------------------------------
HDR = "%-11s %9s %4s %7s %7s %8s %8s %8s %8s %9s" % (
    "mode", "payload", "n", "enc", "dec", "min", "mean", "p95", "max", "MB/s")


def measure_and_report(proc, label, payloads, avg_payload, enc_ms, dec_ms, count):
    rtts = sorted(round_trip(proc, payloads[i % len(payloads)]) for i in range(count))
    mean = sum(rtts) / len(rtts)
    mbps = (avg_payload / (1024 * 1024)) / mean if mean > 0 else 0.0
    print("%-11s %9s %4d %6.1fm %6.1fm %8.1f %8.1f %8.1f %8.1f %9.1f" % (
        label, _human(avg_payload), len(rtts), enc_ms, dec_ms,
        rtts[0] * 1000, mean * 1000, pctl(rtts, 0.95) * 1000, rtts[-1] * 1000, mbps))


def main():
    ap = argparse.ArgumentParser(description="word-wolf 音图传输延迟测试")
    ap.add_argument("--ip", default=DEFAULT_IP)
    ap.add_argument("--images-dir", default=DEFAULT_IMAGES)
    ap.add_argument("--count", type=int, default=30, help="每种模式发送张数(默认 30)")
    ap.add_argument("--num-images", type=int, default=20, help="取前 N 张循环使用(默认 20)")
    ap.add_argument("--jpeg", default="90,70,50", help="JPEG 质量档, 逗号分隔(默认 90,70,50)")
    ap.add_argument("--downscale", type=int, default=64, help="降采样边长(默认 64)")
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(args.images_dir, "*.png")))[:args.num_images]
    if not paths:
        print("!! 图库为空: %s" % args.images_dir, file=sys.stderr)
        return 1
    # 预加载并解码到内存(模拟实时管线里"已在内存的一帧"), 编码计时从这里开始才公平
    pil_images = [Image.open(p).convert("RGB") for p in paths]
    for im in pil_images:
        im.load()
    print("准备 %d 张图: %s\n" % (len(paths), args.images_dir))

    specs = [("original", encode_original, False)]
    for q in [int(x) for x in args.jpeg.split(",") if x.strip()]:
        specs.append(("jpeg-q%d" % q, make_jpeg_encoder(q), True))
    specs.append(("%dx%d" % (args.downscale, args.downscale),
                  make_downscale_encoder(args.downscale), True))

    prepared = []
    for name, enc, timing in specs:
        prepared.append((name,) + prepare(enc, paths, pil_images, timing))

    cmd = build_ssh_cmd(args.ip)
    print("连接 3090PC (%s) via Riken, 启动远程回声服务...\n" % args.ip)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        round_trip(proc, b"warmup")   # 触发认证/握手, 不计入
        print(HDR)
        print("-" * len(HDR))

        base = sorted(round_trip(proc, PROMPT) for _ in range(args.count))
        bmean = sum(base) / len(base)
        print("%-11s %9s %4d %7s %7s %8.1f %8.1f %8.1f %8.1f %9s" % (
            "text-only", _human(len(PROMPT)), len(base), "-", "-",
            base[0] * 1000, bmean * 1000, pctl(base, 0.95) * 1000, base[-1] * 1000, "-"))

        for name, payloads, avg_payload, enc_ms, dec_ms in prepared:
            measure_and_report(proc, name, payloads, avg_payload, enc_ms, dec_ms, args.count)

        print("\nenc=本机压缩耗时/张  dec=解码回图耗时/张(远程 VLM 端量级, 3090 更快)")
        print("min/mean/p95/max=网络往返毫秒  MB/s=有效上行吞吐")
        print("单帧总延迟 ≈ enc + 网络RTT + (远程 dec + VLM 推理)")
    finally:
        try:
            proc.stdin.write(struct.pack(">I", 0))
            proc.stdin.flush()
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
