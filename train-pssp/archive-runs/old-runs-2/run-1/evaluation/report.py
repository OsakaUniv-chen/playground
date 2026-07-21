"""run-1 evaluation/report generator (PLAN.md Phase 0c / "报告格式"). Every
run-1 experiment uses this, not a one-off script -- keeps the comparison
table format identical across experiments.

Evaluates one run-1 checkpoint on BOTH the chat train split and the chat val
split (last 10% of each bag), against two fixed comparison models: exp4 and
the naive continuity baseline (repeat the last input frame). Appends a
section to run-1/RESULTS.md with two tables (peak_dist, PSR_k5@5), each with
a Train column-block (all 3 models) then a Val column-block (all 3 models),
rows = t+1..t+pred_len (t+2 marked as the headline step) -- see PLAN.md
"报告格式".

exp4_new (access-model/predict.py's other EXP_NAMES entry) is DELIBERATELY
NOT included here (owner decision, 2026-07-18): under this pipeline's
preprocessing assumptions (exp-transformed target, sm_ratio=0.5 blended
input -- confirmed correct for exp4 via matching historical numbers exactly)
exp4_new produces degenerate output (peak_dist ~46 on a 64x64 grid, PSR 0%)
-- weights themselves look legitimately trained (no NaN/Inf, normal-scale
stats matching exp4's), so the likely explanation is exp4_new's TRUE
training preprocessing convention differs from exp4's and was never
independently verified (only exp4's was, historically -- see
archive-runs/old-runs-1/CONTEXT.md's normalization saga). Chasing that down
was judged not worth blocking run-1 for; see CONTEXT.md open questions.

exp4 is a fixed clip_len=10, sm_ratio=0.5, 4-step model. The experiment's own
clip_len/sm_ratio can differ (run-1 Phase 2/3 sweep both). To keep every
model scored on the exact same set of prediction targets, both comparison
datasets are built at the EXPERIMENT's clip_len (so window boundaries/count
match exactly): one blended at sm_ratio=0.5 (what exp4 expects) and one at
the experiment's own sm_ratio. Both datasets share the identical (bag,
start) index order (sm_ratio never affects indexing, only the input channel
blend), so zipping the two shuffle=False loaders together yields perfectly
aligned batches -- same `y` from either side, only `x`'s blend differs. exp4
then sees just the last 10 history frames of that window (x[:, -10:]),
matching how it was trained.
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

from dataset import ChatWindowDataset
from metrics import peak_dist, psr_at
from simvp import SimVP

ACCESS_MODEL_DIR = _HERE.parent.parent / "access-model"
RUNS_DIR = _HERE.parent / "train" / "runs"
REPORT_PATH = _HERE.parent / "RESULTS.md"

PSR_K, PSR_N = 5, 5.0
EXP4_CLIP_LEN, EXP4_SM_RATIO = 10, 0.5
EXP4_OFFSETS = [1, 2, 3, 4]
OLD_EXP_NAMES = {"exp4": "simvp_exp4"}  # exp4_new deliberately excluded, see module docstring

REPORT_HEADER = """# run-1 结果报告

chat 数据集（3个bag），按bag内时间轴前90%train/后10%val（不是held-out组，只是
同一段对话内的时间切分，术语见 PLAN.md）。每个实验固定对照 exp4/朴素基线两个
模型，train/val都报，t+1~t+4分步（t+2为重点），不同实验分开出表，不合并平均。
见 PLAN.md "报告格式"。`exp4_new` 未纳入对照——在这套预处理约定下输出明显异常
（peak_dist~46/PSR 0%），真实预处理约定未验证，见 CONTEXT.md 开放问题。

