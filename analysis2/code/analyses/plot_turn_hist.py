"""Histogram of GT speaking-turn lengths per label (Left/Right/Teleoperator/Others).

A "turn" = one continuous run of the same GT 4-label; length = run × 0.25 s.
Visualises the distribution behind the mean±SD in report §4 (heavily right-skewed).

    python plot_turn_hist.py            # -> ../../results/metrics/turn_length_hist.png
"""
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wp4 import load, LABELS4  # noqa: E402

TICKS = os.path.join(os.path.dirname(__file__), "..", "..", "results", "ticks")
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "results", "metrics", "turn_length_hist.png")
XMAX = 5.0          # display cap (s); longer turns go into the last bin
BIN = 0.25         # tick granularity


def runs(seq):
    s = list(seq)
    out, j = [], 0
    while j < len(s):
        k = j
        while k < len(s) and s[k] == s[j]:
            k += 1
        out.append((s[j], k - j))
        j = k
    return out


def main():
    bags = load(TICKS)
    lens = {lab: [] for lab in LABELS4}
    for _, _, df in bags:
        for lab, ln in runs(df["gt_label"]):
            if lab in lens:
                lens[lab].append(ln * BIN)

    bins = np.arange(0, XMAX + BIN, BIN)
    colors = {"Left": "#1f77b4", "Right": "#ff7f0e",
              "Teleoperator": "#2ca02c", "Others": "#9467bd"}
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.5), sharex=True)
    for ax, lab in zip(axes.ravel(), LABELS4):
        r = np.array(lens[lab])
        over_n = int((r > XMAX).sum())
        ax.hist(r[r <= XMAX], bins=bins, color=colors[lab],
                edgecolor="white", alpha=0.9)
        # all >XMAX turns aggregated into one hatched overflow bar at the right
        if over_n:
            ax.bar(XMAX + BIN, over_n, width=BIN, color=colors[lab],
                   edgecolor="black", alpha=0.9, hatch="//")
        ax.axvline(r.mean(), color="red", lw=2,
                   label=f"mean {r.mean():.2f}s")
        ax.axvline(np.median(r), color="black", ls="--", lw=1.5,
                   label=f"median {np.median(r):.2f}s")
        ax.set_title(f"{lab}   (n={len(r)}, mean {r.mean():.2f}±{r.std():.2f}s)",
                     fontsize=11)
        ax.set_xticks([0, 1, 2, 3, 4, 5, XMAX + BIN])
        ax.set_xticklabels(["0", "1", "2", "3", "4", "5", f">{XMAX:.0f}"])
        ax.set_xlabel("turn length (s)")
        ax.set_ylabel("count")
        ax.legend(fontsize=9)
    fig.suptitle("GT speaking-turn length distribution — all 52 bags "
                 "(turn = continuous run of one label × 0.25 s)", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fig.savefig(OUT, dpi=130)
    print(f"wrote {OUT}")
    for lab in LABELS4:
        r = np.array(lens[lab])
        print(f"  {lab:13} n={len(r):5} 1-tick(0.25s)={100*(r<=0.25).mean():.1f}% "
              f"<=0.5s={100*(r<=0.5).mean():.1f}% >2s={100*(r>2).mean():.1f}%")


if __name__ == "__main__":
    main()
