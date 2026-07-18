"""Training loop for the PSSP SimVP model -- fifth-run: data scale/diversity
test #2, using the 2026-07-13 PSSPData reprocess.

fifth-run's single variable vs. third-run is DATA again (see dataset.py's
docstring for the full split reasoning): TRAIN_BAGS is now ~278 bags across
15+ collections (WordWolfExp incl. the merged EXP1-3/testrun_0420 bags,
GRP_meeting, olab_0630/rev, ATR_teleoperation's two RIKEN collections, chat,
Demonstration_Data(+nonconv), demo_data_0318_becap, egoSAS_test_data, kitchen
-- see CONTEXT.md/preprocessing/DATA_REPORT.md), vs. third-run's 78+2. Tests
whether third-run's "diversity may matter more than raw WordWolf volume"
hypothesis holds up now that non-WordWolf data is actually large instead of
2 chat bags. TEST_BAGS = chat_debate_exp1_topic3 (same as third-run) PLUS
G13_game3_DoA/game4_Random/game5_PSSP/game6_Tele -- SimVP architecture, loss,
lr, and every other hyperparameter unchanged from third-run so any metric
difference is attributable to the data, not a confounded change:
  - Single GPU, no DDP.
  - No data augmentation.
  - Plain MSE loss on the exp(x-max)-normalized target.
  - lr=1e-3 (AdamW), same as first/third-run.
  - Local logging only (print + CSV + a final loss-curve PNG), no wandb.

Reports, on the test set each epoch alongside MSE: raw peak_dist (argmax
displacement in grid cells, k=1/unfiltered) and PSR_k5@5 (the success-rate
metric from the owner's own paper, see metrics.py), both per t+1..t+pred_len
-- MSE alone doesn't say whether the model is any good at localization.
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

from dataset import make_datasets
from metrics import peak_dist, psr_at
from simvp import SimVP

DATA_DIR = _HERE.parent.parent.parent / "train-data"
RUNS_DIR = _HERE / "runs"

PSR_K, PSR_N = 5, 5.0  # PSR_k5@5, the paper's headline setting


def peak_dist_batch(pred: torch.Tensor, target: torch.Tensor, k: int = 1) -> np.ndarray:
    """pred/target: (B, pred_len, 1, H, W). Returns (pred_len,) mean argmax
    displacement (grid cells) over the batch, per prediction step."""
    pred_np = pred.detach().cpu().numpy()[:, :, 0]    # (B,T,H,W)
    target_np = target.detach().cpu().numpy()[:, :, 0]
    return peak_dist(pred_np, target_np, k=k).mean(axis=0)


def psr_batch(pred: torch.Tensor, target: torch.Tensor, k: int = PSR_K, n: float = PSR_N) -> np.ndarray:
    """Same shapes as peak_dist_batch. Returns (pred_len,) PSR_k@n over the batch."""
    pred_np = pred.detach().cpu().numpy()[:, :, 0]
    target_np = target.detach().cpu().numpy()[:, :, 0]
    return psr_at(pred_np, target_np, k=k, n=n, sample_axis=0)


def run_epoch(model, loader, device, optimizer=None):
    """optimizer given -> train mode + backward; else eval mode, no_grad.
    Returns (mean_loss, mean_peak_dist[pred_len], mean_psr_k5_5[pred_len])."""
    train = optimizer is not None
    model.train(train)
    criterion = nn.MSELoss()

    total_loss, n_batches = 0.0, 0
    peak_dist_sum = psr_sum = None
    ctx = torch.enable_grad() if train else torch.no_grad()
    with ctx:
        for x, y in loader:
            x, y = x.to(device), y.to(device)
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
        """Returns True if `value` is a new best."""
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
    ap.add_argument("--clip-len", type=int, default=10)
    ap.add_argument("--pred-len", type=int, default=4)
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

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    run_name = args.run_name or time.strftime("%Y%m%d_%H%M%S")
    run_dir = RUNS_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"run: {run_dir}")

    train_ds, test_ds = make_datasets(
        DATA_DIR, clip_len=args.clip_len, pred_len=args.pred_len, sm_ratio=args.sm_ratio,
    )
    print(f"train windows: {len(train_ds)}, test windows: {len(test_ds)}")
    train_loader = DataLoader(train_ds, batch_size=args.bs, shuffle=True,
                               num_workers=args.num_workers, drop_last=True)
    test_loader = DataLoader(test_ds, batch_size=args.bs, shuffle=False,
                              num_workers=args.num_workers, drop_last=True)

    shape_in = (args.clip_len, 2, 64, 64)
    model = SimVP(shape_in, args.pred_len, model_type=args.simvp_type).to(args.device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"model ready on {args.device}, {n_params:,} params")

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    early_stopping = EarlyStopping(patience=args.patience)

    log_path = run_dir / "log.csv"
    fields = (["epoch", "train_loss", "test_loss"]
              + [f"test_peak_dist_t{t+1}" for t in range(args.pred_len)]
              + [f"test_psr_k{PSR_K}_{PSR_N:g}_t{t+1}" for t in range(args.pred_len)]
              + ["test_psr_aggregate"])
    with open(log_path, "w", newline="") as f:
        csv.writer(f).writerow(fields)

    best_path = run_dir / "best_model.pt"
    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        train_loss, _, _ = run_epoch(model, train_loader, args.device, optimizer)
        test_loss, test_pd, test_psr = run_epoch(model, test_loader, args.device, optimizer=None)
        dt = time.time() - t0

        pd_str = " ".join(f"t+{t+1}={test_pd[t]:.2f}" for t in range(args.pred_len))
        psr_str = " ".join(f"t+{t+1}={test_psr[t]:.2%}" for t in range(args.pred_len))
        print(f"epoch {epoch}/{args.epochs}  train_loss={train_loss:.6f}  test_loss={test_loss:.6f}  "
              f"peak_dist[{pd_str}]  PSR_k{PSR_K}@{PSR_N:g}[{psr_str}] agg={test_psr.mean():.2%}  ({dt:.0f}s)")

        with open(log_path, "a", newline="") as f:
            csv.writer(f).writerow([epoch, train_loss, test_loss, *test_pd.tolist(),
                                     *test_psr.tolist(), test_psr.mean()])

        is_best = early_stopping.step(test_loss)
        if is_best:
            torch.save(model.state_dict(), best_path)
        if early_stopping.should_stop:
            print(f"early stopping at epoch {epoch} (best test_loss={early_stopping.best:.6f})")
            break

    plot_loss_curve(log_path, run_dir / "loss_curve.png")
    print(f"done. best model -> {best_path}, log -> {log_path}")


def plot_loss_curve(log_path: Path, out_path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    epochs, train_losses, test_losses = [], [], []
    with open(log_path) as f:
        for row in csv.DictReader(f):
            epochs.append(int(row["epoch"]))
            train_losses.append(float(row["train_loss"]))
            test_losses.append(float(row["test_loss"]))

    plt.figure(figsize=(6, 4))
    plt.plot(epochs, train_losses, label="train")
    plt.plot(epochs, test_losses, label="test")
    plt.xlabel("epoch")
    plt.ylabel("MSE loss")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


if __name__ == "__main__":
    main()