---
"""


def load_old_checkpoint(short_name: str, device: str):
    exp_name = OLD_EXP_NAMES[short_name]
    cfg = json.load(open(ACCESS_MODEL_DIR / "configs" / f"{exp_name}.json"))
    assert cfg["sm_ratio"] == EXP4_SM_RATIO and cfg["pred_len"] == len(EXP4_OFFSETS)
    shape_in = (EXP4_CLIP_LEN, 2, 64, 64)
    model = SimVP(shape_in, cfg["pred_len"], model_type=cfg["simvp_type"]).to(device)
    state_dict = torch.load(ACCESS_MODEL_DIR / "weights" / f"config_{exp_name}.pt",
                             map_location=device, weights_only=True)
    state_dict = {k.replace("module.", ""): v for k, v in state_dict.items()}
    model.load_state_dict(state_dict)
    model.eval()
    return model


def load_experiment_checkpoint(run_name: str, device: str):
    run_dir = RUNS_DIR / run_name
    cfg = json.load(open(run_dir / "config.json"))
    pred_offsets = ([int(o) for o in cfg["pred_offsets"].split(",")] if cfg.get("pred_offsets")
                     else list(range(1, cfg["pred_len"] + 1)))
    shape_in = (cfg["clip_len"], 2, 64, 64)
    model = SimVP(shape_in, len(pred_offsets), model_type=cfg["simvp_type"],
                   N_S=cfg["n_s"], N_T=cfg["n_t"]).to(device)
    state_dict = torch.load(run_dir / "best_model.pt", map_location=device, weights_only=True)
    model.load_state_dict(state_dict)
    model.eval()
    return model, cfg, pred_offsets


def baseline_repeat_last(x: torch.Tensor, pred_len: int, sm_ratio: float) -> torch.Tensor:
    """Undoes the sm_ratio blend to recover the pure last soundmap frame,
    then repeats it pred_len times -- sm_ratio-independent up to float
    rounding (see module docstring), so it's fine to call this on either the
    0.5-blended or experiment-blended input."""
    last_blend, last_gray = x[:, -1, 0], x[:, -1, 1]
    last_sm = (last_blend - (1.0 - sm_ratio) * last_gray) / sm_ratio
    return last_sm.unsqueeze(1).expand(-1, pred_len, -1, -1)


def compute_metrics(fns: dict, which_x: dict, loader_old: DataLoader, loader_new: DataLoader,
                     device: str, pred_len: int):
    names = list(fns)
    pd_sums = {name: None for name in names}
    psr_sums = {name: None for name in names}
    n_batches = n_windows = 0

    with torch.no_grad():
        for (x_old, y), (x_new, _) in zip(loader_old, loader_new):
            x_map = {"old": x_old.to(device), "new": x_new.to(device)}
            y = y.to(device)
            y_np = y.detach().cpu().numpy()[:, :, 0]

            for name in names:
                out = fns[name](x_map[which_x[name]])
                out_np = out.detach().cpu().numpy() if torch.is_tensor(out) else out
                if out_np.ndim == 5:
                    out_np = out_np[:, :, 0]
                pd = peak_dist(out_np, y_np, k=1).mean(axis=0)
                psr = psr_at(out_np, y_np, k=PSR_K, n=PSR_N, sample_axis=0)
                pd_sums[name] = pd if pd_sums[name] is None else pd_sums[name] + pd
                psr_sums[name] = psr if psr_sums[name] is None else psr_sums[name] + psr

            n_batches += 1
            n_windows += x_new.shape[0]

    pd_means = {name: s / n_batches for name, s in pd_sums.items()}
    psr_means = {name: s / n_batches for name, s in psr_sums.items()}
    return pd_means, psr_means, n_windows


DISPLAY_NAMES = {"exp4": "exp4", "exp4_new": "exp4_new", "baseline": "朴素基线"}


def format_table(title: str, unit_fmt, offsets: list[int], model_names: list[str], exp_label: str,
                  train_means: dict, val_means: dict) -> list[str]:
    cols = [DISPLAY_NAMES.get(n, exp_label) for n in model_names]
    header = "| 步长 |" + "".join(f" {c} train |" for c in cols) + "".join(f" {c} val |" for c in cols)
    sep = "|---|" + "---|" * (2 * len(cols))
    lines = [f"### {title}", "", header, sep]
    for t, o in enumerate(offsets):
        label = f"**t+{o}（重点）**" if o == 2 else f"t+{o}"
        row_train = [unit_fmt(train_means[n][t]) for n in model_names]
        row_val = [unit_fmt(val_means[n][t]) for n in model_names]
        lines.append(f"| {label} |" + "".join(f" {v} |" for v in row_train + row_val))
    row_train = [unit_fmt(train_means[n].mean()) for n in model_names]
    row_val = [unit_fmt(val_means[n].mean()) for n in model_names]
    lines.append(f"| 均值 |" + "".join(f" {v} |" for v in row_train + row_val))
    lines.append("")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", required=True, help="train/runs/<name>/best_model.pt to evaluate")
    ap.add_argument("--desc", default="", help="short human-readable description for the RESULTS.md section header")
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    exp4 = load_old_checkpoint("exp4", device)
    experiment, cfg, offsets = load_experiment_checkpoint(args.run_name, device)
    pred_len = len(offsets)
    exp4_idx = [EXP4_OFFSETS.index(o) for o in offsets]
    print(f"checkpoint: {args.run_name}  clip_len={cfg['clip_len']}  sm_ratio={cfg['sm_ratio']}  "
          f"N_S={cfg['n_s']}  N_T={cfg['n_t']}  pred_offsets={offsets}")

    def exp4_forward(x):
        return exp4(x[:, -EXP4_CLIP_LEN:])[:, exp4_idx]

    fns = {
        "exp4": exp4_forward,
        "baseline": lambda x: baseline_repeat_last(x, pred_len, cfg["sm_ratio"]),
        "experiment": lambda x: experiment(x),
    }
    which_x = {"exp4": "old", "baseline": "new", "experiment": "new"}
    model_names = ["exp4", "baseline", "experiment"]
    exp_label = f"本实验({args.run_name})"

    pd_by_split, psr_by_split, n_by_split = {}, {}, {}
    for split in ("train", "val"):
        ds_old = ChatWindowDataset(split, clip_len=cfg["clip_len"], sm_ratio=EXP4_SM_RATIO, pred_offsets=offsets)
        ds_new = ChatWindowDataset(split, clip_len=cfg["clip_len"], sm_ratio=cfg["sm_ratio"], pred_offsets=offsets)
        assert len(ds_old) == len(ds_new) and ds_old.index == ds_new.index, "window index mismatch -- bug"
        loader_old = DataLoader(ds_old, batch_size=32, shuffle=False, drop_last=True)
        loader_new = DataLoader(ds_new, batch_size=32, shuffle=False, drop_last=True)
        print(f"=== {split}: {len(ds_new)} windows, {len(loader_new)} batches of 32 ===")
        pd_means, psr_means, n_windows = compute_metrics(fns, which_x, loader_old, loader_new, device, pred_len)
        pd_by_split[split], psr_by_split[split], n_by_split[split] = pd_means, psr_means, n_windows

        print(f"  peak_dist: " + "  ".join(f"{n}={pd_means[n].mean():.2f}" for n in model_names))
        print(f"  PSR_k{PSR_K}@{PSR_N:g}: " + "  ".join(f"{n}={psr_means[n].mean():.2%}" for n in model_names))

    is_new = not REPORT_PATH.exists()
    with open(REPORT_PATH, "a") as f:
        if is_new:
            f.write(REPORT_HEADER)
        f.write(f"## {time.strftime('%Y-%m-%d %H:%M')} {args.run_name}\n\n")
        f.write(f"配置：{args.desc or args.run_name}（clip_len={cfg['clip_len']}, bs={cfg['bs']}, "
                f"lr={cfg['lr']}, sm_ratio={cfg['sm_ratio']}, N_S={cfg['n_s']}, N_T={cfg['n_t']}, "
                f"augment={cfg['augment']}）。train windows={n_by_split['train']}, val windows={n_by_split['val']}。\n\n")
        f.writelines(l + "\n" for l in format_table("peak_dist (k=1, 格子数, 64x64网格, 越小越好)",
                                                      lambda v: f"{v:.2f}", offsets, model_names, exp_label,
                                                      pd_by_split["train"], pd_by_split["val"]))
        f.writelines(l + "\n" for l in format_table(f"PSR_k{PSR_K}@{PSR_N:g} (成功率, 越大越好)",
                                                      lambda v: f"{v:.2%}", offsets, model_names, exp_label,
                                                      psr_by_split["train"], psr_by_split["val"]))
        f.write("\n---\n\n")
    print(f"\nreport {'created' if is_new else 'appended'} -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
