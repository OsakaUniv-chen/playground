"""Training loop for the PSSP SimVP model -- second-run: loss-function ablation.

second-run's whole purpose (see train-pssp/CONTEXT.md) is to test whether the
loss function is why the old model's predictions came out noticeably more
"diffuse" than the sharp single-peak target -- the classic regression-to-the-
mean symptom of training a peaky spatial target with plain MSE. Three --loss
conditions, same architecture/hyperparameters/data otherwise (single-variable
ablation):
  - mse: unchanged from first-run. Sigmoid output (each pixel independently
    in [0,1]), nn.MSELoss.
  - bce: SAME sigmoid output, only the loss formula changes to
    nn.BCELoss -- the minimal possible change, isolates "does a
    probability-aware loss reduce blur" as the single variable.
  - kl: output_activation='none' (raw logits, no per-pixel sigmoid), loss is
    cross-entropy against the target treated as a spatial probability
    distribution (softmax over the whole H*W map, not per-pixel) -- forces a
    genuine competitive "only one place is hot" distribution, at the cost of
    changing the model's output head (see simvp.py's Decoder).

Loads from the shared train-pssp/train-data/ pool (see DATA_DIR below) --
same data first-run uses, this run's variable is the loss function only.

Logs the same peak_dist/PSR_k5@5 as first-run PLUS two new sharpness
diagnostics (peak_mass, entropy -- see metrics.py) that directly measure
"how concentrated is the prediction," independent of whether the peak is in
the right place. A model could have great peak_dist and still be diffuse (a
wide blob centered on the right cell) -- these catch that failure mode
specifically.
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
import torch.nn.functional as F
from torch.utils.data import DataLoader

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "evaluation"))

from dataset import make_datasets
from metrics import entropy, peak_dist, peak_mass, psr_at, to_prob_dist
from simvp import SimVP

DATA_DIR = _HERE.parent.parent.parent / "train-data"
RUNS_DIR = _HERE / "runs"

PSR_K, PSR_N = 5, 5.0  # PSR_k5@5, the paper's headline setting

LOSS_OUTPUT_ACTIVATION = {"mse": "sigmoid", "bce": "sigmoid", "kl": "none"}


def compute_loss(loss_name: str, out: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    """out/target: (B,T,1,H,W). For 'mse'/'bce', out is sigmoid-bounded [0,1].
    For 'kl', out is raw logits (output_activation='none')."""
    if loss_name == "mse":
        return F.mse_loss(out, target)
    if loss_name == "bce":
        return F.binary_cross_entropy(out.clamp(1e-6, 1 - 1e-6), target.clamp(0, 1))
    if loss_name == "kl":
        B, T, C, H, W = out.shape
        out_flat = out.view(B, T, C, H * W)
        target_flat = target.view(B, T, C, H * W)
        log_p = F.log_softmax(out_flat, dim=-1)
        target_dist = target_flat / target_flat.sum(dim=-1, keepdim=True).clamp_min(1e-12)
        return -(target_dist * log_p).sum(dim=-1).mean()
    raise ValueError(f"unknown loss {loss_name!r}")


def peak_dist_batch(pred: torch.Tensor, target: torch.Tensor, k: int = 1) -> np.ndarray:
    """pred/target: (B, pred_len, 1, H, W). Returns (pred_len,) mean argmax
    displacement (grid cells) over the batch, per prediction step. Argmax
    location is invariant to log_softmax/softmax (monotonic per-sample), so
    this works identically whether pred is raw logits or sigmoid output."""
    pred_np = pred.detach().cpu().numpy()[:, :, 0]    # (B,T,H,W)
    target_np = target.detach().cpu().numpy()[:, :, 0]
    return peak_dist(pred_np, target_np, k=k).mean(axis=0)


def psr_batch(pred: torch.Tensor, target: torch.Tensor, k: int = PSR_K, n: float = PSR_N) -> np.ndarray:
    """Same shapes as peak_dist_batch. Returns (pred_len,) PSR_k@n over the batch."""
    pred_np = pred.detach().cpu().numpy()[:, :, 0]
    target_np = target.detach().cpu().numpy()[:, :, 0]
    return psr_at(pred_np, target_np, k=k, n=n, sample_axis=0)


def sharpness_batch(pred: torch.Tensor, target: torch.Tensor, from_logits: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """pred/target: (B,pred_len,1,H,W). Returns (pred_peak_mass, pred_entropy,
    target_peak_mass, target_entropy), each (pred_len,) mean over the batch."""
    pred_np = pred.detach().cpu().numpy()[:, :, 0]
    target_np = target.detach().cpu().numpy()[:, :, 0]
    pred_p = to_prob_dist(pred_np, from_logits=from_logits)
    target_p = to_prob_dist(target_np, from_logits=False)  # target always >=0 already
    return (peak_mass(pred_p).mean(axis=0), entropy(pred_p).mean(axis=0),
            peak_mass(target_p).mean(axis=0), entropy(target_p).mean(axis=0))


def run_epoch(model, loader, device, loss_name, optimizer=None):
    """optimizer given -> train mode + backward; else eval mode, no_grad.
    Returns dict of (pred_len,)-shaped arrays plus scalar 'loss'."""
    train = optimizer is not None
    model.train(train)
    from_logits = LOSS_OUTPUT_ACTIVATION[loss_name] == "none"

    total_loss, n_batches = 0.0, 0
    sums = {}
    ctx = torch.enable_grad() if train else torch.no_grad()
    with ctx:
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            out = model(x)
            loss = compute_loss(loss_name, out, y)

            if train:
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

            total_loss += loss.item()
            n_batches += 1

            pred_pm, pred_ent, target_pm, target_ent = sharpness_batch(out, y, from_logits)
            batch_metrics = {
                "peak_dist": peak_dist_batch(out, y),
                "psr": psr_batch(out, y),
                "pred_peak_mass": pred_pm,
                "pred_entropy": pred_ent,
                "target_peak_mass": target_pm,
                "target_entropy": target_ent,
            }
            for k, v in batch_metrics.items():
                sums[k] = v if k not in sums else sums[k] + v

    result = {k: v / n_batches for k, v in sums.items()}
    result["loss"] = total_loss / n_batches
    return result


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
    ap.add_argument("--loss", choices=["mse", "bce", "kl"], default="mse")
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
    ap.add_argument("--run-name", default=None, help="defaults to --loss's name")
    ap.add_argument("--num-workers", type=int, default=2)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    run_name = args.run_name or args.loss
    run_dir = RUNS_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"run: {run_dir} (loss={args.loss})")

    train_ds, test_ds = make_datasets(
        DATA_DIR, clip_len=args.clip_len, pred_len=args.pred_len, sm_ratio=args.sm_ratio,
    )
    print(f"train windows: {len(train_ds)}, test windows: {len(test_ds)}")
    train_loader = DataLoader(train_ds, batch_size=args.bs, shuffle=True,
                               num_workers=args.num_workers, drop_last=True)
    test_loader = DataLoader(test_ds, batch_size=args.bs, shuffle=False,
                              num_workers=args.num_workers, drop_last=True)

    shape_in = (args.clip_len, 2, 64, 64)
    output_activation = LOSS_OUTPUT_ACTIVATION[args.loss]
    model = SimVP(shape_in, args.pred_len, model_type=args.simvp_type,
                   output_activation=output_activation).to(args.device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"model ready on {args.device}, {n_params:,} params, output_activation={output_activation}")

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    early_stopping = EarlyStopping(patience=args.patience)

    log_path = run_dir / "log.csv"
    fields = (["epoch", "train_loss", "test_loss"]
              + [f"test_peak_dist_t{t+1}" for t in range(args.pred_len)]
              + [f"test_psr_k{PSR_K}_{PSR_N:g}_t{t+1}" for t in range(args.pred_len)]
              + ["test_psr_aggregate"]
              + [f"test_pred_peak_mass_t{t+1}" for t in range(args.pred_len)]
              + [f"test_pred_entropy_t{t+1}" for t in range(args.pred_len)]
              + [f"test_target_peak_mass_t{t+1}" for t in range(args.pred_len)]
              + [f"test_target_entropy_t{t+1}" for t in range(args.pred_len)])
    with open(log_path, "w", newline="") as f:
        csv.writer(f).writerow(fields)

    best_path = run_dir / "best_model.pt"
    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        train_res = run_epoch(model, train_loader, args.device, args.loss, optimizer)
        test_res = run_epoch(model, test_loader, args.device, args.loss, optimizer=None)
        dt = time.time() - t0

        pd_str = " ".join(f"t+{t+1}={test_res['peak_dist'][t]:.2f}" for t in range(args.pred_len))
        psr_str = " ".join(f"t+{t+1}={test_res['psr'][t]:.2%}" for t in range(args.pred_len))
        sharp_str = " ".join(f"t+{t+1}={test_res['pred_peak_mass'][t]:.3f}(vs target {test_res['target_peak_mass'][t]:.3f})"
                              for t in range(args.pred_len))
        print(f"epoch {epoch}/{args.epochs}  train_loss={train_res['loss']:.6f}  test_loss={test_res['loss']:.6f}  "
              f"peak_dist[{pd_str}]  PSR_k{PSR_K}@{PSR_N:g} agg={test_res['psr'].mean():.2%}  "
              f"peak_mass[{sharp_str}]  ({dt:.0f}s)")

        with open(log_path, "a", newline="") as f:
            csv.writer(f).writerow([
                epoch, train_res["loss"], test_res["loss"],
                *test_res["peak_dist"].tolist(), *test_res["psr"].tolist(), test_res["psr"].mean(),
                *test_res["pred_peak_mass"].tolist(), *test_res["pred_entropy"].tolist(),
                *test_res["target_peak_mass"].tolist(), *test_res["target_entropy"].tolist(),
            ])

        is_best = early_stopping.step(test_res["loss"])
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
    plt.ylabel("loss")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


if __name__ == "__main__":
    main()
