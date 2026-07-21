"""Old-vs-new model comparison on the SAME held-out test set (TEST_BAGS).

Both models get IDENTICAL input construction (exp(x-max) + sm_ratio blend --
dataset.py and access-model/predict.py were made to match exactly, see
CONTEXT.md's normalization-correction writeup), and the SAME SimVP
architecture class, so any metric difference is attributable to the learned
weights, not to a preprocessing or architecture mismatch.

Reports three things per t+1..t+pred_len, matching the owner's own paper
(paper5/publications/...A Feasibility Study...Predictive Sound Source
Positioning.pdf) and metrics.py:
  - raw peak_dist (k=1, mean argmax displacement in grid cells)
  - PSR_k5@5 (success rate: fraction of predictions within 5 cells after a
    5x5 local-mean-filter smoothing)
Always includes a naive "repeat the last observed frame" baseline (same
methodology the paper and old-train-scripts/calc_metrics.py both used) --
a model that can't beat this isn't learning anything about the dynamics, and
per owner instruction this baseline must be reported every time, not just the
models' own numbers.

Every run appends a dated section to ../RESULTS.md (all 4 prediction steps,
not just the Aggregate/mean -- per owner instruction, the per-horizon
breakdown must always be recorded, not summarized away).
"""
from __future__ import annotations

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

from dataset import TEST_BAGS, PSSPWindowDataset
from metrics import peak_dist, psr_at
from simvp import SimVP
from train import PSR_K, PSR_N

DATA_DIR = _HERE.parent.parent.parent / "train-data"
ACCESS_MODEL_DIR = _HERE.parent.parent.parent / "access-model"
NEW_CHECKPOINT = _HERE.parent / "train" / "runs" / "baseline" / "best_model.pt"
REPORT_PATH = _HERE.parent / "RESULTS.md"

CLIP_LEN, PRED_LEN, SM_RATIO = 10, 4, 0.5

