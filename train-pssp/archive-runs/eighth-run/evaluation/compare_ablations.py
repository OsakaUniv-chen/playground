"""Evaluates one eighth-run checkpoint on the fixed 2Hz chat test set
(train/dataset.py -- last 10% of each of chat's 3 bags) and appends a dated
section to ../RESULTS.md. Always reports the naive continuity baseline and
exp4 alongside the run being evaluated (CONTEXT.md: "朴素基线每次都要一起报").

--clip-len must match the checkpoint's training clip_len (10 for Experiment A
and its control; 20 for Experiment B's longer-window arm). The test windows
are built at that clip_len; the NEW model gets the full input, but exp4 is a
fixed clip_len=10 model so it's fed the LAST 10 history frames (x[:, -10:]) --
same current-moment context, same prediction targets, so comparable. The
naive baseline uses only the last input frame, unaffected by clip_len.
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

from dataset import ChatWindowDataset, MixedWindowDataset, CHAT_BAGS, G3_TEST_BAGS
from metrics import peak_dist, psr_at
from simvp import SimVP
from train import PSR_K, PSR_N

ACCESS_MODEL_DIR = _HERE.parent.parent.parent / "access-model"  # archive-runs/ adds one extra level (see CONTEXT.md)
RUNS_DIR = _HERE.parent / "train" / "runs"
REPORT_PATH = _HERE.parent / "RESULTS.md"

EXP4_CLIP_LEN, SM_RATIO = 10, 0.5   # exp4 is a fixed 10-frame model
EXP4_OFFSETS = [1, 2, 3, 4]         # exp4 always predicts these 4 ticks-ahead, fixed at training time

REPORT_HEADER = """# eighth-run 结果报告

只用 chat（3 个 bag，`train_ratio=0.9` 时间切分，后10%测试）做小规模对照，攻七轮
都没解决的核心问题——位置相关性低于朴素基线（见 CONTEXT.md "当前开放问题"）。两个
实验分开做、单一变量：
- **实验A**：loss 监督目标——`--loss mse`（像素重建，历轮都是这个）vs
  `--loss softargmax`（可导 soft-argmax 直接监督峰值位置，见 losses.py）。
- **实验B**：输入窗口长度——`--clip-len 10`（5s）vs `--clip-len 20`（10s）。

其余超参固定在 sixth-run 的 chat 最佳配方（lr=1e-3、noise_ratio 增强）。**早停监控
的是 test peak_dist（不是 test_loss）**，让不同 loss 的对照公平。

## 评价指标说明

同一套指标（peak_dist k=1、PSR_k5@5），同一套朴素连续性基线。**2026-07-16起不再
报"位置相关性"（Pearson r）**——负责人认为其计算方式（把整个测试集所有窗口的预测/
真值峰值坐标拼成两条长向量算相关系数，见旧版报告）不一定合理，之前的历史记录段落
仍保留原样，不回填/不删除。

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


def compute_metrics(models: dict, loader, device, pred_len: int) -> tuple[dict, dict, int]:
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


def print_report(pd_means: dict, psr_means: dict, offsets: list[int]):
    header = "".join(f"t+{o:>7}" for o in offsets)
    print("\npeak_dist (k=1, raw, grid cells on a 64x64 map -- lower is better):")
    print(f"  {'':>32}{header}   mean")
    for name, mean in pd_means.items():
        print(f"  {name:>32}" + "".join(f"{v:9.2f}" for v in mean) + f"   {mean.mean():.2f}")

    print(f"\nPSR_k{PSR_K}@{PSR_N:g} (success rate, higher is better):")
    print(f"  {'':>32}{header}   Aggregate")
    for name, mean in psr_means.items():
        print(f"  {name:>32}" + "".join(f"{v:8.2%}" for v in mean) + f"   {mean.mean():.2%}")


