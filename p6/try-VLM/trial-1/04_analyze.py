"""Summarize a probe run: confusion of pred vs GT clock + error histogram.

Reads results/localize_results.csv (from 03_probe_localize.py) and writes
results/localize_analysis.png plus a short text summary. Purely offline / no GPU.
"""
from __future__ import annotations
import argparse
import csv
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(HERE / "results" / "localize_results.csv"))
    args = ap.parse_args()
    res = Path(args.csv)
    rows = list(csv.DictReader(res.open()))
    conf = np.zeros((12, 12), int)          # [gt-1, pred-1]
    signed = []                              # signed circular error (pred-gt)
    for r in rows:
        if r["pred_clock"] == "":
            continue
        gt, pr = int(r["gt_clock"]), int(r["pred_clock"])
        conf[gt - 1, pr - 1] += 1
        d = (pr - gt + 6) % 12 - 6           # signed, in [-6,5]
        signed.append(d)
    signed = np.array(signed)

    fig, ax = plt.subplots(1, 2, figsize=(12, 5))
    im = ax[0].imshow(conf, cmap="viridis")
    ax[0].set_xticks(range(12), range(1, 13)); ax[0].set_yticks(range(12), range(1, 13))
    ax[0].set_xlabel("predicted clock"); ax[0].set_ylabel("GT clock")
    ax[0].set_title(f"confusion (diagonal=correct) — {res.stem}")
    fig.colorbar(im, ax=ax[0], fraction=0.046)

    ax[1].hist(signed, bins=np.arange(-6.5, 6.5, 1), color="#4c78a8", edgecolor="k")
    ax[1].axvline(0, color="r", lw=1)
    ax[1].set_xlabel("signed clock error (pred - GT)")
    ax[1].set_ylabel("count"); ax[1].set_title("error distribution")
    fig.tight_layout()
    out = res.with_name(res.stem + "_analysis.png")
    fig.savefig(out, dpi=100)

    n = len(rows)
    parsed = len(signed)
    exact = int((signed == 0).sum())
    within1 = int((np.abs(signed) <= 1).sum())
    mae = float(np.abs(signed).mean()) if parsed else float("nan")
    print(f"n={n} parsed={parsed}")
    print(f"exact={exact} ({exact/n:.1%})  within1={within1} ({within1/n:.1%})  "
          f"mean|err|={mae:.2f} clock-hrs")
    print(f"analysis -> {out}")


if __name__ == "__main__":
    main()
