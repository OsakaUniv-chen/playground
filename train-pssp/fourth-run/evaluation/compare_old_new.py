"""Three-way model comparison on the SAME held-out test set (TEST_BAGS =
chat_debate_exp1_topic3, identical to third-run's -- see dataset.py's
docstring): old SimVP (access-model exp4), third-run's SimVP (best
checkpoint, lr=1e-6), and this run's DMVFN. Plus the naive "repeat the last
observed frame" baseline, always.

All three real models get IDENTICAL input construction (exp(x-max) +
sm_ratio blend) and the SAME test windows (fourth-run's dataset.py is a
byte-identical copy of third-run's) -- any metric difference between
third-run and fourth-run is attributable to the architecture (SimVP vs
DMVFN), not a confounded data/preprocessing change. The old model is
re-evaluated on this test set (not its original one) for a fair comparison,
same as third-run did.

Reports the same three things per t+1..t+pred_len that third-run's
comparison does:
  - raw peak_dist (k=1)
  - PSR_k5@5
  - position correlation (Pearson r) + the naive continuity reference --
    this is the metric that actually matters: does a flow-warping
    architecture track position any better than SimVP's direct regression?

Appends a dated section to ../RESULTS.md (all 4 prediction steps, not just
the aggregate).
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
THIRD_RUN_TRAIN_DIR = _HERE.parent.parent / "archive-runs" / "third-run" / "train"
# THIRD_RUN_TRAIN_DIR inserted BEFORE fourth-run's train dir: both contain a
# module literally named train.py (SimVP's vs DMVFN's), and `import train`
# resolves to whichever is earlier on sys.path -- fourth-run/train must win
# that collision since this file needs its rollout(), not third-run's.
# simvp.py only exists in THIRD_RUN_TRAIN_DIR so it isn't ambiguous either way.
sys.path.insert(0, str(THIRD_RUN_TRAIN_DIR))
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "train"))

from dataset import TEST_BAGS, TRAIN_BAGS, PSSPWindowDataset
from dmvfn import DMVFN
from metrics import peak_coords, peak_dist, psr_at
from simvp import SimVP  # third-run's copy, added to sys.path above
from train import rollout  # fourth-run's train.py (see path ordering note above)

DATA_DIR = _HERE.parent.parent / "train-data"
ACCESS_MODEL_DIR = _HERE.parent.parent / "access-model"
THIRD_RUN_CHECKPOINT = THIRD_RUN_TRAIN_DIR / "runs" / "lr1e-6" / "best_model.pt"
RUNS_DIR = _HERE.parent / "train" / "runs"
REPORT_PATH = _HERE.parent / "RESULTS.md"

CLIP_LEN, PRED_LEN, SM_RATIO = 10, 4, 0.5
PSR_K, PSR_N = 5, 5.0

REPORT_HEADER = """# fourth-run 结果报告

fourth-run 的单一变量是模型架构：third-run 用的是 SimVP（直接回归整个 clip_len
历史，一次吐出 t+1~t+4），这里换成 DMVFN（Hu et al., CVPR 2023 highlight，
arXiv:2303.09875）——只用最近 2 帧算光流+融合掩码、反向 warp 合成下一帧，多步预测
靠自回归滚动（把自己的输出喂回去）。数据（train-data/train-test 划分/窗口）和
third-run 完全一致，唯一变量是架构本身。旧模型（access-model exp4）和 third-run
的最优 checkpoint（lr1e-6）都在同一个测试集上重新跑，保证三边公平对比。脚本每次
运行会在下面追加一节，不覆盖历史记录。

## 评价指标说明

- **peak_dist (k=1)**：预测声图和真值声图各自找最大值位置，算欧氏距离（64x64 网格
  的格子数）。数值越小越好。
- **PSR_k@n**：k x k 局部均值滤波后再算 peak_dist，看这个距离小于阈值 n 的样本占多少
  比例——是**成功率**，不是平均距离。数值越大越好。这里统一用 k=5, n=5（PSR_k5@5）。
- **位置相关性（Pearson r）**：预测峰值位置（行/列）和真值峰值位置的皮尔逊相关系数
  （行、列分别算完取平均），同时报告朴素连续性基线本身的相关性作为参照天花板。这是
  fourth-run 真正想验证的指标——DMVFN 的 warp 机制是否比 SimVP 的直接回归更能
  跟踪位置变化。
- **baseline（重复最后一帧）**：把输入历史的最后一帧原样当作未来 4 帧的预测，不用
  模型。

