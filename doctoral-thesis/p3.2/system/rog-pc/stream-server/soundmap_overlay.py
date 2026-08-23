#!/usr/bin/env python3
"""鱼眼 ＋ 1-bit 声音图的叠加图，推回 OME（stream key `rgb_sm`）。

    OME ──WebRTC──▶ fisheye  1080×1080@30 ┐
                                          ├─ 叠加 ─▶ SRT ─▶ OME(app/rgb_sm)
    OME ──WebRTC──▶ soundmap 64×64@15     ┘

**只是给人看的监视流**，不进 bag、不参与任何判断。要做定量分析请用 bag 里的
`soundmap/map`（float32 原始值），不要用这条流（H.264 有损、还叠了底图）。

叠加的式子和 QC 视频（`soundmap-generator/soundmap-video/bag2video.py`）一致：

    sm_color = 黄色化(声音图 INTER_LINEAR 放大到 1080)
    blend    = addWeighted(sm_color, 0.6, cam, 0.8, 0)

**鱼眼必须是正方形的 1080×1080。** 声音图的「像素 → 方向」对应关系是按这个
正方形推的（robot-pc 的 soundmap.py 里 pixel_size=1080），把横向压扁去凑尺寸
会让斑点的位置整体错开。所以这里是**裁中央**，不是缩放。

**★ 声音图断了就回到素画面。** 贴着几秒前的旧斑点比不贴更容易误导 ——
看的人没法从画面上分辨「现在没声音」和「声音图这条流挂了」。

**★ 鱼眼和声音图之间没有做时间戳对齐**，拿到哪帧就叠哪帧（架构 §8）。

用法:
    ../run_overlay.sh
    python3 soundmap_overlay.py --fps 30        # 跟满鱼眼的节奏
"""
from __future__ import annotations

import os

os.environ.setdefault("GIO_USE_PROXY_RESOLVER", "dummy")   # 理由见 person_detect.py

import argparse  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

# =====================================================================
# 配置
# =====================================================================

OUT_SIZE = 1080          # 输出边长。见文件头：必须是鱼眼的那个正方形
OUT_FPS = 15             # 声音图本来就是 15 Hz，监视流没必要跟满 30
ALPHA = 0.6              # 声音图的权重 ┐ 和 QC 视频一致，改了就对不上
BETA = 0.8               # 画面的权重   ┘
SM_TIMEOUT = 2.0         # 声音图超过这么久没来 → 回到素画面
REPORT_SEC = 10.0

# =====================================================================


def log(level, msg):
    print(f"[{level}] {msg}", flush=True)


def env(key):
    try:
        return os.environ[key]
    except KeyError:
        raise SystemExit(
            f"[error] {key} 未设置 —— 没读到 config.env。用 ../run_overlay.sh 启动。"
        ) from None


def _to_bgr(data, sample):
    """appsink 的一枚 → (h, w, 3) uint8 BGR。按 stride 还原再切掉 padding。"""
    st = sample.get_caps().get_structure(0)
    w, h = st.get_value("width"), st.get_value("height")
    if not h:
        return None
    a = np.frombuffer(data, dtype=np.uint8)
    stride = a.size // h
    if stride * h != a.size:
        return None
    return a.reshape(h, stride)[:, : w * 3].reshape(h, w, 3)


