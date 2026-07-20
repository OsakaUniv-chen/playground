"""run-2 training loop -- mixed three-domain training pool (chat+WordWolfExp+
GRP_meeting, see dataset.py / PLAN.md). Builds directly on run-1's infra:

  - Early stopping = LR-decay-linked (LRDecayEarlyStopping, ported unchanged
    from run-1): a plateau first decays lr (not an immediate stop); only
    after `max_decays` such decays plateau again (or lr hits `min_lr`) does
    training actually stop.
  - --bs/--sm-ratio/--N-S/--N-T first-class CLI knobs, same as run-1.

DIFFERENCE from run-1: three separate eval domains (chat_val, wordwolfexp_test,
grpmtg_test), each with its own peak_dist/PSR -- never pooled into one merged
number when REPORTING (report.py handles that). For the single scalar early
stopping needs to monitor, though, we take the unweighted mean of the three
domains' peak_dist_mean (each domain counts equally regardless of window
count) -- logged every epoch alongside the three domains' own numbers.
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

from augment import augment_batch, augment_batch_noise_ratio, augment_batch_both
from dataset import make_datasets
from metrics import peak_dist, psr_at
from simvp import SimVP

RUNS_DIR = _HERE / "runs"

PSR_K, PSR_N = 5, 5.0
DOMAINS = ["chat", "wordwolfexp", "grpmtg"]


def peak_dist_batch(pred: torch.Tensor, target: torch.Tensor, k: int = 1) -> np.ndarray:
    pred_np = pred.detach().cpu().numpy()[:, :, 0]
    target_np = target.detach().cpu().numpy()[:, :, 0]
    return peak_dist(pred_np, target_np, k=k).mean(axis=0)


def psr_batch(pred: torch.Tensor, target: torch.Tensor, k: int = PSR_K, n: float = PSR_N) -> np.ndarray:
    pred_np = pred.detach().cpu().numpy()[:, :, 0]
    target_np = target.detach().cpu().numpy()[:, :, 0]
    return psr_at(pred_np, target_np, k=k, n=n, sample_axis=0)


def run_epoch(model, loader, device, criterion, optimizer=None, augment_fn=None):
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
    """See archive-runs/old-runs-2/run-1/train/train.py -- ported unchanged."""

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
    ap.add_argument("--clip-len", type=int, default=10)
    ap.add_argument("--pred-len", type=int, default=4)
    ap.add_argument("--sm-ratio", type=float, default=0.5)
    ap.add_argument("--ww-test-group", type=str, default="G1",
                     help="which WordWolfExp group's game3~6 to hold out as test (see dataset.py ww_split())")
    ap.add_argument("--use-full-pool", action="store_true",
                     help="add OTHER_BAGS (remaining ~157 train-data bags) into combined_train -- Phase 4's"
                          " 'use the full 283-bag pool' (default off, keeps Phase 1~3's 121-bag pool)")
    ap.add_argument("--atr1f-holdout", action="store_true", dest="atr1f_holdout",
                     help="add OTHER_TRAIN_BAGS_EX_ATR1F (108 bags) into combined_train, keeping ATR_RIKEN_1F"
                          " (49 bags) wholly unseen -- mutually exclusive with --use-full-pool, see dataset.py")
    ap.add_argument("--simvp-type", type=str, default="gsta", choices=["incepu", "gsta", "tau"])
    ap.add_argument("--N-S", type=int, default=4, dest="n_s")
    ap.add_argument("--N-T", type=int, default=4, dest="n_t")
    ap.add_argument("--bs", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--weight-decay", type=float, default=0.01)
    ap.add_argument("--drop", type=float, default=0.2, help="SimVP MLP dropout (class default 0.2)")
    ap.add_argument("--drop-path", type=float, default=0.2, dest="drop_path",
                     help="SimVP stochastic depth rate (class default 0.2)")
    ap.add_argument("--augment", choices=["none", "flipcrop", "noise_ratio", "both"], default="noise_ratio")
    ap.add_argument("--aug-noise-std", type=float, default=0.03, dest="aug_noise_std",
                     help="noise_ratio augment: gaussian noise std added to input (default 0.03)")
    ap.add_argument("--aug-ratio-lo", type=float, default=0.3, dest="aug_ratio_lo",
                     help="noise_ratio augment: lower bound of randomized sm/gray blend ratio (default 0.3)")
    ap.add_argument("--aug-ratio-hi", type=float, default=0.7, dest="aug_ratio_hi",
                     help="noise_ratio augment: upper bound of randomized sm/gray blend ratio (default 0.7)")
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--plateau-patience", type=int, default=3)
    ap.add_argument("--lr-decay-factor", type=float, default=0.5)
    ap.add_argument("--max-decays", type=int, default=3)
    ap.add_argument("--min-lr", type=float, default=1e-6)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--num-workers", type=int, default=2)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    run_name = args.run_name or time.strftime("%Y%m%d_%H%M%S")
    run_dir = RUNS_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "config.json", "w") as f:
        json.dump(vars(args), f, indent=2)
    print(f"run: {run_dir}  clip_len={args.clip_len}  bs={args.bs}  lr={args.lr}  sm_ratio={args.sm_ratio}"
          f"  N_S={args.n_s}  N_T={args.n_t}  augment={args.augment}")

    t0 = time.time()
    ds = make_datasets(clip_len=args.clip_len, pred_len=args.pred_len, sm_ratio=args.sm_ratio,
                        ww_test_group=args.ww_test_group, use_full_pool=args.use_full_pool,
                        atr1f_holdout=args.atr1f_holdout)
    print(f"datasets loaded in {time.time() - t0:.0f}s: combined_train={len(ds['combined_train'])}, "
          f"chat_val={len(ds['chat_val'])}, wordwolfexp_test={len(ds['wordwolfexp_test'])}, "
          f"grpmtg_test={len(ds['grpmtg_test'])}")

    train_loader = DataLoader(ds["combined_train"], batch_size=args.bs, shuffle=True,
                               num_workers=args.num_workers, drop_last=True)
    eval_loaders = {
        "chat": DataLoader(ds["chat_val"], batch_size=args.bs, shuffle=False, drop_last=True),
        "wordwolfexp": DataLoader(ds["wordwolfexp_test"], batch_size=args.bs, shuffle=False, drop_last=True),
        "grpmtg": DataLoader(ds["grpmtg_test"], batch_size=args.bs, shuffle=False, drop_last=True),
    }

    shape_in = (args.clip_len, 2, 64, 64)
    model = SimVP(shape_in, args.pred_len, model_type=args.simvp_type, N_S=args.n_s, N_T=args.n_t,
                   drop=args.drop, drop_path=args.drop_path).to(args.device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"model ready on {args.device}, {n_params:,} params, drop={args.drop}, drop_path={args.drop_path}")

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    early_stopping = LRDecayEarlyStopping(optimizer, plateau_patience=args.plateau_patience,
                                           lr_decay_factor=args.lr_decay_factor,
                                           max_decays=args.max_decays, min_lr=args.min_lr)
    augment_fn = {"none": None, "flipcrop": augment_batch,
                  "noise_ratio": lambda x, y: augment_batch_noise_ratio(
                      x, y, base_ratio=args.sm_ratio, ratio_range=(args.aug_ratio_lo, args.aug_ratio_hi),
                      noise_std=args.aug_noise_std),
                  "both": lambda x, y: augment_batch_both(
                      x, y, base_ratio=args.sm_ratio, ratio_range=(args.aug_ratio_lo, args.aug_ratio_hi),
                      noise_std=args.aug_noise_std)}[args.augment]
    criterion = nn.MSELoss()

    log_path = run_dir / "log.csv"
    fields = ["epoch", "lr", "decay_count", "train_loss"]
    for dom in DOMAINS:
        fields += [f"{dom}_peak_dist_mean", f"{dom}_psr_agg"]
    fields += ["overall_peak_dist_mean", "epoch_seconds"]
    with open(log_path, "w", newline="") as f:
        csv.writer(f).writerow(fields)

    best_path = run_dir / "best_model.pt"
    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        train_loss, _, _ = run_epoch(model, train_loader, args.device, criterion, optimizer, augment_fn=augment_fn)

        dom_pd_mean, dom_psr_agg = {}, {}
        for dom in DOMAINS:
            _, pd, psr = run_epoch(model, eval_loaders[dom], args.device, criterion, optimizer=None)
            dom_pd_mean[dom] = float(pd.mean())
            dom_psr_agg[dom] = float(psr.mean())
        overall_pd_mean = float(np.mean([dom_pd_mean[d] for d in DOMAINS]))
        dt = time.time() - t0

        dom_str = "  ".join(f"{d}: pd={dom_pd_mean[d]:.2f} psr={dom_psr_agg[d]:.2%}" for d in DOMAINS)
        print(f"epoch {epoch}/{args.epochs}  lr={early_stopping.lr:.2e}  decays={early_stopping.decay_count}  "
              f"train_loss={train_loss:.6f}  {dom_str}  overall_pd_mean={overall_pd_mean:.2f}  ({dt:.0f}s)")

        with open(log_path, "a", newline="") as f:
            row = [epoch, early_stopping.lr, early_stopping.decay_count, train_loss]
            for dom in DOMAINS:
                row += [dom_pd_mean[dom], dom_psr_agg[dom]]
            row += [overall_pd_mean, dt]
            csv.writer(f).writerow(row)

        is_best = early_stopping.step(overall_pd_mean)
        if is_best:
            torch.save(model.state_dict(), best_path)
        if early_stopping.should_stop:
            print(f"early stopping at epoch {epoch} (best overall_pd_mean={early_stopping.best:.4f}, "
                  f"{early_stopping.decay_count} lr decays)")
            break

    plot_curves(log_path, run_dir / "curves.png")
    print(f"done. best model -> {best_path}, log -> {log_path}")


def plot_curves(log_path: Path, out_path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    epochs, train_losses, overall_pd = [], [], []
    dom_pd = {d: [] for d in DOMAINS}
    with open(log_path) as f:
        for row in csv.DictReader(f):
            epochs.append(int(row["epoch"]))
            train_losses.append(float(row["train_loss"]))
            overall_pd.append(float(row["overall_peak_dist_mean"]))
            for d in DOMAINS:
                dom_pd[d].append(float(row[f"{d}_peak_dist_mean"]))

    fig, ax1 = plt.subplots(figsize=(7, 4))
    ax1.plot(epochs, train_losses, label="train_loss", color="tab:blue")
    ax1.set_xlabel("epoch"); ax1.set_ylabel("train_loss"); ax1.legend(loc="upper left")
    ax2 = ax1.twinx()
    ax2.plot(epochs, overall_pd, label="overall_peak_dist_mean", color="black", linewidth=2)
    for d, color in zip(DOMAINS, ["tab:green", "tab:orange", "tab:red"]):
        ax2.plot(epochs, dom_pd[d], label=f"{d}_peak_dist", color=color, linestyle="--", alpha=0.7)
    ax2.set_ylabel("peak_dist_mean"); ax2.legend(loc="upper right", fontsize=8)
    plt.tight_layout(); plt.savefig(out_path); plt.close()


if __name__ == "__main__":
    main()
