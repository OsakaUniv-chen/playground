"""Loss-function ablation report: mse vs bce vs kl on the SAME held-out test
set (TEST_BAGS), plus the naive repeat-last-frame baseline (always reported,
per owner instruction -- see CONTEXT.md's "汇报口径" rules).

Reports, per t+1..t+pred_len (never just the aggregate, same rule):
  - peak_dist (k=1) and PSR_k5@5 -- same as first-run, for continuity/
    comparability with that run's numbers.
  - peak_mass and entropy -- the new sharpness diagnostics this run exists to
    investigate (see train.py's module docstring and CONTEXT.md). All three
    loss conditions' outputs are normalized to a proper probability
    distribution (to_prob_dist) before these are computed, so they're
    comparable across conditions despite different native output scales
    (sigmoid [0,1] per-pixel for mse/bce vs raw logits for kl).

Every run appends a dated section to ../RESULTS.md (same convention as
first-run's compare_old_new.py).
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

from dataset import TEST_BAGS, PSSPWindowDataset
from metrics import entropy, peak_dist, peak_mass, psr_at, to_prob_dist
from simvp import SimVP
from train import DATA_DIR, LOSS_OUTPUT_ACTIVATION, PSR_K, PSR_N, RUNS_DIR

REPORT_PATH = _HERE.parent / "RESULTS.md"
CLIP_LEN, PRED_LEN, SM_RATIO = 10, 4, 0.5
LOSSES = ["mse", "bce", "kl"]

REPORT_HEADER = """# second-run 结果报告：loss 函数消融

记录 `second-run/` 这一轮（MSE vs BCE vs KL loss 消融）的所有结果。脚本每次运行会
在下面追加一节，不覆盖历史记录。背景/动机见 `CONTEXT.md`：旧模型（以及 first-run
的 MSE 基线）预测出来的声图比输入/目标明显"模糊"，怀疑是 MSE 在有不确定性时天然
倾向于回归到均值（regression-to-the-mean）导致的，这轮用 BCE、KL（spatial softmax
交叉熵）两种更"概率感知"的 loss 做对照，看能不能缓解这个问题。

## 指标说明（除了 first-run 已有的 peak_dist / PSR_k5@5）

- **peak_mass**：把输出归一化成一个概率分布（和为 1）之后，峰值格子占的概率质量。
  越接近 1 越"自信/尖锐"，越接近 1/4096（64x64 均匀分布）越"模糊"。
- **entropy**：同一个归一化分布的香农熵，越低越集中。
  三种 loss 条件的原始输出量级不同（MSE/BCE 是逐像素 sigmoid，KL 是没归一化的
  logits），计算这两个指标前都先统一做了归一化，所以可以互相比较。
- 上面两个指标只衡量"预测有多尖"，不衡量"尖得对不对地方"——位置准不准还是看
  peak_dist/PSR。一个模型完全可能 peak_dist 很好但整体很模糊（一个宽而低的团刚好
  盖住正确格子），这两个新指标专门用来抓这种情况。
- 朴素基线（重复最后一帧）**每次都必须一起汇报**，同 first-run 的规则。

