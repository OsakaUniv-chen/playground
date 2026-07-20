"""One-off diagnostic (2026-07-19, owner's request): with early stopping
disabled, how low can TRAIN peak_dist go on a small ~24-bag pool (chat 3 +
WordWolfExp G1~G3 groups 18 + 3 arbitrary MTG bags)? Answers "is the current
SimVP capacity (N_S=N_T=4, gsta) anywhere near its ceiling on fitting this
task" -- separate question from generalization (see CONTEXT.md Phase 4 open
questions). No held-out split, no early stopping -- just train the pool as
hard as possible for a fixed epoch budget and watch where train peak_dist/PSR
flattens out. Not part of the regular Phase pipeline, not meant to be reused
by report.py.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "evaluation"))

from augment import augment_batch_noise_ratio
from dataset import CHAT_BAGS, WW_ALL_BAGS, MTG_TRAIN_BAGS, FullBagWindowDataset
from simvp import SimVP
from train import run_epoch, RUNS_DIR

POOL_BAGS = CHAT_BAGS + [b for b in WW_ALL_BAGS if b.split("_")[0] in ("G1", "G2", "G3")] + MTG_TRAIN_BAGS[:3]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clip-len", type=int, default=10)
    ap.add_argument("--pred-len", type=int, default=4)
    ap.add_argument("--sm-ratio", type=float, default=0.5)
    ap.add_argument("--N-S", type=int, default=4, dest="n_s")
    ap.add_argument("--N-T", type=int, default=4, dest="n_t")
    ap.add_argument("--bs", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--weight-decay", type=float, default=0.01)
    ap.add_argument("--epochs", type=int, default=150)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--run-name", default="overfit_probe")
    ap.add_argument("--num-workers", type=int, default=4)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    run_dir = RUNS_DIR / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    n_ww = len(POOL_BAGS) - len(CHAT_BAGS) - 3
    print(f"pool: {len(POOL_BAGS)} bags -- chat={len(CHAT_BAGS)}, ww_G1-G3={n_ww}, mtg=3")

    ds = FullBagWindowDataset(POOL_BAGS, clip_len=args.clip_len, pred_len=args.pred_len, sm_ratio=args.sm_ratio)
    print(f"train windows: {len(ds)}")
    loader = DataLoader(ds, batch_size=args.bs, shuffle=True, num_workers=args.num_workers, drop_last=True)

    shape_in = (args.clip_len, 2, 64, 64)
    model = SimVP(shape_in, args.pred_len, model_type="gsta", N_S=args.n_s, N_T=args.n_t).to(args.device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"model ready on {args.device}, {n_params:,} params")

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    augment_fn = lambda x, y: augment_batch_noise_ratio(x, y, base_ratio=args.sm_ratio)
    criterion = nn.MSELoss()

    log_path = run_dir / "log.csv"
    with open(log_path, "w", newline="") as f:
        csv.writer(f).writerow(["epoch", "train_loss", "train_peak_dist_mean", "train_psr_agg", "epoch_seconds"])

    last_path = run_dir / "last_model.pt"
    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        train_loss, pd, psr = run_epoch(model, loader, args.device, criterion, optimizer, augment_fn=augment_fn)
        dt = time.time() - t0
        pd_mean, psr_mean = float(pd.mean()), float(psr.mean())
        print(f"epoch {epoch}/{args.epochs}  train_loss={train_loss:.6f}  "
              f"train_peak_dist={pd_mean:.3f}  train_psr={psr_mean:.2%}  ({dt:.0f}s)")
        with open(log_path, "a", newline="") as f:
            csv.writer(f).writerow([epoch, train_loss, pd_mean, psr_mean, dt])
        torch.save(model.state_dict(), last_path)  # no early stop / best tracking -- diagnostic only

    plot_curve(log_path, run_dir / "curves.png")
    print(f"done. log -> {log_path}")


def plot_curve(log_path: Path, out_path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    epochs, pd, psr = [], [], []
    with open(log_path) as f:
        for row in csv.DictReader(f):
            epochs.append(int(row["epoch"]))
            pd.append(float(row["train_peak_dist_mean"]))
            psr.append(float(row["train_psr_agg"]))

    fig, ax1 = plt.subplots(figsize=(7, 4))
    ax1.plot(epochs, pd, color="tab:blue", label="train_peak_dist")
    ax1.set_xlabel("epoch"); ax1.set_ylabel("train_peak_dist", color="tab:blue")
    ax2 = ax1.twinx()
    ax2.plot(epochs, psr, color="tab:orange", label="train_psr")
    ax2.set_ylabel("train_psr", color="tab:orange")
    fig.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close()


if __name__ == "__main__":
    main()
