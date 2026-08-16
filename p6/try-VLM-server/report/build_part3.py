#!/usr/bin/env python3
"""g1_data.json -> 第3部の Markdown 表(正解率 + 4ラベル precision/recall + 平均遅延)。
標準出力に貼り付け用の Markdown を出す。"""
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABELS = ("Left", "Right", "Teleoperator", "Others")
ORDER = ["baseline::-", "mode_A::coord", "mode_A::grid", "mode_A::nl",
         "mode_B::0.30", "mode_B::0.50", "mode_B::0.70", "combo::coord+a50"]
NAME = {"baseline::-": "baseline（視覚のみ）", "mode_A::coord": "mode_A: coord",
        "mode_A::grid": "mode_A: grid(3×3)", "mode_A::nl": "mode_A: nl",
        "mode_B::0.30": "mode_B: α=0.3", "mode_B::0.50": "mode_B: α=0.5",
        "mode_B::0.70": "mode_B: α=0.7",
        "combo::coord+a50": "**combo: coord + α=0.5**"}


def pr(rs, c):
    tp = sum(1 for r in rs if r["pred"] == c and r["gt"] == c)
    pred = sum(1 for r in rs if r["pred"] == c)
    gt = sum(1 for r in rs if r["gt"] == c)
    p = "%.0f%%" % (100 * tp / pred) if pred else "—"
    rc = "%.0f%%" % (100 * tp / gt) if gt else "—"
    return "%s / %s" % (p, rc)


def main():
    d = json.loads((HERE / "g1_data.json").read_text())
    recs = d["records"]
    by = {}
    for r in recs:
        by.setdefault("%s::%s" % (r["condition"], r["method"]), []).append(r)
    gt = Counter(r["gt"] for r in by["baseline::-"])

    print("G1_game3_Tele 全 %d tick。gt 分布: %s。" % (
        len(by["baseline::-"]),
        " / ".join("%s %d" % (c, gt[c]) for c in LABELS)))
    print()
    print("| 手法 | 正解率 | 平均遅延 | Left P/R | Right P/R | Teleoperator P/R | Others P/R |")
    print("|---|---|---|---|---|---|---|")
    for k in ORDER:
        rs = by[k]
        acc = 100 * sum(r["correct"] for r in rs) / len(rs)
        lat = sum(r["latency_ms"] for r in rs) / len(rs) / 1000
        cells = " | ".join(pr(rs, c) for c in LABELS)
        print("| %s | **%.1f%%** | %.2fs | %s |" % (NAME[k], acc, lat, cells))
    print()
    print("（P/R = precision / recall。正解率のランダム基線 25%。）")


if __name__ == "__main__":
    main()