---
"""


def load_checkpoint(loss_name: str, run_name: str, shape_in, pred_len, simvp_type, device):
    model = SimVP(shape_in, pred_len, model_type=simvp_type,
                  output_activation=LOSS_OUTPUT_ACTIVATION[loss_name]).to(device)
    path = RUNS_DIR / run_name / "best_model.pt"
    state_dict = torch.load(path, map_location=device, weights_only=True)
    state_dict = {k.replace("module.", ""): v for k, v in state_dict.items()}
    model.load_state_dict(state_dict)
    model.eval()
    return model, path


def baseline_repeat_last(x: torch.Tensor, pred_len: int, sm_ratio: float) -> torch.Tensor:
    """x: (B,clip_len,2,H,W) -- ch0=sm_ratio-blended exp(sm), ch1=gray.
    Recovers the last history frame's unblended exp(sm) and repeats it
    pred_len times. Returns (B,pred_len,H,W)."""
    last_blend, last_gray = x[:, -1, 0], x[:, -1, 1]
    last_sm = (last_blend - (1.0 - sm_ratio) * last_gray) / sm_ratio
    return last_sm.unsqueeze(1).expand(-1, pred_len, -1, -1)


def compute_all_metrics(models: dict, loader, device) -> tuple[dict, int]:
    """models: name -> (callable(x)->pred (B,T,1,H,W) or (B,T,H,W), from_logits: bool).
    Returns (metrics_by_name, n_windows), metrics_by_name[name] = dict of (PRED_LEN,) arrays."""
    sums = {name: None for name in models}
    n_batches, n_windows = 0, 0
    with torch.no_grad():
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            y_np = y.detach().cpu().numpy()[:, :, 0]
            y_dist = to_prob_dist(y_np, from_logits=False)

            for name, (fn, from_logits) in models.items():
                out = fn(x)
                out_np = out.detach().cpu().numpy() if torch.is_tensor(out) else out
                if out_np.ndim == 5:
                    out_np = out_np[:, :, 0]
                out_dist = to_prob_dist(out_np, from_logits=from_logits)

                batch = {
                    "peak_dist": peak_dist(out_np, y_np, k=1).mean(axis=0),
                    "psr": psr_at(out_np, y_np, k=PSR_K, n=PSR_N, sample_axis=0),
                    "peak_mass": peak_mass(out_dist).mean(axis=0),
                    "entropy": entropy(out_dist).mean(axis=0),
                }
                if sums[name] is None:
                    sums[name] = batch
                else:
                    sums[name] = {k: sums[name][k] + v for k, v in batch.items()}

            n_batches += 1
            n_windows += x.shape[0]

    means = {name: {k: v / n_batches for k, v in s.items()} for name, s in sums.items()}
    return means, n_windows


def format_report(means: dict, n_windows: int, checkpoint_paths: dict) -> str:
    header = "".join(f" t+{t+1} |" for t in range(PRED_LEN))
    sep = "---|" * (PRED_LEN + 1)

    lines = [
        f"## {time.strftime('%Y-%m-%d %H:%M')} 消融运行",
        "",
        f"测试集：`{TEST_BAGS}`（{n_windows} 个窗口，引用自 `train-data/`）。",
        "checkpoints：" + "，".join(f"{name}=`{p.relative_to(REPORT_PATH.parent)}`" for name, p in checkpoint_paths.items()),
        "",
        "### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)",
        "",
        f"| |{header} 平均 |",
        f"|---|{sep}",
    ]
    for name, m in means.items():
        cells = "".join(f" {v:.2f} |" for v in m["peak_dist"])
        lines.append(f"| {name} |{cells} {m['peak_dist'].mean():.2f} |")

    lines += [
        "",
        f"### PSR_k{PSR_K}@{PSR_N:g} (成功率, 越大越好)",
        "",
        f"| |{header} Aggregate |",
        f"|---|{sep}",
    ]
    for name, m in means.items():
        cells = "".join(f" {v:.2%} |" for v in m["psr"])
        lines.append(f"| {name} |{cells} {m['psr'].mean():.2%} |")

    lines += [
        "",
        "### peak_mass (锐利度：归一化后峰值概率质量, 越接近 1 越尖)",
        "",
        f"| |{header} 平均 |",
        f"|---|{sep}",
    ]
    for name, m in means.items():
        cells = "".join(f" {v:.4f} |" for v in m["peak_mass"])
        lines.append(f"| {name} |{cells} {m['peak_mass'].mean():.4f} |")

    lines += [
        "",
        "### entropy (锐利度：香农熵, nats, 越低越尖)",
        "",
        f"| |{header} 平均 |",
        f"|---|{sep}",
    ]
    for name, m in means.items():
        cells = "".join(f" {v:.3f} |" for v in m["entropy"])
        lines.append(f"| {name} |{cells} {m['entropy'].mean():.3f} |")

    lines.append("\n---\n")
    return "\n".join(lines)


def print_report(means: dict):
    header = "".join(f"t+{t+1:>9}" for t in range(PRED_LEN))
    specs = [
        ("peak_dist", "peak_dist (k=1, lower better)", lambda v: f"{v:9.2f}", lambda v: f"{v:.2f}"),
        ("psr", f"PSR_k{PSR_K}@{PSR_N:g} (higher better)", lambda v: f"{v:9.2%}", lambda v: f"{v:.2%}"),
        ("peak_mass", "peak_mass (higher = sharper)", lambda v: f"{v:9.4f}", lambda v: f"{v:.4f}"),
        ("entropy", "entropy (lower = sharper)", lambda v: f"{v:9.3f}", lambda v: f"{v:.3f}"),
    ]
    for metric_key, label, cell_fmt, agg_fmt in specs:
        print(f"\n{label}:")
        print(f"  {'':>24}{header}   mean")
        for name, m in means.items():
            cells = "".join(cell_fmt(v) for v in m[metric_key])
            print(f"  {name:>24}{cells}   {agg_fmt(m[metric_key].mean())}")


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    shape_in = (CLIP_LEN, 2, 64, 64)

    test_bags = [DATA_DIR / f"{b}.npz" for b in TEST_BAGS]
    test_ds = PSSPWindowDataset(test_bags, clip_len=CLIP_LEN, pred_len=PRED_LEN, sm_ratio=SM_RATIO)
    loader = DataLoader(test_ds, batch_size=32, shuffle=False, drop_last=True)
    print(f"test set: {TEST_BAGS} ({len(test_ds)} windows, {len(loader)} batches of 32)")

    models, checkpoint_paths = {}, {}
    for loss_name in LOSSES:
        model, path = load_checkpoint(loss_name, loss_name, shape_in, PRED_LEN, "gsta", device)
        from_logits = LOSS_OUTPUT_ACTIVATION[loss_name] == "none"
        models[loss_name] = (lambda x, m=model: m(x), from_logits)
        checkpoint_paths[loss_name] = path

    models["baseline (repeat-last)"] = (lambda x: baseline_repeat_last(x, PRED_LEN, SM_RATIO), False)

    means, n_windows = compute_all_metrics(models, loader, device)
    print_report(means)

    section = format_report(means, n_windows, checkpoint_paths)
    is_new = not REPORT_PATH.exists()
    with open(REPORT_PATH, "a") as f:
        if is_new:
            f.write(REPORT_HEADER)
        f.write(section)
    print(f"\nreport {'created' if is_new else 'appended'} -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
