#!/usr/bin/env python3
"""分帧協議 + 画像エンコード(../server/vlm_server.py と必ず一致)。

  請求: [4B 総長][4B prompt_len][prompt utf-8][JPEG 画像バイト]
  応答: [4B 長さ][結果 utf-8 文本]

一つの接続で連続多帧(長接続)を扱える。JPEG q70 が保真と延迟の最適(test/ 参照)。
"""
import io
import socket
import struct

from PIL import Image


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


def encode_jpeg(path, quality=70):
    im = Image.open(path).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def connect(port):
    """本機の転送ポートに接続(TCP_NODELAY)。"""
    sock = socket.create_connection(("127.0.0.1", port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock


def human_bytes(n):
    return "%.0fKB" % (n / 1024) if n >= 1024 else "%dB" % n
