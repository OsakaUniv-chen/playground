"""Evaluates one sixth-run checkpoint on the fixed sparse/2Hz chat test set
(see train/dataset.py -- last 10% of each of chat's 3 bags, same test set
for every ablation arm) and appends a dated section to ../RESULTS.md.
Always reports the naive continuity baseline and exp4 alongside the run
being evaluated, per project convention (CONTEXT.md: "朴素基线每次都要一起报").
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "train"))

from dataset import SparseChatWindowDataset
from metrics import peak_coords, peak_dist, psr_at
from simvp import SimVP
from train import PSR_K, PSR_N

ACCESS_MODEL_DIR = _HERE.parent.parent.parent / "access-model"
RUNS_DIR = _HERE.parent / "train" / "runs"
REPORT_PATH = _HERE.parent / "RESULTS.md"

CLIP_LEN, PRED_LEN, SM_RATIO = 10, 4, 0.5

REPORT_HEADER = """# sixth-run 结果报告

只用 chat（3 个 bag，每个按 `train_ratio=0.9` 做时间切分，前90%训练/后10%测试，和
exp4 自己的切分方式完全一致）做小规模消融，逐一验证 fifth-run 深挖 exp4 训练代码
后找到的三个真实训练管线差异（见 CONTEXT.md 的 fifth-run 一节）：lr、滑窗起点密度、
数据增强。测试集在所有消融里保持不变（sparse/2Hz，每个 bag 最后10%），只变训练时
的那一个变量，结果才能直接比较。

## 评价指标说明

见 third-run/fifth-run RESULTS.md，同一套指标（peak_dist k=1、PSR_k5@5、位置相关性
Pearson r），同一套朴素连续性基线。

