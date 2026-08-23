#!/usr/bin/env python3
"""tmux 窗口 0 的状态面板。**Ctrl-C 在这个窗口 = 全停。**

只做一件事：把 `$LOG_DIR/status.json` 摆出来。**这个文件由 asr.py 每
`STATUS_INTERVAL` 秒写一次**，面板不认识里面任何一路的含义 —— 加一路输入、
加一个统计量，改 asr.py 的 `_write_status()` 就行，这边不用动。

和 robot-pc 的面板同一个路数（那边是 shell 写的，因为那边有六个各自独立的
进程要记账；这边只有一个进程，状态是它自己算出来的，所以直接读它写的 JSON）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

C_RESET, C_DIM, C_BOLD = "\033[0m", "\033[2m", "\033[1m"
C_GREEN, C_RED, C_YELLOW = "\033[32m", "\033[31m", "\033[33m"

STALE_SEC = 6.0        # status.json 这么久没更新 = 进程八成没了


def w(text):
    """显示宽度。中文占 2 列，printf 的 %-Ns 按字节算会错位。"""
    return sum(2 if ord(c) > 0x2E80 else 1 for c in text)


def pad(text, width):
    return text + " " * max(0, width - w(text))


def main():
    log_dir = os.environ.get("LOG_DIR")
    if not log_dir:
        print("[error] LOG_DIR 未设置 —— 没读到 config.env", file=sys.stderr)
        return 1
    path = os.path.join(log_dir, "status.json")
    session = os.environ.get("TMUX_SESSION", "vlm")
    interval = float(os.environ.get("STATUS_INTERVAL", 2))

    try:
        while True:
            render(path, session, interval)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  全停中……")
        subprocess.run(["tmux", "kill-session", "-t", session],
                       stderr=subprocess.DEVNULL)
        return 0


def render(path, session, interval):
    print("\033[H\033[2J", end="")
    now = time.time()

    try:
        with open(path) as f:
            st = json.load(f)
    except Exception:
        st = None

    print(f"  {C_BOLD}vlm-server 状态{C_RESET}                "
          f"{time.strftime('%F %T')}")

    if st is None:
        print(f"\n  {C_RED}○ 还没有状态{C_RESET} —— vlm 进程刚起，"
              f"或者起不来。")
        print(f"  {C_DIM}切到 vlm 那个窗口看它在说什么（Ctrl-b 4）。{C_RESET}")
        _footer(interval)
        return

    age = now - st["t"]
    ome = st["ome"]
    buf, take, tr, dec = st["buffer"], st["take"], st["transcript"], st["decide"]
    print(f"  OME  {ome['host']}:{ome['port']}  ({ome['app']}/*)   "
          f"{C_DIM}tailnet，只取视频{C_RESET}")
    up = now - st["started"]
    print(f"  已运行 {int(up // 3600):02d}:{int(up % 3600 // 60):02d}:{int(up % 60):02d}"
          f"        {C_DIM}第 {dec['ticks']} 轮，每 {dec['interval']:.1f}s 一轮{C_RESET}")
    if age > STALE_SEC:
        print(f"\n  {C_RED}★ 状态已经 {age:.0f}s 没更新了 —— vlm 进程八成死了。"
              f"看 vlm 窗口（Ctrl-b 4）。{C_RESET}")

    # ---- 输入 ----
    print()
    print("  输入        状态       收到        最新     形状")
    print("  " + "-" * 62)
    for chan, sv in st["inputs"].items():
        unit = "帧" if sv.get("kind") == "video" else "块"
        if sv["alive"]:
            mark, aged = f"{C_GREEN}● 正常{C_RESET}", f"{sv['age']:.1f}s"
        elif sv["n"] > 0:
            mark, aged = f"{C_RED}○ 断了{C_RESET}", f"{sv['age']:.0f}s"
        else:
            mark, aged = f"{C_YELLOW}○ 没来{C_RESET}", "-"
        got = pad("{} {}".format(sv["n"], unit), 11)
        print(f"  {pad(chan, 11)} {mark}     {got} {pad(aged, 8)} {sv.get('desc','')}")

    missing = [c for c, v in st["inputs"].items() if v["n"] == 0]
    if missing:
        print(f"  {C_YELLOW}★ {' '.join(missing)} 一个数据都没来 —— "
              f"推流侧起了没有？stream key 对不对？{C_RESET}")

    # ---- 缓冲和取帧 ----
    print()
    print(f"  缓冲   鱼眼 {buf['fisheye']} 帧 / 声音图 {buf['soundmap']} 帧，"
          f"跨 {buf['span']:.1f}s   {C_DIM}上限 {buf['sec']:.0f}s @ {buf['fps']:.0f}fps{C_RESET}")
    dt = take["max_dt"]
    if dt is None:
        dtxt = f"{C_DIM}（没配到声音图）{C_RESET}"
    elif abs(dt) > 0.2:
        dtxt = f"{C_RED}配对差最大 {dt*1000:+.0f}ms{C_RESET}  ← 画面和斑点可能对不上"
    else:
        dtxt = f"配对差最大 {dt*1000:+.0f}ms"
    print(f"  取帧   要 {take['frames']} 帧跨 {take['span']:.1f}s，"
          f"实际拿到 {take['got']} 帧   {dtxt}")

    # ---- 转写 ----
    print()
    if tr["ok"]:
        bad = [k for k, v in tr["sources"].items() if not v.get("ok")]
        line = f"{C_GREEN}● 通{C_RESET}   最近 {tr['seconds']:.0f}s 有 {tr['n']} 句"
        if bad:
            line += f"   {C_YELLOW}★ 这几路没数据: {' '.join(bad)}{C_RESET}"
        elif tr["n"] == 0:
            line += f"   {C_DIM}（没人说话 —— 不是故障）{C_RESET}"
    else:
        line = f"{C_RED}○ 拉不到{C_RESET}  {tr.get('error')}"
    print(f"  转写   {line}")
    print(f"         {C_DIM}{tr['url']}  成功 {tr['n_ok']} / 失败 {tr['n_fail']}{C_RESET}")

    print()
    print(f"  VLM    {C_YELLOW}decide() 还是空的{C_RESET}   "
          f"{C_DIM}接上之后判断写在窗口 3{C_RESET}")
    _footer(interval)


def _footer(interval):
    print()
    print(f"  {C_DIM}窗口: 1=onboard 转写  2=operator 转写  3=VLM 判断  "
          f"4=vlm 进程本体（Ctrl-b 数字切换）{C_RESET}")
    print(f"  {C_DIM}全停: 在这个窗口按 Ctrl-C   |   离开但不停: Ctrl-b d   "
          f"|   每 {interval:.0f}s 刷新{C_RESET}")


if __name__ == "__main__":
    sys.exit(main())
