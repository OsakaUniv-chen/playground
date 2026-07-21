"""2026-07-16 owner requirement: report train-set AND test-set peak_dist/PSR_k5@5,
per t+1..t+4 (or fewer, for D's single-offset run) SEPARATELY (t+2 primary,
others auxiliary). See first-run/evaluation/eval_train_test.py for the full
rationale. RUN_CONFIG records each of eighth-run's 7 checkpoints' actual
training config (clip_len / dataset / pred_offsets) so train-set windows are
built exactly as each checkpoint saw them.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import torch
from torch.utils.data import DataLoader

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "train"))

from dataset import ChatWindowDataset, MixedWindowDataset, CHAT_BAGS, G1_BAGS, G2_BAGS, G3_TEST_BAGS
from metrics import peak_dist, psr_at
from simvp import SimVP
from train import PSR_K, PSR_N

RUNS_DIR = _HERE.parent / "train" / "runs"
REPORT_PATH = _HERE.parent / "RESULTS.md"
SM_RATIO = 0.5

# run_name -> (clip_len, dataset, pred_offsets)
RUN_CONFIG = {
    "A_mse_control":         (10, "chat", None),
    "A_softargmax":           (10, "chat", None),
    "A_softargmax_lr1e-4":    (10, "chat", None),
    "A_softargmax_lr1e-6":    (10, "chat", None),
    "B_clip20":                (20, "chat", None),
    "C_g1g2_train_g3_test":    (20, "chat_g1g2_g3", None),
    "D_horizon1s":              (20, "chat_g1g2_g3", [2]),
}


def load_checkpoint(path, shape_in, pred_len, device):
    model = SimVP(shape_in, pred_len, model_type="gsta").to(device)
    state_dict = torch.load(path, map_location=device, weights_only=True)
    state_dict = {k.replace("module.", ""): v for k, v in state_dict.items()}
    model.load_state_dict(state_dict)
    model.eval()
    return model


def baseline_repeat_last(x: torch.Tensor, pred_len: int, sm_ratio: float) -> torch.Tensor:
    last_blend, last_gray = x[:, -1, 0], x[:, -1, 1]
    last_sm = (last_blend - (1.0 - sm_ratio) * last_gray) / sm_ratio
    return last_sm.unsqueeze(1).expand(-1, pred_len, -1, -1)


def compute_metrics(models: dict, loader, device) -> tuple[dict, dict, int]:
    pd_sums = {name: None for name in models}
    psr_sums = {name: None for name in models}
    n_batches = n_windows = 0
    with torch.no_grad():
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            y_np = y.detach().cpu().numpy()[:, :, 0]
            for name, fn in models.items():
                out = fn(x)
                out_np = out.detach().cpu().numpy() if torch.is_tensor(out) else out
                if out_np.ndim == 5:
                    out_np = out_np[:, :, 0]
                pd = peak_dist(out_np, y_np, k=1).mean(axis=0)
                psr = psr_at(out_np, y_np, k=PSR_K, n=PSR_N, sample_axis=0)
                pd_sums[name] = pd if pd_sums[name] is None else pd_sums[name] + pd
                psr_sums[name] = psr if psr_sums[name] is None else psr_sums[name] + psr
            n_batches += 1
            n_windows += x.shape[0]
    pd_means = {name: s / n_batches for name, s in pd_sums.items()}
    psr_means = {name: s / n_batches for name, s in psr_sums.items()}
    return pd_means, psr_means, n_windows


def print_report(split_name: str, pd_means: dict, psr_means: dict, n_windows: int, offsets: list[int]):
    header = "".join(f"t+{o:>7}" for o in offsets)
    print(f"\n=== {split_name} ({n_windows} windows) ===")
    print("peak_dist (k=1, lower better):")
    print(f"  {'':>32}{header}   mean")
    for name, mean in pd_means.items():
        print(f"  {name:>32}" + "".join(f"{v:9.2f}" for v in mean) + f"   {mean.mean():.2f}")
    print(f"PSR_k{PSR_K}@{PSR_N:g} (higher better):")
    print(f"  {'':>32}{header}   Aggregate")
    for name, mean in psr_means.items():
        print(f"  {name:>32}" + "".join(f"{v:8.2%}" for v in mean) + f"   {mean.mean():.2%}")


def format_split_tables(split_label: str, pd_means: dict, psr_means: dict, n_windows: int, offsets: list[int]) -> list[str]:
    header = "".join(f" t+{o} |" for o in offsets)
    sep = "---|" * (len(offsets) + 1)
    lines = [f"#### {split_label}（{n_windows} 个窗口）", "",
             "peak_dist (k=1, 越小越好" + ("，**t+2 是重点参考步**" if 2 in offsets else "") + "):", "",
             f"| |{header} 平均(仅参考) |", f"|---|{sep}"]
    for name, mean in pd_means.items():
        cells = "".join(f" {v:.2f} |" for v in mean)
        lines.append(f"| {name} |{cells} {mean.mean():.2f} |")
    lines += ["", f"PSR_k{PSR_K}@{PSR_N:g} (越大越好" + ("，**t+2 是重点参考步**" if 2 in offsets else "") + "):", "",
              f"| |{header} Aggregate(仅参考) |", f"|---|{sep}"]
    for name, mean in psr_means.items():
        cells = "".join(f" {v:.2%} |" for v in mean)
        lines.append(f"| {name} |{cells} {mean.mean():.2%} |")
    lines.append("")
    return lines


def build_datasets(clip_len: int, dataset: str, pred_offsets):
    if dataset == "chat":
        train_ds = ChatWindowDataset("train", clip_len=clip_len, sm_ratio=SM_RATIO, pred_offsets=pred_offsets)
        test_groups = [("chat", ChatWindowDataset("test", clip_len=clip_len, sm_ratio=SM_RATIO,
                                                    pred_offsets=pred_offsets))]
    else:
        common = dict(clip_len=clip_len, sm_ratio=SM_RATIO, pred_offsets=pred_offsets,
                      split_bags=CHAT_BAGS, full_train_bags=[], full_test_bags=[])
        train_ds = MixedWindowDataset("train", **{**common, "full_train_bags": G1_BAGS + G2_BAGS})
        chat_test = MixedWindowDataset("test", **common)
        g3_test = MixedWindowDataset("test", clip_len=clip_len, sm_ratio=SM_RATIO, pred_offsets=pred_offsets,
                                      split_bags=[], full_train_bags=[], full_test_bags=G3_TEST_BAGS)
        test_groups = [("chat", chat_test), ("G3_game3-6", g3_test)]
    return train_ds, test_groups


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    lines = [f"## {time.strftime('%Y-%m-%d %H:%M')} train/test 分列复核（新指标口径：逐步不合并，重点t+2）", ""]

    for run_name, (clip_len, dataset, pred_offsets) in RUN_CONFIG.items():
        offsets = pred_offsets or [1, 2, 3, 4]
        pred_len = len(offsets)
        shape_in = (clip_len, 2, 64, 64)
        train_ds, test_groups = build_datasets(clip_len, dataset, pred_offsets)

        model = load_checkpoint(RUNS_DIR / run_name / "best_model.pt", shape_in, pred_len, device)
        models = {f"eighth-run ({run_name})": lambda x, m=model: m(x),
                  "baseline (repeat-last)": lambda x: baseline_repeat_last(x, pred_len, SM_RATIO)}

        train_loader = DataLoader(train_ds, batch_size=32, shuffle=False, drop_last=True)
        train_pd, train_psr, n_train = compute_metrics(models, train_loader, device)
        print_report(f"TRAIN {run_name}", train_pd, train_psr, n_train, offsets)

        lines.append(f"### {run_name} (clip_len={clip_len}, dataset={dataset}, offsets={offsets})")
        lines.append("")
        lines += format_split_tables("TRAIN", train_pd, train_psr, n_train, offsets)

        for group_name, test_ds in test_groups:
            test_loader = DataLoader(test_ds, batch_size=32, shuffle=False, drop_last=True)
            pd_means, psr_means, n_windows = compute_metrics(models, test_loader, device)
            print_report(f"TEST [{group_name}] {run_name}", pd_means, psr_means, n_windows, offsets)
            lines += format_split_tables(f"TEST [{group_name}]", pd_means, psr_means, n_windows, offsets)

    lines.append("\n---\n")
    with open(REPORT_PATH, "a") as f:
        f.write("\n".join(lines))
    print(f"\nappended -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