---
"""


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


def _pearson(a: list[float], b: list[float]) -> float:
    a, b = np.asarray(a), np.asarray(b)
    if a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def compute_metrics(models: dict, loader, device) -> tuple[dict, dict, dict, int]:
    pd_sums = {name: None for name in models}
    psr_sums = {name: None for name in models}
    pred_rows = {name: [[] for _ in range(PRED_LEN)] for name in models}
    pred_cols = {name: [[] for _ in range(PRED_LEN)] for name in models}
    true_rows = [[] for _ in range(PRED_LEN)]
    true_cols = [[] for _ in range(PRED_LEN)]
    cont_rows, cont_cols = [], []
    n_batches = n_windows = 0

    with torch.no_grad():
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            y_np = y.detach().cpu().numpy()[:, :, 0]
            tr, tc = peak_coords(y_np)
            for t in range(PRED_LEN):
                true_rows[t].extend(tr[:, t].tolist())
                true_cols[t].extend(tc[:, t].tolist())

            last_blend, last_gray = x[:, -1, 0], x[:, -1, 1]
            last_sm = ((last_blend - (1.0 - SM_RATIO) * last_gray) / SM_RATIO).detach().cpu().numpy()
            lr, lc = peak_coords(last_sm)
            cont_rows.extend(lr.tolist())
            cont_cols.extend(lc.tolist())

            for name, fn in models.items():
                out = fn(x)
                out_np = out.detach().cpu().numpy() if torch.is_tensor(out) else out
                if out_np.ndim == 5:
                    out_np = out_np[:, :, 0]
                pd = peak_dist(out_np, y_np, k=1).mean(axis=0)
                psr = psr_at(out_np, y_np, k=PSR_K, n=PSR_N, sample_axis=0)
                pd_sums[name] = pd if pd_sums[name] is None else pd_sums[name] + pd
                psr_sums[name] = psr if psr_sums[name] is None else psr_sums[name] + psr

                pr, pc = peak_coords(out_np)
                for t in range(PRED_LEN):
                    pred_rows[name][t].extend(pr[:, t].tolist())
                    pred_cols[name][t].extend(pc[:, t].tolist())

            n_batches += 1
            n_windows += x.shape[0]

    pd_means = {name: s / n_batches for name, s in pd_sums.items()}
    psr_means = {name: s / n_batches for name, s in psr_sums.items()}
    corr_means = {}
    for name in models:
        corr_means[name] = np.array([
            (_pearson(pred_rows[name][t], true_rows[t]) + _pearson(pred_cols[name][t], true_cols[t])) / 2
            for t in range(PRED_LEN)
        ])
    corr_means["naive continuity (last-input pos)"] = np.array([
        (_pearson(cont_rows, true_rows[t]) + _pearson(cont_cols, true_cols[t])) / 2
        for t in range(PRED_LEN)
    ])
    return pd_means, psr_means, corr_means, n_windows


def print_report(pd_means: dict, psr_means: dict, corr_means: dict):
    header = "".join(f"t+{t+1:>7}" for t in range(PRED_LEN))
    print("\npeak_dist (k=1, raw, grid cells on a 64x64 map -- lower is better):")
    print(f"  {'':>32}{header}   mean")
    for name, mean in pd_means.items():
        print(f"  {name:>32}" + "".join(f"{v:9.2f}" for v in mean) + f"   {mean.mean():.2f}")

    print(f"\nPSR_k{PSR_K}@{PSR_N:g} (success rate, higher is better):")
    print(f"  {'':>32}{header}   Aggregate")
    for name, mean in psr_means.items():
        print(f"  {name:>32}" + "".join(f"{v:8.2%}" for v in mean) + f"   {mean.mean():.2%}")

    print("\nposition correlation (Pearson r, predicted vs true peak position -- higher is better):")
    print(f"  {'':>32}{header}   mean")
    for name, mean in corr_means.items():
        print(f"  {name:>32}" + "".join(f"{v:9.3f}" for v in mean) + f"   {mean.mean():.3f}")


def format_report(run_name: str, args_desc: str, pd_means: dict, psr_means: dict, corr_means: dict,
                   n_windows: int) -> str:
    header = "".join(f" t+{t+1} |" for t in range(PRED_LEN))
    sep = "---|" * (PRED_LEN + 1)
    lines = [
        f"## {time.strftime('%Y-%m-%d %H:%M')} {run_name}",
        "",
        f"配置：{args_desc}。测试集：chat 3 个bag各自最后10%（{n_windows} 个窗口）。",
        "",
        f"### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)",
        "",
        f"| |{header} 平均 |",
        f"|---|{sep}",
    ]
    for name, mean in pd_means.items():
        cells = "".join(f" {v:.2f} |" for v in mean)
        lines.append(f"| {name} |{cells} {mean.mean():.2f} |")

    lines += ["", f"### PSR_k{PSR_K}@{PSR_N:g} (成功率, 越大越好)", "", f"| |{header} Aggregate |", f"|---|{sep}"]
    for name, mean in psr_means.items():
        cells = "".join(f" {v:.2%} |" for v in mean)
        lines.append(f"| {name} |{cells} {mean.mean():.2%} |")

    lines += ["", f"### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)", "",
              f"| |{header} 平均 |", f"|---|{sep}"]
    for name, mean in corr_means.items():
        cells = "".join(f" {v:.3f} |" for v in mean)
        lines.append(f"| {name} |{cells} {mean.mean():.3f} |")

    lines.append("\n---\n")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", required=True, help="train/runs/<name>/best_model.pt to evaluate")
    ap.add_argument("--desc", default="", help="short human-readable description of this run's config, "
                                                  "for the RESULTS.md section header")
    args = ap.parse_args()
    checkpoint = RUNS_DIR / args.run_name / "best_model.pt"

    device = "cuda" if torch.cuda.is_available() else "cpu"
    shape_in = (CLIP_LEN, 2, 64, 64)

    test_ds = SparseChatWindowDataset("test", clip_len=CLIP_LEN, pred_len=PRED_LEN, sm_ratio=SM_RATIO)
    loader = DataLoader(test_ds, batch_size=32, shuffle=False, drop_last=True)
    print(f"test set: {len(test_ds)} windows, {len(loader)} batches of 32")
    print(f"checkpoint: {checkpoint}")

    old_cfg = json.load(open(ACCESS_MODEL_DIR / "configs" / "simvp_exp4.json"))
    assert old_cfg["sm_ratio"] == SM_RATIO and old_cfg["pred_len"] == PRED_LEN
    old_model = load_checkpoint(ACCESS_MODEL_DIR / "weights" / "config_simvp_exp4.pt",
                                 shape_in, old_cfg["pred_len"], old_cfg["simvp_type"], device)
    new_model = load_checkpoint(checkpoint, shape_in, PRED_LEN, "gsta", device)

    models = {
        "old (exp4)": lambda x: old_model(x),
        f"sixth-run ({args.run_name})": lambda x: new_model(x),
        "baseline (repeat-last)": lambda x: baseline_repeat_last(x, PRED_LEN, SM_RATIO),
    }
    pd_means, psr_means, corr_means, n_windows = compute_metrics(models, loader, device)
    print_report(pd_means, psr_means, corr_means)

    section = format_report(args.run_name, args.desc or args.run_name, pd_means, psr_means, corr_means, n_windows)
    is_new = not REPORT_PATH.exists()
    with open(REPORT_PATH, "a") as f:
        if is_new:
            f.write(REPORT_HEADER)
        f.write(section)
    print(f"\nreport {'created' if is_new else 'appended'} -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