---
"""


def load_simvp(path, shape_in, pred_len, simvp_type, device):
    model = SimVP(shape_in, pred_len, model_type=simvp_type).to(device)
    state_dict = torch.load(path, map_location=device, weights_only=True)
    state_dict = {k.replace("module.", ""): v for k, v in state_dict.items()}
    model.load_state_dict(state_dict)
    model.eval()
    return model


def load_dmvfn(path, channels, device):
    model = DMVFN(channels=channels).to(device)
    state_dict = torch.load(path, map_location=device, weights_only=True)
    model.load_state_dict(state_dict)
    model.eval()
    return model


def unblend_last_sm(x: torch.Tensor, sm_ratio: float) -> np.ndarray:
    last_blend, last_gray = x[:, -1, 0], x[:, -1, 1]
    last_sm = (last_blend - (1.0 - sm_ratio) * last_gray) / sm_ratio
    return last_sm.detach().cpu().numpy()


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
    n_batches = 0
    n_windows = 0

    with torch.no_grad():
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            y_np = y.detach().cpu().numpy()[:, :, 0]  # (B,T,H,W)
            tr, tc = peak_coords(y_np)
            for t in range(PRED_LEN):
                true_rows[t].extend(tr[:, t].tolist())
                true_cols[t].extend(tc[:, t].tolist())

            last_sm = unblend_last_sm(x, SM_RATIO)
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


def format_report(pd_means: dict, psr_means: dict, corr_means: dict, n_windows: int, extra_notes: str = "") -> str:
    header = "".join(f" t+{t+1} |" for t in range(PRED_LEN))
    sep = "---|" * (PRED_LEN + 1)

    lines = [
        f"## {time.strftime('%Y-%m-%d %H:%M')} 对比运行",
        "",
        f"测试集：`{TEST_BAGS}`（{n_windows} 个窗口）。训练集：{len(TRAIN_BAGS)} 个 bag"
        f"（78 WordWolfExp + chat_topic1/2，和 third-run 完全一致）。checkpoint：旧 = "
        f"`access-model/weights/config_simvp_exp4.pt`，third-run = "
        f"`{THIRD_RUN_CHECKPOINT.relative_to(REPORT_PATH.parent.parent)}`，fourth-run = "
        f"`{(RUNS_DIR / 'baseline' / 'best_model.pt').relative_to(REPORT_PATH.parent)}`。",
        "",
        "### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)",
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

    lines += [
        "",
        "### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)",
        "",
        f"| |{header} 平均 |",
        f"|---|{sep}",
    ]
    for name, mean in corr_means.items():
        cells = "".join(f" {v:.3f} |" for v in mean)
        lines.append(f"| {name} |{cells} {mean.mean():.3f} |")

    if extra_notes:
        lines += ["", extra_notes]

    lines.append("\n---\n")
    return "\n".join(lines)


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
    old_model = load_simvp(ACCESS_MODEL_DIR / "weights" / "config_simvp_exp4.pt",
                            shape_in, old_cfg["pred_len"], old_cfg["simvp_type"], device)
    third_run_model = load_simvp(THIRD_RUN_CHECKPOINT, shape_in, PRED_LEN, "gsta", device)
    dmvfn_model = load_dmvfn(RUNS_DIR / "baseline" / "best_model.pt", channels=2, device=device)

    models = {
        "old (exp4)": lambda x: old_model(x),
        "third-run (SimVP, lr1e-6)": lambda x: third_run_model(x),
        "fourth-run (DMVFN)": lambda x: rollout(dmvfn_model, x, PRED_LEN)[:, :, 0:1],
        "baseline (repeat-last)": lambda x: baseline_repeat_last(x, PRED_LEN, SM_RATIO),
    }
    pd_means, psr_means, corr_means, n_windows = compute_metrics(models, loader, device)
    print_report(pd_means, psr_means, corr_means)

    notes = ("**注意**：旧模型和third-run模型都是在这个测试集上重新评的，不是各自"
             "原来测试集上的历史数字。DMVFN 用自回归滚动产出 t+1~t+4，其余用直接回归。")
    section = format_report(pd_means, psr_means, corr_means, n_windows, extra_notes=notes)
    is_new = not REPORT_PATH.exists()
    with open(REPORT_PATH, "a") as f:
        if is_new:
            f.write(REPORT_HEADER)
        f.write(section)
    print(f"\nreport {'created' if is_new else 'appended'} -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
