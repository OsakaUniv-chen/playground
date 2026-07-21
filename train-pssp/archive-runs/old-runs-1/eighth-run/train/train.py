"""Training loop for the PSSP SimVP model -- eighth-run: attack the core
"position correlation below naive baseline" problem that survived all seven
prior runs (see CONTEXT.md "当前开放问题/下一步方向"). Chat-only (small/fast),
two experiments run SEPARATELY, each a single-variable comparison:

  Experiment A -- loss supervision TARGET (the one lever never varied):
    --loss mse         : pixel MSE over the whole heatmap (every prior run).
    --loss softargmax  : differentiable soft-argmax position loss (losses.py) --
                         directly supervises the predicted peak toward the
                         target's true peak.
    --loss combined    : mse + lam*softargmax (fallback if pure position loss
                         degenerates the heatmap shape).

  Experiment B -- input window length:
    --clip-len 10      : 5s history @2Hz (every prior run).
    --clip-len 20      : 10s history -- turn-taking signal may need more context.

**Early stopping monitors test peak_dist (mean over t+1..t+pred_len), NOT the
training loss** -- deliberate, and different from prior runs. peak_dist is what
we ultimately care about, and it's the SAME criterion regardless of which
training loss is used, so the mse-vs-softargmax comparison is fair (their raw
loss values are on incomparable scales). test_loss is still logged.

Backdrop held constant at sixth-run's winning chat recipe (lr=1e-3, noise_ratio
augmentation) so each experiment's control arm is comparable to that known-good
result. Change ONE thing per run.
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

from augment import augment_batch, augment_batch_noise_ratio
from dataset import make_datasets
from losses import soft_argmax_loss, combined_loss
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


def make_criterion(loss_name: str, tau: float, lam: float):
    """Returns a (pred, target) -> scalar loss callable."""
    if loss_name == "mse":
        mse = nn.MSELoss()
        return lambda out, y: mse(out, y)
    if loss_name == "softargmax":
        return lambda out, y: soft_argmax_loss(out, y, tau=tau)
    if loss_name == "combined":
        return lambda out, y: combined_loss(out, y, lam=lam, tau=tau)
    raise ValueError(loss_name)


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


class EarlyStopping:
    def __init__(self, patience: int):
        self.patience = patience
        self.best = float("inf")
        self.counter = 0
        self.should_stop = False

    def step(self, value: float) -> bool:
        """Returns True if `value` is a new best (lower)."""
        if value < self.best:
            self.best = value
            self.counter = 0
            return True
        self.counter += 1
        if self.counter >= self.patience:
            self.should_stop = True
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loss", choices=["mse", "softargmax", "combined"], default="mse",
                     help="training loss -- see module docstring / losses.py")
    ap.add_argument("--tau", type=float, default=0.05, help="softmax temperature for soft-argmax")
    ap.add_argument("--lam", type=float, default=0.01, help="position-term weight for --loss combined")
    ap.add_argument("--augment", choices=["none", "flipcrop", "noise_ratio"], default="noise_ratio",
                     help="held constant at sixth-run's winner (noise_ratio) by default")
    ap.add_argument("--clip-len", type=int, default=10, help="input window length in 2Hz ticks (10=5s, 20=10s)")
    ap.add_argument("--dataset", choices=["chat", "chat_g1g2_g3"], default="chat",
                     help="chat=chat-only 90/10 split (default); chat_g1g2_g3=chat(90/10)"
                          " + WordWolfExp G1+G2 (full, train) + G3 game3-6 (full, test)")
    ap.add_argument("--pred-len", type=int, default=4)
    ap.add_argument("--pred-offsets", type=str, default=None,
                     help="comma-separated ticks-ahead to predict, overrides the implicit 1..pred_len"
                          " (e.g. '2' for a single +1s@2Hz target instead of the usual 4-step t+1..t+4)")
    ap.add_argument("--sm-ratio", type=float, default=0.5)
    ap.add_argument("--simvp-type", type=str, default="gsta", choices=["incepu", "gsta", "tau"])
    ap.add_argument("--bs", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--weight-decay", type=float, default=0.01)
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--patience", type=int, default=10)
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
    print(f"run: {run_dir}  loss={args.loss}  clip_len={args.clip_len}  lr={args.lr}  augment={args.augment}"
          f"  pred_offsets={pred_offsets or list(range(1, args.pred_len + 1))}")

    train_ds, test_ds = make_datasets(clip_len=args.clip_len, pred_len=args.pred_len, sm_ratio=args.sm_ratio,
                                       dataset=args.dataset, pred_offsets=pred_offsets)
    offsets = train_ds.pred_offsets   # actual ticks-ahead per output slot, source of truth for labeling
    pred_len = train_ds.pred_len
    print(f"train windows: {len(train_ds)}, test windows: {len(test_ds)}")
    train_loader = DataLoader(train_ds, batch_size=args.bs, shuffle=True,
                               num_workers=args.num_workers, drop_last=True)
    test_loader = DataLoader(test_ds, batch_size=args.bs, shuffle=False,
                              num_workers=args.num_workers, drop_last=True)

    shape_in = (args.clip_len, 2, 64, 64)
    model = SimVP(shape_in, pred_len, model_type=args.simvp_type).to(args.device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"model ready on {args.device}, {n_params:,} params")

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    early_stopping = EarlyStopping(patience=args.patience)   # monitors test peak_dist (see docstring)
    augment_fn = {"none": None, "flipcrop": augment_batch, "noise_ratio": augment_batch_noise_ratio}[args.augment]
    criterion = make_criterion(args.loss, args.tau, args.lam)

    log_path = run_dir / "log.csv"
    fields = (["epoch", "train_loss", "test_loss"]
              + [f"test_peak_dist_t{o}" for o in offsets]
              + [f"test_psr_k{PSR_K}_{PSR_N:g}_t{o}" for o in offsets]
              + ["test_peak_dist_mean", "test_psr_aggregate"])
    with open(log_path, "w", newline="") as f:
        csv.writer(f).writerow(fields)

    best_path = run_dir / "best_model.pt"
    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        train_loss, _, _ = run_epoch(model, train_loader, args.device, criterion, optimizer, augment_fn=augment_fn)
        test_loss, test_pd, test_psr = run_epoch(model, test_loader, args.device, criterion, optimizer=None)
        dt = time.time() - t0
        pd_mean = float(test_pd.mean())

        pd_str = " ".join(f"t+{o}={test_pd[t]:.2f}" for t, o in enumerate(offsets))
        psr_str = " ".join(f"t+{o}={test_psr[t]:.2%}" for t, o in enumerate(offsets))
        print(f"epoch {epoch}/{args.epochs}  train_loss={train_loss:.6f}  test_loss={test_loss:.6f}  "
              f"peak_dist[{pd_str}] mean={pd_mean:.2f}  PSR_k{PSR_K}@{PSR_N:g}[{psr_str}] agg={test_psr.mean():.2%}  ({dt:.0f}s)")

        with open(log_path, "a", newline="") as f:
            csv.writer(f).writerow([epoch, train_loss, test_loss, *test_pd.tolist(),
                                     *test_psr.tolist(), pd_mean, test_psr.mean()])

        is_best = early_stopping.step(pd_mean)   # <-- early-stop on peak_dist, not loss
        if is_best:
            torch.save(model.state_dict(), best_path)
        if early_stopping.should_stop:
            print(f"early stopping at epoch {epoch} (best test_peak_dist_mean={early_stopping.best:.4f})")
            break

    plot_curves(log_path, run_dir / "curves.png")
    print(f"done. best model -> {best_path}, log -> {log_path}")


def plot_curves(log_path: Path, out_path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    epochs, train_losses, test_losses, pd_means = [], [], [], []
    with open(log_path) as f:
        for row in csv.DictReader(f):
            epochs.append(int(row["epoch"]))
            train_losses.append(float(row["train_loss"]))
            test_losses.append(float(row["test_loss"]))
            pd_means.append(float(row["test_peak_dist_mean"]))

    fig, ax1 = plt.subplots(figsize=(6, 4))
    ax1.plot(epochs, train_losses, label="train_loss", color="tab:blue")
    ax1.plot(epochs, test_losses, label="test_loss", color="tab:orange")
    ax1.set_xlabel("epoch"); ax1.set_ylabel("loss"); ax1.legend(loc="upper left")
    ax2 = ax1.twinx()
    ax2.plot(epochs, pd_means, label="test_peak_dist_mean", color="tab:green")
    ax2.set_ylabel("peak_dist_mean"); ax2.legend(loc="upper right")
    plt.tight_layout(); plt.savefig(out_path); plt.close()


if __name__ == "__main__":
    main()
