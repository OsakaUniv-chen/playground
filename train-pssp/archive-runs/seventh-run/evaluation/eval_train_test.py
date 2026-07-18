"""2026-07-16 owner requirement: report train-set AND test-set peak_dist/PSR_k5@5,
per t+1..t+4 SEPARATELY (t+2 primary, others auxiliary). See
first-run/evaluation/eval_train_test.py for the full rationale. seventh-run's
4 checkpoints split into two train scopes: "baseline" used the full 278-bag
pool, the three wordwolf_chat_lr* variants used the 76-bag WordWolfExp+chat
subset (WORDWOLF_AND_CHAT_TRAIN_BAGS) -- RUN_TRAIN_BAGS below picks the right
one per checkpoint so train-set metrics reflect what each model actually saw.
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

from dataset import (TRAIN_BAGS, TEST_BAGS, TEST_BAG_MIN_START_FRAC, WORDWOLF_RE,
                      WORDWOLF_AND_CHAT_TRAIN_BAGS, PSSPWindowDataset)
from metrics import peak_dist, psr_at
from simvp import SimVP
from torch.utils.data import Subset
from train import PSR_K, PSR_N

# full-278-bag train set (234k windows) makes a full pass take 20+ minutes;
# stride-subsample down to ~this many windows for a fast, still-representative
# train-set estimate. The 76-bag wordwolf_chat scope is small enough to run in full.
MAX_TRAIN_WINDOWS = 30000

DATA_DIR = _HERE.parent.parent.parent / "train-data"
ACCESS_MODEL_DIR = _HERE.parent.parent.parent / "access-model"
RUNS_DIR = _HERE.parent / "train" / "runs"
REPORT_PATH = _HERE.parent / "RESULTS.md"

CLIP_LEN, PRED_LEN, SM_RATIO = 10, 4, 0.5

RUN_TRAIN_BAGS = {
    "baseline": TRAIN_BAGS,
    "wordwolf_chat_lr1e-3": WORDWOLF_AND_CHAT_TRAIN_BAGS,
    "wordwolf_chat_lr1e-4": WORDWOLF_AND_CHAT_TRAIN_BAGS,
    "wordwolf_chat_lr1e-6": WORDWOLF_AND_CHAT_TRAIN_BAGS,
}


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
    print(f"  {'':>32}{header}   mean")
    for name, mean in pd_means.items():
        print(f"  {name:>32}" + "".join(f"{v:9.2f}" for v in mean) + f"   {mean.mean():.2f}")
    print(f"PSR_k{PSR_K}@{PSR_N:g} (higher better):")
    print(f"  {'':>32}{header}   Aggregate")
    for name, mean in psr_means.items():
        print(f"  {name:>32}" + "".join(f"{v:8.2%}" for v in mean) + f"   {mean.mean():.2%}")


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


def build_loader(bag_names: list[str], min_start_frac: dict | None = None, bs: int = 64,
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
    return DataLoader(ds, batch_size=bs, shuffle=False, drop_last=True), len(ds), n_total


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    shape_in = (CLIP_LEN, 2, 64, 64)

    old_cfg = json.load(open(ACCESS_MODEL_DIR / "configs" / "simvp_exp4.json"))
    old_model = load_checkpoint(ACCESS_MODEL_DIR / "weights" / "config_simvp_exp4.pt",
                                 shape_in, old_cfg["pred_len"], old_cfg["simvp_type"], device)

    chat_bags = [b for b in TEST_BAGS if not WORDWOLF_RE.match(b)]
    wordwolf_bags = [b for b in TEST_BAGS if WORDWOLF_RE.match(b)]
    test_groups = [("chat", chat_bags), ("wordwolfexp (G13)", wordwolf_bags), ("combined", TEST_BAGS)]

    train_loader_cache = {}
    lines = [f"## {time.strftime('%Y-%m-%d %H:%M')} train/test 分列复核（新指标口径：逐步不合并，重点t+2）", "",
             "baseline 训练集是全部278 bag，wordwolf_chat_lr* 三个变体训练集是76 bag子集"
             "（WordWolfExp+chat，见 RUN_TRAIN_BAGS）——各自的 train 指标用各自实际训练用的数据算。"
             f"278-bag 训练集超过 {MAX_TRAIN_WINDOWS} 窗口时做等距抽样（stride subsample，不是随机），"
             "换取可行的评估时间，注释里会写清楚抽样比例。"
             "测试集 chat/wordwolfexp(G13)/combined 三组分开报告。exp4 不在训练集上评估。", ""]

    baseline_fn = lambda x: baseline_repeat_last(x, PRED_LEN, SM_RATIO)
    for run_name, train_bags in RUN_TRAIN_BAGS.items():
        key = tuple(train_bags)
        if key not in train_loader_cache:
            loader, n_used, n_total = build_loader(train_bags, max_windows=MAX_TRAIN_WINDOWS)
            train_loader_cache[key] = (loader, n_used, n_total)
            print(f"train scope for {run_name}: {len(train_bags)} bags, {n_used}/{n_total} windows used")
        train_loader, n_used, n_total = train_loader_cache[key]
        subsample_note = "" if n_used == n_total else f"（等距抽样自 {n_total} 个窗口）"

        model = load_checkpoint(RUNS_DIR / run_name / "best_model.pt", shape_in, PRED_LEN, "gsta", device)
        models = {f"seventh-run ({run_name})": lambda x, m=model: m(x), "baseline (repeat-last)": baseline_fn}

        train_pd, train_psr, n_train = compute_metrics(models, train_loader, device)
        print_report(f"TRAIN {run_name}", train_pd, train_psr, n_train)
        lines.append(f"### {run_name} (train scope: {len(train_bags)} bags)")
        lines.append("")
        lines += format_split_tables(f"TRAIN{subsample_note}", train_pd, train_psr, n_train)

        for group_name, bag_names in test_groups:
            loader, n_used_test, n_total_test = build_loader(bag_names, TEST_BAG_MIN_START_FRAC, bs=32)
            test_models = {"old (exp4)": lambda x: old_model(x), **models}
            pd_means, psr_means, n_windows = compute_metrics(test_models, loader, device)
            print_report(f"TEST [{group_name}] {run_name}", pd_means, psr_means, n_windows)
            lines += format_split_tables(f"TEST [{group_name}]", pd_means, psr_means, n_windows)

    lines.append("\n---\n")
    with open(REPORT_PATH, "a") as f:
        f.write("\n".join(lines))
    print(f"\nappended -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
