"""Training loop for DMVFN -- fourth-run: architecture test vs SimVP.

fourth-run's single variable vs. third-run is the MODEL (see dmvfn.py's
module docstring for the full DMVFN design/scoping notes). Data is
byte-identical to third-run (same dataset.py, same TRAIN_BAGS/TEST_BAGS,
same clip_len=10/pred_len=4 windows) so any metric difference between this
run and third-run's SimVP checkpoint is attributable to the architecture,
not a confounded data change.

Unlike SimVP (one forward pass over the whole clip -> all pred_len steps
at once), DMVFN is single-step by design (paper: predicts Ĩ_{t+1} from the
last 2 frames, iterates for further steps). So:
  - Training loss is single-step: only t+1 is supervised each batch (see
    dmvfn_loss, deep-supervised across the 9 MVFB stages).
  - Test-set peak_dist/PSR (reported every epoch, same as SimVP runs) needs
    the full t+1..t+pred_len horizon for comparability -- rollout() below
    autoregressively re-feeds the model's own last output as the next
    "current frame" for pred_len steps, exactly matching the paper's
    stated inference procedure and the owner's request to still get
    0.5/1/1.5/2s-ahead predictions comparable to SimVP's output shape.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "evaluation"))

from dataset import make_datasets
from dmvfn import DMVFN, dmvfn_loss
from metrics import peak_dist, psr_at

DATA_DIR = _HERE.parent.parent / "train-data"
RUNS_DIR = _HERE / "runs"

PSR_K, PSR_N = 5, 5.0  # PSR_k5@5, same headline setting used throughout this project


def rollout(model: DMVFN, x: torch.Tensor, pred_len: int) -> torch.Tensor:
    """x: (B, clip_len, channels, H, W) history (only the last 2 frames are
    actually used, see dmvfn.py's module docstring). Returns
    (B, pred_len, channels, H, W): autoregressively feeds the model's own
    previous output back in as the new "current frame" for each further
    step -- this is how the paper itself does multi-step prediction (it
    only ever trains single-step)."""
    i_prev, i_cur = x[:, -2], x[:, -1]
    outs = []
    for _ in range(pred_len):
        estimates, _ = model(i_prev, i_cur)
        pred = estimates[-1]
        outs.append(pred)
        i_prev, i_cur = i_cur, pred
    return torch.stack(outs, dim=1)


def peak_dist_batch(pred: torch.Tensor, target: torch.Tensor, k: int = 1) -> np.ndarray:
    """pred/target: (B, pred_len, 1, H, W). Returns (pred_len,) mean argmax
    displacement (grid cells) over the batch, per prediction step."""
    pred_np = pred.detach().cpu().numpy()[:, :, 0]    # (B,T,H,W)
    target_np = target.detach().cpu().numpy()[:, :, 0]
    return peak_dist(pred_np, target_np, k=k).mean(axis=0)


def psr_batch(pred: torch.Tensor, target: torch.Tensor, k: int = PSR_K, n: float = PSR_N) -> np.ndarray:
    pred_np = pred.detach().cpu().numpy()[:, :, 0]
    target_np = target.detach().cpu().numpy()[:, :, 0]
    return psr_at(pred_np, target_np, k=k, n=n, sample_axis=0)


def train_epoch(model: DMVFN, loader, device, optimizer) -> float:
    """Single-step training: only t+1 is supervised (see module docstring)."""
    model.train()
    total_loss, n_batches = 0.0, 0
    for x, y in loader:
        x, y = x.to(device), y.to(device)
        i_prev, i_cur, target = x[:, -2], x[:, -1], y[:, 0]
        estimates, flow_mask = model(i_prev, i_cur)
        loss = dmvfn_loss(estimates, target, flow_mask)

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        total_loss += loss.item()
        n_batches += 1
    return total_loss / n_batches


def eval_epoch(model: DMVFN, loader, device, pred_len: int):
    """Returns (mean_single_step_loss, peak_dist[pred_len], psr[pred_len]).
    single_step_loss uses the same t+1-only loss as training (for a
    comparable train/test loss curve); peak_dist/PSR use the full
    autoregressive rollout (see module docstring)."""
    model.eval()
    total_loss, n_batches = 0.0, 0
    peak_dist_sum = psr_sum = None
    with torch.no_grad():
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            i_prev, i_cur, target = x[:, -2], x[:, -1], y[:, 0]
            estimates, flow_mask = model(i_prev, i_cur)
            loss = dmvfn_loss(estimates, target, flow_mask)
            total_loss += loss.item()
            n_batches += 1

            out = rollout(model, x, pred_len)[:, :, 0:1]  # (B,pred_len,1,H,W)
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
    ap.add_argument("--feat", type=int, default=32, help="MVFB conv channel width")
    ap.add_argument("--bs", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-4,
                     help="lower than SimVP's 1e-3 -- DMVFN's 9-stage residual flow cascade "
                          "saturates/collapses at 1e-3 (see dmvfn.py's MVFB docstring)")
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

    model = DMVFN(channels=2, feat=args.feat).to(args.device)
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
        train_loss = train_epoch(model, train_loader, args.device, optimizer)
        test_loss, test_pd, test_psr = eval_epoch(model, test_loader, args.device, args.pred_len)
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
    plt.ylabel("single-step Laplacian L1 loss")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


if __name__ == "__main__":
    main()
