"""run-1 training loop -- chat-only (see PLAN.md "范围调整"). Two infra
changes from every prior run (negotiated with the owner 2026-07-18, see
PLAN.md Phase 0):

  1. Early stopping = LR-decay-linked (LRDecayEarlyStopping below), not a
     flat "stop after N epochs without improvement". A plateau first decays
     lr (giving the model a chance to keep refining at a lower rate); only
     after `max_decays` such decays plateau again (or lr has hit `min_lr`)
     does training actually stop. Targets a recurring pattern across
     third/fifth/seventh-run (archive-runs/old-runs-1/CONTEXT.md): the best
     epoch always landed suspiciously early relative to the epoch budget --
     a flat early stop may have been giving up right as a lower lr would
     have helped.
  2. --bs, --sm-ratio, --N-S, --N-T are all first-class CLI knobs (run-1
     Phase 1 sweeps --bs, Phase 3 sweeps --sm-ratio and --N-S/--N-T) --
     every prior run hardcoded these except sm_ratio.

Monitors val peak_dist (mean over t+1..t+pred_len), not val_loss -- same
reasoning as eighth-run: peak_dist is what we actually care about.

DataLoader shuffle=True reshuffles every epoch by construction (PyTorch
recreates the RandomSampler's permutation on each `iter()` call) -- verified
directly (not just from memory) in verify_shuffle.py, see PLAN.md Phase 0b.
"""
from __future__ import annotations

import argparse
import csv
import json
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

from augment import augment_batch, augment_batch_noise_ratio
from dataset import make_datasets
from metrics import peak_dist, psr_at
from simvp import SimVP

RUNS_DIR = _HERE / "runs"

PSR_K, PSR_N = 5, 5.0  # PSR_k5@5, the paper's headline setting


def peak_dist_batch(pred: torch.Tensor, target: torch.Tensor, k: int = 1) -> np.ndarray:
    pred_np = pred.detach().cpu().numpy()[:, :, 0]
    target_np = target.detach().cpu().numpy()[:, :, 0]
    return peak_dist(pred_np, target_np, k=k).mean(axis=0)


def psr_batch(pred: torch.Tensor, target: torch.Tensor, k: int = PSR_K, n: float = PSR_N) -> np.ndarray:
    pred_np = pred.detach().cpu().numpy()[:, :, 0]
    target_np = target.detach().cpu().numpy()[:, :, 0]
    return psr_at(pred_np, target_np, k=k, n=n, sample_axis=0)


def run_epoch(model, loader, device, criterion, optimizer=None, augment_fn=None):
    """optimizer given -> train mode + backward; else eval mode, no_grad.
    augment_fn only applies when training. Returns (mean_loss,
    mean_peak_dist[pred_len], mean_psr[pred_len])."""
    train = optimizer is not None
    model.train(train)

    total_loss, n_batches = 0.0, 0
    peak_dist_sum = psr_sum = None
    ctx = torch.enable_grad() if train else torch.no_grad()
    with ctx:
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            if train and augment_fn is not None:
                x, y = augment_fn(x, y)
            out = model(x)
            loss = criterion(out, y)

            if train:
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

            total_loss += loss.item()
            n_batches += 1
            pd = peak_dist_batch(out, y)
            psr = psr_batch(out, y)
            peak_dist_sum = pd if peak_dist_sum is None else peak_dist_sum + pd
            psr_sum = psr if psr_sum is None else psr_sum + psr

    return total_loss / n_batches, peak_dist_sum / n_batches, psr_sum / n_batches


