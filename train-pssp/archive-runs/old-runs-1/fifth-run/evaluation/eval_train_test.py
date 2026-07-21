"""2026-07-16 owner requirement: report train-set AND test-set peak_dist/PSR_k5@5,
per t+1..t+4 SEPARATELY (t+2 primary, others auxiliary). See
first-run/evaluation/eval_train_test.py for the full rationale. Test set stays
split into chat / wordwolfexp(G13) / combined groups (fifth-run's own
convention, never just a combined average -- see CONTEXT.md's fifth-run
lesson about mixed-domain averages being misleading).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import torch
from torch.utils.data import DataLoader

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "train"))

from dataset import TRAIN_BAGS, TEST_BAGS, TEST_BAG_MIN_START_FRAC, WORDWOLF_RE, PSSPWindowDataset
from metrics import peak_dist, psr_at
from simvp import SimVP
from torch.utils.data import Subset
from train import PSR_K, PSR_N

DATA_DIR = _HERE.parent.parent.parent / "train-data"
ACCESS_MODEL_DIR = _HERE.parent.parent.parent / "access-model"
NEW_CHECKPOINT = _HERE.parent / "train" / "runs" / "baseline" / "best_model.pt"
REPORT_PATH = _HERE.parent / "RESULTS.md"

CLIP_LEN, PRED_LEN, SM_RATIO = 10, 4, 0.5
# 278-bag train set (234k windows) makes a full pass take 20+ minutes; stride-
# subsample down to ~this many windows for a fast, still-representative estimate.
MAX_TRAIN_WINDOWS = 30000


def load_checkpoint(path, shape_in, pred_len, simvp_type, device):
    model = SimVP(shape_in, pred_len, model_type=simvp_type).to(device)
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


def print_report(split_name: str, pd_means: dict, psr_means: dict, n_windows: int):
    header = "".join(f"t+{t+1:>7}" for t in range(PRED_LEN))
    print(f"\n=== {split_name} ({n_windows} windows) ===")
    print("peak_dist (k=1, lower better):")
    print(f"  {'':>24}{header}   mean")
    for name, mean in pd_means.items():
        print(f"  {name:>24}" + "".join(f"{v:9.2f}" for v in mean) + f"   {mean.mean():.2f}")
    print(f"PSR_k{PSR_K}@{PSR_N:g} (higher better):")
    print(f"  {'':>24}{header}   Aggregate")
    for name, mean in psr_means.items():
        print(f"  {name:>24}" + "".join(f"{v:8.2%}" for v in mean) + f"   {mean.mean():.2%}")


def format_split_tables(split_label: str, pd_means: dict, psr_means: dict, n_windows: int) -> list[str]:
    header = "".join(f" t+{t+1} |" for t in range(PRED_LEN))
    sep = "---|" * (PRED_LEN + 1)
    lines = [f"#### {split_label}（{n_windows} 个窗口）", "",
             "peak_dist (k=1, 越小越好，**t+2 是重点参考步**):", "",
             f"| |{header} 平均(仅参考) |", f"|---|{sep}"]
    for name, mean in pd_means.items():
        cells = "".join(f" {v:.2f} |" for v in mean)
        lines.append(f"| {name} |{cells} {mean.mean():.2f} |")
    lines += ["", f"PSR_k{PSR_K}@{PSR_N:g} (越大越好，**t+2 是重点参考步**):", "",
              f"| |{header} Aggregate(仅参考) |", f"|---|{sep}"]
    for name, mean in psr_means.items():
        cells = "".join(f" {v:.2%} |" for v in mean)
        lines.append(f"| {name} |{cells} {mean.mean():.2%} |")
    lines.append("")
    return lines


def build_loader(bag_names: list[str], min_start_frac: dict | None = None,
                  max_windows: int | None = None) -> tuple[DataLoader, int, int]:
    """Returns (loader, n_used, n_total). n_used < n_total when a stride
    subsample was applied (see MAX_TRAIN_WINDOWS)."""
    paths = [DATA_DIR / f"{b}.npz" for b in bag_names]
    ds = PSSPWindowDataset(paths, clip_len=CLIP_LEN, pred_len=PRED_LEN, sm_ratio=SM_RATIO,
                           bag_min_start_frac=min_start_frac or {})
    n_total = len(ds)
    if max_windows and n_total > max_windows:
        stride = -(-n_total // max_windows)  # ceil div
        ds = Subset(ds, range(0, n_total, stride))
    return DataLoader(ds, batch_size=64, shuffle=False, drop_last=True), len(ds), n_total


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    shape_in = (CLIP_LEN, 2, 64, 64)

    new_model = load_checkpoint(NEW_CHECKPOINT, shape_in, PRED_LEN, "gsta", device)
    train_models = {"fifth-run (baseline)": lambda x: new_model(x),
                     "baseline (repeat-last)": lambda x: baseline_repeat_last(x, PRED_LEN, SM_RATIO)}

    old_cfg = json.load(open(ACCESS_MODEL_DIR / "configs" / "simvp_exp4.json"))
    old_model = load_checkpoint(ACCESS_MODEL_DIR / "weights" / "config_simvp_exp4.pt",
                                 shape_in, old_cfg["pred_len"], old_cfg["simvp_type"], device)
    test_models = {"old (exp4)": lambda x: old_model(x), **train_models}

    train_loader, n_used, n_total = build_loader(TRAIN_BAGS, max_windows=MAX_TRAIN_WINDOWS)
    subsample_note = "" if n_used == n_total else f"（等距抽样自 {n_total} 个窗口）"
    print(f"train: {n_used}/{n_total} windows used ({len(TRAIN_BAGS)} bags)")
    train_pd, train_psr, n_train_eff = compute_metrics(train_models, train_loader, device)
    print_report(f"TRAIN (全部278 bag{subsample_note})", train_pd, train_psr, n_train_eff)

    chat_bags = [b for b in TEST_BAGS if not WORDWOLF_RE.match(b)]
    wordwolf_bags = [b for b in TEST_BAGS if WORDWOLF_RE.match(b)]
    test_groups = [("chat", chat_bags), ("wordwolfexp (G13)", wordwolf_bags), ("combined", TEST_BAGS)]

    test_results = []
    for group_name, bag_names in test_groups:
        loader, _, _ = build_loader(bag_names, TEST_BAG_MIN_START_FRAC)
        pd_means, psr_means, n_windows = compute_metrics(test_models, loader, device)
        print_report(f"TEST [{group_name}]", pd_means, psr_means, n_windows)
        test_results.append((group_name, pd_means, psr_means, n_windows))

    lines = [f"## {time.strftime('%Y-%m-%d %H:%M')} train/test 分列复核（新指标口径：逐步不合并，重点t+2）", "",
             f"训练集：全部 {len(TRAIN_BAGS)} 个 bag。超过 {MAX_TRAIN_WINDOWS} 窗口时做等距抽样"
             "（stride subsample，不是随机），换取可行的评估时间。测试集：chat_debate_exp1_topic3"
             "(最后10%) + G13 game3-6，chat/wordwolfexp/combined 三组分开报告（不只看合并平均）。"
             "exp4 不在训练集上评估（训练数据边界不同，无过拟合诊断意义）。", ""]
    lines += format_split_tables(f"TRAIN{subsample_note}", train_pd, train_psr, n_train_eff)
    for group_name, pd_means, psr_means, n_windows in test_results:
        lines += format_split_tables(f"TEST [{group_name}]", pd_means, psr_means, n_windows)
    lines.append("\n---\n")

    with open(REPORT_PATH, "a") as f:
        f.write("\n".join(lines))
    print(f"\nappended -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
