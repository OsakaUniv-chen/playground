#!/usr/bin/env python3
"""vlm-server 的主循环：把 VLM 要的东西凑齐，然后判断。

    OME ──WebRTC──▶ fisheye  ┐ 持续收，本地环形缓冲
    OME ──WebRTC──▶ soundmap ┘ （架构 §5.1）
                                      │
    stream-server ── GET /transcript ─┤ 判断那一刻才拉（架构 §3.1）
                                      ▼
                                  【VLM】← **还没接**
                                      │
                     POST /decision ──┘ （架构 §5.3，也还没接）

**★ VLM 本体还没有。** 现在这个循环每 `DECIDE_INTERVAL` 秒把该凑的都凑齐、
把凑出来的东西记一行，然后**什么都不判断**。这样整条输入链路可以先跑起来、
被观察、被调参，等模型进来只要填 `decide()` 那一个函数。

## 三样输入，两条路

| 要什么 | 怎么来 |
|---|---|
| 鱼眼画面（多帧） | 从 OME 持续拉 WebRTC，本地缓冲，判断时按需取 |
| 声音图（多帧，和画面配对） | 同上 |
| 最近 N 秒的转写 | `GET /transcript` 找 stream-server 要 |

**媒体不走 HTTP。** 架构 §1.2 定的是「出 OME 一律 WebRTC」，没有例外 ——
让 stream-server 再用 HTTP 送一遍帧，等于在 OME 之外另开一条媒体通路。
而且多帧的时候那样也不省带宽（8 帧 × 756² × 1 Hz ≈ 13.8 Mbps，比持续拉流的
5.5 Mbps 还贵），持续收流在这台机器上实测只占 45% 的一个核。

## 帧怎么取是这边的事

`BUFFER_SEC` / `BUFFER_FPS` 决定留多少，`VLM_FRAMES` / `VLM_SPAN` 决定每次
判断取多少 —— **全是本机的 config，改了不用碰 stream-server。**

**★ 这几个值都还没定。** 一次吃几帧、跨多长时间、多久判断一次，要接上 VLM
之后拿实际效果调。缓冲留得够长（15 s），所以改取法不用改别的地方。
"""
from __future__ import annotations

import os

os.environ.setdefault("GIO_USE_PROXY_RESOLVER", "dummy")   # 理由见 recv.py

import argparse  # noqa: E402
import json  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from recv import Inputs, env, log  # noqa: E402
from transcript import TranscriptClient  # noqa: E402

REPORT_SEC = 30.0


def decide(frames, transcript):
    """**这里将来放 VLM。** 现在什么都不做。

    frames:
        `Inputs.frames()` 给的那个列表 —— 每项是
        `{"t":.., "video": Frame, "pair": Frame|None, "dt": 秒|None}`，
        `video` 是鱼眼、`pair` 是时刻最近的声音图。
        `Frame.array()` 出 (h, w, 3) uint8。
    transcript:
        `TranscriptClient.fetch()` 的返回。**`utterances` 空 ＋ `ok` 为真
        = 确实没人说话**；`ok` 为假 = 转写那一路坏了。这两种在 prompt 里
        该有不同的说法（架构 §5.2）。

    返回 None 表示这一轮不判断。真接上之后返回的东西 POST 给 stream-server
    转成 ROS（架构 §5.3）。
    """
    return None