REPORT_HEADER = """# first-run 结果报告

记录 `first-run/` 这一轮里所有训练/对比的结果。脚本每次运行会在下面追加一节，不覆盖
历史记录。

## 评价指标说明

- **peak_dist (k=1)**：预测声图和真值声图各自找最大值位置，算欧氏距离（64x64 网格
  的格子数）。数值越小越好。
- **PSR_k@n**（来自 `paper5/publications/...Predictive Sound Source
  Positioning.pdf`，详见 `CONTEXT.md`）：先对声图做 k x k 局部均值滤波（边缘感知，
  只算图内实际邻居），再算 peak_dist，看这个距离小于阈值 n 的样本占多少比例——是
  **成功率**，不是平均距离。数值越大越好。这里统一用 k=5, n=5（PSR_k5@5）。
  "Aggregate" = 对 t+1~t+4 四步取平均，只是总览数字，**不能替代逐步结果**。
- **baseline（重复最后一帧）**：把输入历史的最后一帧原样当作未来 4 帧的预测，不用
  模型。任何真正学到时序动态的模型都应该明显超过这个基线——**每次汇报都必须带上
  这一行**，只看模型自己的数字没有意义。

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
    """x: (B,clip_len,2,H,W) -- ch0=sm_ratio-blended exp(sm), ch1=gray.
    Recovers the last history frame's unblended exp(sm) and repeats it
    pred_len times. Returns (B,pred_len,H,W)."""
    last_blend, last_gray = x[:, -1, 0], x[:, -1, 1]
    last_sm = (last_blend - (1.0 - sm_ratio) * last_gray) / sm_ratio
    return last_sm.unsqueeze(1).expand(-1, pred_len, -1, -1)


def compute_metrics(models: dict, loader, device) -> tuple[dict, dict, int]:
    """models: name -> callable(x)->pred tensor (B,T,1,H,W), OR the special
    string "baseline" to use baseline_repeat_last. Returns (pd_means,
    psr_means, n_windows) where each *_means[name] is a (PRED_LEN,) array."""
    pd_sums = {name: None for name in models}
    psr_sums = {name: None for name in models}
    n_batches = 0
    n_windows = 0
    with torch.no_grad():
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            y_np = y.detach().cpu().numpy()[:, :, 0]  # (B,T,H,W)

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


def format_report(pd_means: dict, psr_means: dict, n_windows: int, extra_notes: str = "") -> str:
    header = "".join(f" t+{t+1} |" for t in range(PRED_LEN))
    sep = "---|" * (PRED_LEN + 1)

    lines = [
        f"## {time.strftime('%Y-%m-%d %H:%M')} 对比运行",
        "",
        f"测试集：`{TEST_BAGS}`（{n_windows} 个窗口）。checkpoint：旧 = "
        f"`access-model/weights/config_simvp_exp4.pt`，新 = `{NEW_CHECKPOINT.relative_to(REPORT_PATH.parent)}`。",
        "",
        f"### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)",
        "",
        f"| |{header} 平均 |",
        f"|---|{sep}",
    ]
    for name, mean in pd_means.items():
        cells = "".join(f" {v:.2f} |" for v in mean)
        lines.append(f"| {name} |{cells} {mean.mean():.2f} |")

    lines += [
        "",
        f"### PSR_k{PSR_K}@{PSR_N:g} (成功率, 越大越好)",
        "",
        f"| |{header} Aggregate |",
        f"|---|{sep}",
    ]
    for name, mean in psr_means.items():
        cells = "".join(f" {v:.2%} |" for v in mean)
        lines.append(f"| {name} |{cells} {mean.mean():.2%} |")

    if extra_notes:
        lines += ["", extra_notes]

    lines.append("\n---\n")
    return "\n".join(lines)


def print_report(pd_means: dict, psr_means: dict):
    header = "".join(f"t+{t+1:>7}" for t in range(PRED_LEN))
    print("\npeak_dist (k=1, raw, grid cells on a 64x64 map -- lower is better):")
    print(f"  {'':>24}{header}   mean")
    for name, mean in pd_means.items():
        print(f"  {name:>24}" + "".join(f"{v:9.2f}" for v in mean) + f"   {mean.mean():.2f}")

    print(f"\nPSR_k{PSR_K}@{PSR_N:g} (success rate, higher is better):")
    print(f"  {'':>24}{header}   Aggregate")
    for name, mean in psr_means.items():
        print(f"  {name:>24}" + "".join(f"{v:8.2%}" for v in mean) + f"   {mean.mean():.2%}")


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    shape_in = (CLIP_LEN, 2, 64, 64)

    test_bags = [DATA_DIR / f"{b}.npz" for b in TEST_BAGS]
    test_ds = PSSPWindowDataset(test_bags, clip_len=CLIP_LEN, pred_len=PRED_LEN, sm_ratio=SM_RATIO)
    loader = DataLoader(test_ds, batch_size=32, shuffle=False, drop_last=True)
    print(f"test set: {TEST_BAGS} ({len(test_ds)} windows, {len(loader)} batches of 32)")

    old_cfg = json.load(open(ACCESS_MODEL_DIR / "configs" / "simvp_exp4.json"))
    assert old_cfg["sm_ratio"] == SM_RATIO and old_cfg["pred_len"] == PRED_LEN, \
        "old model config doesn't match this comparison's clip_len/pred_len/sm_ratio"
    old_model = load_checkpoint(ACCESS_MODEL_DIR / "weights" / "config_simvp_exp4.pt",
                                 shape_in, old_cfg["pred_len"], old_cfg["simvp_type"], device)
    new_model = load_checkpoint(NEW_CHECKPOINT, shape_in, PRED_LEN, "gsta", device)

    models = {
        "old (exp4)": lambda x: old_model(x),
        "new": lambda x: new_model(x),
        "baseline (repeat-last)": lambda x: baseline_repeat_last(x, PRED_LEN, SM_RATIO),
    }
    pd_means, psr_means, n_windows = compute_metrics(models, loader, device)
    print_report(pd_means, psr_means)

    notes = ("**注意**：新旧模型训练数据分布不完全对等（旧模型训练集不含 WordWolfExp "
             "数据），详见 CONTEXT.md 的对比限制说明。")
    section = format_report(pd_means, psr_means, n_windows, extra_notes=notes)
    is_new = not REPORT_PATH.exists()
    with open(REPORT_PATH, "a") as f:
        if is_new:
            f.write(REPORT_HEADER)
        f.write(section)
    print(f"\nreport {'created' if is_new else 'appended'} -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
