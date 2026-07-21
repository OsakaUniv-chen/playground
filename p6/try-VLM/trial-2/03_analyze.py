"""4-label confusion matrix + accuracy for a trial-2 probe run."""
from __future__ import annotations
import argparse
import csv
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from common2 import LABELS

HERE = Path(__file__).parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(HERE / "results" / "probe_results.csv"))
    args = ap.parse_args()
    res = Path(args.csv)
    rows = list(csv.DictReader(res.open()))
    idx = {l: i for i, l in enumerate(LABELS)}
    conf = np.zeros((4, 5), int)             # cols: 4 labels + "(none)"
    for r in rows:
        g = idx[r["gt_label"]]
        p = idx.get(r["pred"], 4)
        conf[g, p] += 1

    fig, ax = plt.subplots(figsize=(6.5, 5))
    im = ax.imshow(conf, cmap="viridis")
    ax.set_xticks(range(5), list(LABELS) + ["(none)"], rotation=30, ha="right")
    ax.set_yticks(range(4), LABELS)
    ax.set_xlabel("predicted"); ax.set_ylabel("GT")
    for i in range(4):
        for j in range(5):
            ax.text(j, i, conf[i, j], ha="center", va="center",
                    color="w" if conf[i, j] < conf.max() / 2 else "k")
    n = len(rows)
    acc = sum(r["correct"] == "1" for r in rows) / n
    ax.set_title(f"trial-2 4-label — acc {acc:.1%} (random 25%), n={n}")
    fig.colorbar(im, fraction=0.046)
    fig.tight_layout()
    out = res.with_name(res.stem + "_confusion.png")
    fig.savefig(out, dpi=100)
    print(f"acc={acc:.1%}  ->  {out}")


if __name__ == "__main__":
    main()