class Overlay:
    def __init__(self, out_size=OUT_SIZE, fps=OUT_FPS):
        self.size = out_size
        self.fps = fps
        self._period = 1.0 / fps
        self._lock = threading.Lock()
        self._sm_color = None          # 放大并黄色化之后的声音图
        self._sm_t = 0.0
        self._sm_stale = True
        self._last_push = 0.0
        self.out = None
        self.n_cam = self.n_sm = self.n_out = 0
        self.blend_ms = 0.0

    # ---- 声音图（15 Hz）：放大和上色在这里只做一次 ----

    def on_soundmap(self, data, sample):
        img = _to_bgr(data, sample)
        if img is None:
            return
        self.n_sm += 1
        # robot-pc 那边出的就是 BGR=[0, v, v] 的「黑底黄斑」（soundmap.py），
        # 所以只取 G 通道。4:2:0 的色差间引会让 B 有一点渗，这样取能让黑底
        # 保持是黑的。
        v = img[:, :, 1]
        big = cv2.resize(v, (self.size, self.size),
                         interpolation=cv2.INTER_LINEAR)      # 和 QC 视频一致
        color = np.stack([np.zeros_like(big), big, big], axis=-1)
        with self._lock:
            self._sm_color = color
            self._sm_t = time.monotonic()
            if self._sm_stale:
                self._sm_stale = False
                log("info", "声音图 接收中")

    # ---- 鱼眼：裁成正方形、叠加、推出去 ----

    def _fit(self, frame):
        h, w = frame.shape[:2]
        if h != w:
            # **裁中央，不是缩放。** 见文件头
            side = min(h, w)
            y0, x0 = (h - side) // 2, (w - side) // 2
            frame = frame[y0:y0 + side, x0:x0 + side]
        if frame.shape[0] != self.size:
            frame = cv2.resize(frame, (self.size, self.size),
                               interpolation=cv2.INTER_AREA)
        return frame

    def on_fisheye(self, data, sample):
        # 限流：鱼眼是 30 fps，输出只要 OUT_FPS。**丢帧要在解码之后、叠加之前**，
        # 这样省掉的是 resize + addWeighted + 编码，那才是花钱的地方。
        now = time.monotonic()
        if now - self._last_push < self._period * 0.95:
            return
        img = _to_bgr(data, sample)
        if img is None:
            return
        self.n_cam += 1
        self._last_push = now

        t0 = time.monotonic()
        cam = self._fit(img)
        with self._lock:
            sm = self._sm_color
            age = now - self._sm_t
            if sm is not None and age > SM_TIMEOUT:
                if not self._sm_stale:
                    self._sm_stale = True
                    log("warn", f"声音图 {age:.1f} 秒没来 —— 回到素画面")
                sm = None
        out = cam if sm is None else cv2.addWeighted(sm, ALPHA, cam, BETA, 0)
        self.blend_ms = (time.monotonic() - t0) * 1e3

        if self.out is None:
            from srt_out import from_env
            self.out = from_env("STREAM_KEY_RGB_SM", self.size, self.size,
                                self.fps, "RGB_SM_BITRATE", 4000, logger=log)
            self.out.start()
        self.out.push(out)
        self.n_out += 1

    def report(self):
        log("info", f"鱼眼 {self.n_cam} 声音图 {self.n_sm} 推出 {self.n_out}"
                    f"  叠加 {self.blend_ms:.1f}ms"
                    f"  声音图 {'无' if self._sm_stale else '有'}")


def main():
    ap = argparse.ArgumentParser(description="鱼眼 ＋ 声音图叠加，推回 OME")
    ap.add_argument("--fps", type=int, default=OUT_FPS, help="输出帧率")
    ap.add_argument("--size", type=int, default=OUT_SIZE, help="输出边长")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="跑多少秒后自己退出（0=一直跑）")
    a = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ome_receiver import OmeReceiver

    ov = Overlay(a.size, a.fps)
    host, port, app = env("OME_HOST"), int(env("OME_WS_PORT")), env("OME_APP")

    # **两条流各自一个 receiver。** OmeReceiver 每个实例自己起线程、自己带
    # MainContext（就是为并行收多条设计的），所以这里不需要额外做什么。
    cam = OmeReceiver(host, port, app, env("STREAM_KEY_FISHEYE"),
                      on_video=ov.on_fisheye, video_format="BGR",
                      logger=lambda lv, m: log(lv, f"[fisheye] {m}"))
    sm = OmeReceiver(host, port, app, env("STREAM_KEY_SOUNDMAP"),
                     on_video=ov.on_soundmap, video_format="BGR",
                     logger=lambda lv, m: log(lv, f"[soundmap] {m}"))

    running = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: running.set())
    signal.signal(signal.SIGTERM, lambda *_: running.set())

    log("info", f"输出 {a.size}x{a.size}@{a.fps}  alpha={ALPHA} beta={BETA}")
    cam.start()
    sm.start()
    t_end = time.monotonic() + a.seconds if a.seconds > 0 else None
    try:
        while not running.is_set():
            if running.wait(REPORT_SEC):
                break
            ov.report()
            if t_end and time.monotonic() >= t_end:
                break
    finally:
        cam.stop()
        sm.stop()
        if ov.out:
            ov.out.stop()
        log("info", f"终了 推出 {ov.n_out} 帧")


if __name__ == "__main__":
    main()