class LRDecayEarlyStopping:
    """Monitors a metric (lower=better, here val_peak_dist_mean). On
    `plateau_patience` epochs without a new best: if the decay budget isn't
    exhausted and lr is still above min_lr, decay lr by `lr_decay_factor`
    (linked -- NOT a stop) and reset the plateau counter; otherwise, stop.
    See module docstring point 1 / PLAN.md Phase 0a."""

    def __init__(self, optimizer: torch.optim.Optimizer, plateau_patience: int,
                 lr_decay_factor: float, max_decays: int, min_lr: float):
        self.optimizer = optimizer
        self.plateau_patience = plateau_patience
        self.lr_decay_factor = lr_decay_factor
        self.max_decays = max_decays
        self.min_lr = min_lr
        self.best = float("inf")
        self.counter = 0
        self.decay_count = 0
        self.should_stop = False

    @property
    def lr(self) -> float:
        return self.optimizer.param_groups[0]["lr"]

    def step(self, value: float) -> bool:
        """Call once per epoch with the monitored metric. Returns True if
        `value` is a new best (caller should checkpoint on True)."""
        if value < self.best:
            self.best = value
            self.counter = 0
            return True

        self.counter += 1
        if self.counter < self.plateau_patience:
            return False

        self.counter = 0
        if self.decay_count >= self.max_decays or self.lr <= self.min_lr:
            self.should_stop = True
        else:
            new_lr = max(self.lr * self.lr_decay_factor, self.min_lr)
            for g in self.optimizer.param_groups:
                g["lr"] = new_lr
            self.decay_count += 1
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clip-len", type=int, default=10, help="input window length in 2Hz ticks (10=5s, 20=10s)")
    ap.add_argument("--pred-len", type=int, default=4)
    ap.add_argument("--pred-offsets", type=str, default=None,
                     help="comma-separated ticks-ahead to predict, overrides the implicit 1..pred_len")
    ap.add_argument("--sm-ratio", type=float, default=0.5)
    ap.add_argument("--simvp-type", type=str, default="gsta", choices=["incepu", "gsta", "tau"])
    ap.add_argument("--N-S", type=int, default=4, dest="n_s", help="SimVP spatial encoder/decoder depth")
    ap.add_argument("--N-T", type=int, default=4, dest="n_t", help="SimVP temporal (mid-net) depth")
    ap.add_argument("--bs", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3, help="starting lr, decays per LRDecayEarlyStopping")
    ap.add_argument("--weight-decay", type=float, default=0.01)
    ap.add_argument("--augment", choices=["none", "flipcrop", "noise_ratio"], default="noise_ratio")
    ap.add_argument("--epochs", type=int, default=200, help="hard cap -- LRDecayEarlyStopping normally stops first")
    ap.add_argument("--plateau-patience", type=int, default=3)
    ap.add_argument("--lr-decay-factor", type=float, default=0.5)
    ap.add_argument("--max-decays", type=int, default=3)
    ap.add_argument("--min-lr", type=float, default=1e-6)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--run-name", default=None, help="defaults to a timestamp")
    ap.add_argument("--num-workers", type=int, default=2)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    pred_offsets = [int(o) for o in args.pred_offsets.split(",")] if args.pred_offsets else None

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    run_name = args.run_name or time.strftime("%Y%m%d_%H%M%S")
    run_dir = RUNS_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "config.json", "w") as f:
        json.dump(vars(args), f, indent=2)
    print(f"run: {run_dir}  clip_len={args.clip_len}  bs={args.bs}  lr={args.lr}  sm_ratio={args.sm_ratio}"
          f"  N_S={args.n_s}  N_T={args.n_t}  augment={args.augment}")

    train_ds, val_ds = make_datasets(clip_len=args.clip_len, pred_len=args.pred_len, sm_ratio=args.sm_ratio,
                                      pred_offsets=pred_offsets)
    offsets = train_ds.pred_offsets
    pred_len = train_ds.pred_len
    print(f"train windows: {len(train_ds)}, val windows: {len(val_ds)}")
    train_loader = DataLoader(train_ds, batch_size=args.bs, shuffle=True,
                               num_workers=args.num_workers, drop_last=True)
    val_loader = DataLoader(val_ds, batch_size=args.bs, shuffle=False,
                             num_workers=args.num_workers, drop_last=True)

    shape_in = (args.clip_len, 2, 64, 64)
    model = SimVP(shape_in, pred_len, model_type=args.simvp_type, N_S=args.n_s, N_T=args.n_t).to(args.device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"model ready on {args.device}, {n_params:,} params")

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    early_stopping = LRDecayEarlyStopping(optimizer, plateau_patience=args.plateau_patience,
                                           lr_decay_factor=args.lr_decay_factor,
                                           max_decays=args.max_decays, min_lr=args.min_lr)
    augment_fn = {"none": None, "flipcrop": augment_batch,
                  "noise_ratio": lambda x, y: augment_batch_noise_ratio(x, y, base_ratio=args.sm_ratio)}[args.augment]
    criterion = nn.MSELoss()

    log_path = run_dir / "log.csv"
    fields = (["epoch", "lr", "decay_count", "train_loss", "val_loss"]
              + [f"val_peak_dist_t{o}" for o in offsets]
              + [f"val_psr_k{PSR_K}_{PSR_N:g}_t{o}" for o in offsets]
              + ["val_peak_dist_mean", "val_psr_aggregate", "epoch_seconds"])
    with open(log_path, "w", newline="") as f:
        csv.writer(f).writerow(fields)

    best_path = run_dir / "best_model.pt"
    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        train_loss, _, _ = run_epoch(model, train_loader, args.device, criterion, optimizer, augment_fn=augment_fn)
        val_loss, val_pd, val_psr = run_epoch(model, val_loader, args.device, criterion, optimizer=None)
        dt = time.time() - t0
        pd_mean = float(val_pd.mean())

        pd_str = " ".join(f"t+{o}={val_pd[t]:.2f}" for t, o in enumerate(offsets))
        psr_str = " ".join(f"t+{o}={val_psr[t]:.2%}" for t, o in enumerate(offsets))
        print(f"epoch {epoch}/{args.epochs}  lr={early_stopping.lr:.2e}  decays={early_stopping.decay_count}  "
              f"train_loss={train_loss:.6f}  val_loss={val_loss:.6f}  "
              f"peak_dist[{pd_str}] mean={pd_mean:.2f}  PSR_k{PSR_K}@{PSR_N:g}[{psr_str}] agg={val_psr.mean():.2%}  ({dt:.0f}s)")

        with open(log_path, "a", newline="") as f:
            csv.writer(f).writerow([epoch, early_stopping.lr, early_stopping.decay_count, train_loss, val_loss,
                                     *val_pd.tolist(), *val_psr.tolist(), pd_mean, val_psr.mean(), dt])

        is_best = early_stopping.step(pd_mean)
        if is_best:
            torch.save(model.state_dict(), best_path)
        if early_stopping.should_stop:
            print(f"early stopping at epoch {epoch} (best val_peak_dist_mean={early_stopping.best:.4f}, "
                  f"{early_stopping.decay_count} lr decays)")
            break

    plot_curves(log_path, run_dir / "curves.png")
    print(f"done. best model -> {best_path}, log -> {log_path}")


def plot_curves(log_path: Path, out_path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    epochs, lrs, train_losses, val_losses, pd_means = [], [], [], [], []
    with open(log_path) as f:
        for row in csv.DictReader(f):
            epochs.append(int(row["epoch"]))
            lrs.append(float(row["lr"]))
            train_losses.append(float(row["train_loss"]))
            val_losses.append(float(row["val_loss"]))
            pd_means.append(float(row["val_peak_dist_mean"]))

    fig, ax1 = plt.subplots(figsize=(6, 4))
    ax1.plot(epochs, train_losses, label="train_loss", color="tab:blue")
    ax1.plot(epochs, val_losses, label="val_loss", color="tab:orange")
    ax1.set_xlabel("epoch"); ax1.set_ylabel("loss"); ax1.legend(loc="upper left")
    ax2 = ax1.twinx()
    ax2.plot(epochs, pd_means, label="val_peak_dist_mean", color="tab:green")
    ax2.set_ylabel("peak_dist_mean"); ax2.legend(loc="upper right")
    plt.tight_layout(); plt.savefig(out_path); plt.close()


if __name__ == "__main__":
    main()