def format_report(run_name: str, args_desc: str, pd_means: dict, psr_means: dict,
                   n_windows: int, offsets: list[int], test_desc: str = "chat 3 个bag各自最后10%") -> str:
    header = "".join(f" t+{o} |" for o in offsets)
    sep = "---|" * (len(offsets) + 1)
    lines = [
        f"## {time.strftime('%Y-%m-%d %H:%M')} {run_name}",
        "",
        f"配置：{args_desc}。测试集：{test_desc}（{n_windows} 个窗口）。",
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

    lines.append("\n---\n")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", required=True, help="train/runs/<name>/best_model.pt to evaluate")
    ap.add_argument("--clip-len", type=int, default=10,
                     help="MUST match the checkpoint's training clip_len (10 or 20)")
    ap.add_argument("--desc", default="", help="short human-readable description of this run's config, "
                                                  "for the RESULTS.md section header")
    ap.add_argument("--dataset", choices=["chat", "chat_g1g2_g3"], default="chat",
                     help="chat=chat-only test set (default). chat_g1g2_g3=checkpoint was trained on"
                          " chat+G1+G2 -- reports chat-test and G3(game3-6)-test SEPARATELY"
                          " (never just a combined average, see CONTEXT.md fifth-run lesson)")
    ap.add_argument("--pred-offsets", type=str, default=None,
                     help="comma-separated ticks-ahead the checkpoint was trained to predict -- MUST match"
                          " the --pred-offsets used at training time (default: 1,2,3,4). exp4 is sliced down"
                          " to the same offsets from its fixed 4-step [1,2,3,4] output for a fair comparison.")
    args = ap.parse_args()
    checkpoint = RUNS_DIR / args.run_name / "best_model.pt"
    offsets = [int(o) for o in args.pred_offsets.split(",")] if args.pred_offsets else [1, 2, 3, 4]
    pred_len = len(offsets)
    exp4_idx = [EXP4_OFFSETS.index(o) for o in offsets]  # exp4 can only be sliced, not retrained

    device = "cuda" if torch.cuda.is_available() else "cpu"
    new_shape_in = (args.clip_len, 2, 64, 64)
    exp4_shape_in = (EXP4_CLIP_LEN, 2, 64, 64)
    print(f"checkpoint: {checkpoint}  pred_offsets={offsets}")

    old_cfg = json.load(open(ACCESS_MODEL_DIR / "configs" / "simvp_exp4.json"))
    assert old_cfg["sm_ratio"] == SM_RATIO and old_cfg["pred_len"] == len(EXP4_OFFSETS)
    old_model = load_checkpoint(ACCESS_MODEL_DIR / "weights" / "config_simvp_exp4.pt",
                                 exp4_shape_in, old_cfg["pred_len"], old_cfg["simvp_type"], device)
    new_model = load_checkpoint(checkpoint, new_shape_in, pred_len, "gsta", device)

    # exp4 is a fixed 10-frame, 4-step model: feed it the last EXP4_CLIP_LEN
    # history frames, then slice its output down to the requested offsets.
    def exp4_forward(x):
        return old_model(x[:, -EXP4_CLIP_LEN:])[:, exp4_idx]

    models = {
        "old (exp4)": exp4_forward,
        f"eighth-run ({args.run_name})": lambda x: new_model(x),
        "baseline (repeat-last)": lambda x: baseline_repeat_last(x, pred_len, SM_RATIO),
    }

    if args.dataset == "chat":
        groups = [("chat", ChatWindowDataset("test", clip_len=args.clip_len, sm_ratio=SM_RATIO,
                                              pred_offsets=offsets),
                   "chat 3 个bag各自最后10%")]
    else:
        chat_test = MixedWindowDataset("test", clip_len=args.clip_len, sm_ratio=SM_RATIO, pred_offsets=offsets,
                                        split_bags=CHAT_BAGS, full_train_bags=[], full_test_bags=[])
        g3_test = MixedWindowDataset("test", clip_len=args.clip_len, sm_ratio=SM_RATIO, pred_offsets=offsets,
                                      split_bags=[], full_train_bags=[], full_test_bags=G3_TEST_BAGS)
        groups = [
            ("chat", chat_test, "chat 3 个bag各自最后10%（训练集里的90%）"),
            ("G3_game3-6", g3_test, "WordWolfExp G3 的 game3/4/5/6（完全held-out，训练集含chat+G1+G2）"),
        ]

    is_new = not REPORT_PATH.exists()
    with open(REPORT_PATH, "a") as f:
        if is_new:
            f.write(REPORT_HEADER)
        for group_name, test_ds, test_desc in groups:
            loader = DataLoader(test_ds, batch_size=32, shuffle=False, drop_last=True)
            print(f"\n=== test group: {group_name} (clip_len={args.clip_len}) ==="
                  f" {len(test_ds)} windows, {len(loader)} batches of 32")
            pd_means, psr_means, n_windows = compute_metrics(models, loader, device, pred_len)
            print_report(pd_means, psr_means, offsets)

            section_name = f"{args.run_name} [{group_name}]" if len(groups) > 1 else args.run_name
            section = format_report(section_name, args.desc or args.run_name, pd_means, psr_means,
                                     n_windows, offsets, test_desc=test_desc)
            f.write(section)
    print(f"\nreport {'created' if is_new else 'appended'} -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
