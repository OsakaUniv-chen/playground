#!/usr/bin/env python3
"""
带 ground-truth 标签的评测 —— 判断远程 VLM 能否读出音源方向(而非像 3B 那样塌缩)。

对 word-wolf trial-2 的 160 张带标签音图(manifest2.csv, gt_label ∈
Left/Right/Teleoperator/Others), 逐张发到远程 VLM server, 解析预测标签, 输出:
  - 4 分类准确率(整体 + 逐类)
  - 混淆矩阵
  - 预测分布(用来看是否塌缩成某一个标签)

PROMPT 与 parse_label 直接沿用 p6/try-VLM/trial-2/02_probe.py, 保证与 3B 结果可比。
隧道/协议复用同目录 vlm_client.py。

前提: 远程已启动 server(建议 --max-new-tokens 128 以容纳 "reason + ANSWER:")。

用法
----
  /home/chen/.virtualenvs/wolf/bin/python eval_labeled.py            # 全部 160 张
  /home/chen/.virtualenvs/wolf/bin/python eval_labeled.py --limit 30
"""

import argparse
import csv
import os
import re
import socket
import sys
import time

from vlm_client import (start_tunnel, wait_port, send_request, recv_response,
                        encode_jpeg, DEFAULT_IP)

TRIAL2 = "/home/chen/Documents/Playground/p6/try-VLM/trial-2"
LABELS = ["Left", "Right", "Teleoperator", "Others"]

PROMPT = """This is a fisheye camera view of a Word Wolf game room, with a sound-energy heatmap overlaid on it (jet colormap: blue = quiet, green/yellow = medium, red = loudest). The red region shows where sound is coming from RIGHT NOW.

The scene has three possible sound sources:
- Two local players: one seated on the LEFT side, one seated on the RIGHT side of the view.
- A remote Teleoperator, whose voice comes out at the LOWER-CENTRE region of the image (over the table / robot in the middle-bottom). So when the red peak sits in that lower-centre region, the teleoperator is the one speaking.

Question: at this instant, which of these 4 is the sound source?
- Left        : the left player is speaking (red peak on the left person)
- Right       : the right player is speaking (red peak on the right person)
- Teleoperator: the remote operator is speaking (red peak in the lower-centre region)
- Others      : none of the above (no clear source / elsewhere / quiet)

Give one short reason, then on the final line write only the answer word,
one of these four: Left, Right, Teleoperator, Others.
Final line format -> ANSWER: word"""


def parse_label(text):
    m = re.findall(r"ANSWER:\s*([A-Za-z]+)", text)
    fallback = [w for w in re.findall(r"\b(Left|Right|Teleoperator|Others)\b", text)]
    cands = ([m[-1]] if m else []) + fallback[::-1]
    for c in cands:
        cl = c.lower()
        for lab in LABELS:
            if cl == lab.lower() or (cl in ("tele", "teleop", "operator") and lab == "Teleoperator"):
                return lab
    return None


def main():
    ap = argparse.ArgumentParser(description="带标签 VLM 评测")
    ap.add_argument("--manifest", default=os.path.join(TRIAL2, "manifest2.csv"))
    ap.add_argument("--images-dir", default=os.path.join(TRIAL2, "images"))
    ap.add_argument("--limit", type=int, default=0, help="只测前 N 张(0=全部)")
    ap.add_argument("--quality", type=int, default=70)
    ap.add_argument("--ip", default=DEFAULT_IP)
    ap.add_argument("--local-port", type=int, default=50017)
    ap.add_argument("--remote-port", type=int, default=50007)
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.manifest)))
    if args.limit:
        rows = rows[:args.limit]

    print("建立 SSH 端口转发 %d -> 3090:%d ..." % (args.local_port, args.remote_port))
    tunnel = start_tunnel(args.ip, args.local_port, args.remote_port)
    try:
        if not wait_port(args.local_port):
            print("!! 隧道未就绪。确认远程 server 已启动。", file=sys.stderr)
            return 1
        sock = socket.create_connection(("127.0.0.1", args.local_port))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print("已连接, 评测 %d 张 (JPEG q%d)\n" % (len(rows), args.quality))

        cm = {g: {p: 0 for p in LABELS} for g in LABELS}   # cm[gt][pred]
        pred_dist = {p: 0 for p in LABELS}
        n_correct = n_total = n_unparsed = 0
        lat = []

        for r in rows:
            path = os.path.join(args.images_dir, "%s.png" % r["id"])
            gt = r["gt_label"]
            img = encode_jpeg(path, args.quality)
            t0 = time.perf_counter()
            send_request(sock, PROMPT, img)
            out = recv_response(sock)
            lat.append((time.perf_counter() - t0) * 1000.0)
            if out is None:
                print("!! server 断开", file=sys.stderr)
                break
            pred = parse_label(out)
            n_total += 1
            if pred is None:
                n_unparsed += 1
            else:
                pred_dist[pred] += 1
                if gt in cm:
                    cm[gt][pred] += 1
                if pred == gt:
                    n_correct += 1
            mark = "OK " if pred == gt else "   "
            print("%s%-4s gt=%-12s pred=%-12s %5.0fms" % (
                mark, r["id"], gt, pred, lat[-1]))
        sock.close()

        # ---- 汇总 ----
        print("\n================ 结果 ================")
        acc = n_correct / n_total if n_total else 0.0
        print("4 分类准确率: %d/%d = %.1f%%   (未解析 %d)" % (
            n_correct, n_total, acc * 100, n_unparsed))
        print("随机基线 = 25%%;  平均延迟 %.0fms/张" % (sum(lat) / len(lat) if lat else 0))

        print("\n预测分布(看是否塌缩):")
        for p in LABELS:
            print("  %-12s %d" % (p, pred_dist[p]))

        print("\n混淆矩阵 (行=gt, 列=pred):")
        print("  %-12s %s" % ("", " ".join("%-6s" % p[:6] for p in LABELS)))
        for g in LABELS:
            print("  %-12s %s" % (g, " ".join("%-6d" % cm[g][p] for p in LABELS)))
    finally:
        tunnel.terminate()
        try:
            tunnel.wait(timeout=5)
        except Exception:
            tunnel.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