class Loop:
    def __init__(self, args):
        self.args = args
        self.n_frames = int(args.frames or env("VLM_FRAMES"))
        self.span = float(args.span if args.span is not None else env("VLM_SPAN"))
        self.interval = float(args.interval or env("DECIDE_INTERVAL"))
        self.context_sec = float(env("TRANSCRIPT_SECONDS"))
        self.log_dir = env("LOG_DIR")

        # **只连视频。** fisheye 那条流虽然带着机体麦克风的音轨，但音频归
        # stream-server 处理了（架构 §5.1），这边不接那个 pad —— 少一路解码。
        self.inp = Inputs(only=["fisheye", "soundmap"])
        self.tc = TranscriptClient()
        self.n_tick = 0
        self.n_decide = 0
        self.started = time.time()
        self._seen = set()      # 已经写进本地 txt 的发话，(source, t_start)

    # ---- 每一轮 ----

    def tick(self):
        self.n_tick += 1
        frames = self.inp.frames(n=self.n_frames, span=self.span)
        tr = self.tc.fetch(self.context_sec)
        self._mirror(tr)
        out = decide(frames, tr)
        if out is not None:
            self.n_decide += 1
            self._append("vlm.txt", json.dumps(out, ensure_ascii=False))
        return frames, tr

    def _mirror(self, tr):
        """把拉回来的发话按音源写进本地 txt —— tmux 的窗口 1/2 tail 的就是它们。

        **按 (音源, 起始时刻) 去重。** 每轮拉的是最近 N 秒，同一句话会被拉到
        很多次；不去重的话窗口里全是重复。
        """
        for u in tr.get("utterances", []):
            key = (u["source"], round(u["t_start"], 3))
            if key in self._seen:
                continue
            self._seen.add(key)
            ts = time.strftime("%H:%M:%S", time.localtime(u["t_start"]))
            self._append(f"{u['source']}.txt",
                         f"{ts} [{u['t_end'] - u['t_start']:4.1f}s] {u['text']}")
        if len(self._seen) > 5000:          # 别无限涨
            self._seen = set(list(self._seen)[-2000:])

    def _append(self, name, line):
        with open(os.path.join(self.log_dir, name), "a") as f:
            f.write(line + "\n")

    # ---- 状态 ----

    def write_status(self, frames, tr):
        n_buf, buf_span = self.inp.buffered("fisheye")
        n_sm, _ = self.inp.buffered("soundmap")
        dts = [f["dt"] for f in frames if f["dt"] is not None]
        st = {
            "t": time.time(),
            "started": self.started,
            "ome": {"host": self.inp.host, "port": self.inp.port,
                    "app": self.inp.app},
            "inputs": self.inp.stats(),
            "buffer": {"fisheye": n_buf, "soundmap": n_sm,
                       "span": round(buf_span, 1),
                       "sec": self.inp.buffer_sec, "fps": self.inp.buffer_fps},
            "take": {"frames": self.n_frames, "span": self.span,
                     "got": len(frames),
                     "max_dt": round(max(dts, key=abs), 3) if dts else None},
            "transcript": {
                "url": self.tc.url, "ok": bool(tr.get("ok")),
                "seconds": self.context_sec,
                "n": len(tr.get("utterances", [])),
                "sources": tr.get("sources", {}),
                "error": tr.get("error"),
                "n_ok": self.tc.n_ok, "n_fail": self.tc.n_fail,
            },
            "decide": {"interval": self.interval, "ticks": self.n_tick,
                       "made": self.n_decide, "implemented": False},
        }
        tmp = os.path.join(self.log_dir, "status.json.tmp")
        with open(tmp, "w") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(self.log_dir, "status.json"))

    def run(self, seconds=0.0):
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())

        self.inp.start()
        log("info", f"每 {self.interval:.1f}s 判断一次；每次取 {self.n_frames} 帧 / "
                    f"跨 {self.span:.1f}s；转写窗 {self.context_sec:.0f}s")
        log("info", f"缓冲 {self.inp.buffer_sec:.0f}s @ {self.inp.buffer_fps:.0f}fps")
        log("warn", "★ decide() 还是空的 —— 只凑输入，不判断")

        t0 = time.monotonic()
        next_report = t0 + REPORT_SEC
        while not stop.is_set():
            if seconds > 0 and time.monotonic() - t0 >= seconds:
                break
            frames, tr = self.tick()
            self.write_status(frames, tr)
            if time.monotonic() >= next_report:
                next_report = time.monotonic() + REPORT_SEC
                n_buf, buf_span = self.inp.buffered("fisheye")
                dts = [f["dt"] for f in frames if f["dt"] is not None]
                mdt = f"{max(dts, key=abs) * 1000:.0f}ms" if dts else "-"
                tok = "ok" if tr.get("ok") else f"✗ {tr.get('error')}"
                log("info", f"[10s] 第 {self.n_tick} 轮 / 缓冲 {n_buf} 帧跨 "
                            f"{buf_span:.1f}s / 取到 {len(frames)} 帧 配对差 {mdt} / "
                            f"转写 {tok} {len(tr.get('utterances', []))} 句")
            stop.wait(self.interval)
        self.inp.stop()
        return 0


def main():
    ap = argparse.ArgumentParser(description="vlm-server 的主循环")
    ap.add_argument("--frames", type=int, default=None, help="每次取几帧")
    ap.add_argument("--span", type=float, default=None, help="这几帧跨多少秒")
    ap.add_argument("--interval", type=float, default=None, help="多久判断一次 [s]")
    ap.add_argument("--seconds", type=float, default=0.0, help="跑多久（0 = 一直跑）")
    a = ap.parse_args()

    os.makedirs(env("LOG_DIR"), exist_ok=True)
    return Loop(a).run(a.seconds)


if __name__ == "__main__":
    sys.exit(main())
