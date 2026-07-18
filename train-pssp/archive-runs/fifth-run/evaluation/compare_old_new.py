"""Old-vs-new model comparison on the SAME held-out test set (TEST_BAGS =
chat_debate_exp1_topic3 + G13_game3/4/5/6, see dataset.py's docstring for why).

Both models get IDENTICAL input construction (exp(x-max) + sm_ratio blend),
and the SAME SimVP architecture class, so any metric difference is
attributable to the learned weights, not to a preprocessing/architecture
mismatch. The old model is re-evaluated on this new test set (not its
original one) for a fair comparison -- per owner instruction, whenever the
test set changes, the old checkpoint must be re-scored on the SAME set, not
compared against its own historical numbers.

Reports four things per t+1..t+pred_len:
  - raw peak_dist (k=1, mean argmax displacement in grid cells)
  - PSR_k5@5 (success rate: fraction of predictions within 5 cells after a
    5x5 local-mean-filter smoothing)
  - position correlation (Pearson r between predicted and true peak
    row/col, averaged) -- this is the metric this whole line of runs exists
    to move (see CONTEXT.md's position-tracking diagnostic write-up: all of
    first/second-run's models sat BELOW the naive "last-input-position
    predicts next" correlation ceiling, meaning they weren't learning to
    track input at all, not just tracking it imprecisely). Reported for
    old/new AND for that naive continuity reference, so it's clear whether
    fifth-run's much bigger/more-diverse data (vs third-run's 78+2 bags)
    closes that gap.
Always includes a naive "repeat the last observed frame" baseline for
peak_dist/PSR (same as first-run) -- a model that can't beat this isn't
learning anything about the dynamics.

Every run appends a dated section to ../RESULTS.md (all 4 prediction steps,
not just the Aggregate/mean).
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

from dataset import TEST_BAGS, TRAIN_BAGS, TEST_BAG_MIN_START_FRAC, WORDWOLF_RE, PSSPWindowDataset
from metrics import peak_coords, peak_dist, psr_at
from simvp import SimVP
from train import PSR_K, PSR_N

DATA_DIR = _HERE.parent.parent.parent / "train-data"
ACCESS_MODEL_DIR = _HERE.parent.parent.parent / "access-model"
RUNS_DIR = _HERE.parent / "train" / "runs"
REPORT_PATH = _HERE.parent / "RESULTS.md"

CLIP_LEN, PRED_LEN, SM_RATIO = 10, 4, 0.5

REPORT_HEADER = """# fifth-run 结果报告

fifth-run 的单一变量还是数据规模/多样性（第二次测试这个假设，第一次是 third-run）：
训练集从 third-run 的 78 个 WordWolfExp bag + chat 前两段扩到 2026-07-13 PSSPData 重新
整理后的全部 ~278 个 bag（15+ 个来源：WordWolfExp 含合并进去的 EXP1-3/testrun_0420、
GRP_meeting、olab_0630/rev、ATR_teleoperation 两个 RIKEN 集合、chat、Demonstration_Data
(+nonconv)、demo_data_0318_becap、egoSAS_test_data、kitchen，见 CONTEXT.md/
DATA_REPORT.md）。测试集是 chat_debate_exp1_topic3（和 third-run 一致）+ G13 的
game3/4/5/6 四局——**注意 G13_game2_Video 和 G13_interview 仍然留在训练集里，这是负责人
明确要求的选择，会有同组信息泄漏风险（同一批人同一个房间），不是无意的疏漏**，dataset.py
的 `KNOWN_GROUP_OVERLAP` 显式记录了这个例外。SimVP 架构/loss/lr 等超参数和 third-run
完全一致，不重新引入其它变量。旧模型（access-model exp4）在同一个新测试集上重新跑一遍，
不用它原来的历史数字，保证公平对比。脚本每次运行会在下面追加一节，不覆盖历史记录。

## 评价指标说明

- **peak_dist (k=1)**：预测声图和真值声图各自找最大值位置，算欧氏距离（64x64 网格
  的格子数）。数值越小越好。
- **PSR_k@n**：k x k 局部均值滤波后再算 peak_dist，看这个距离小于阈值 n 的样本占多少
  比例——是**成功率**，不是平均距离。数值越大越好。这里统一用 k=5, n=5（PSR_k5@5）。
- **位置相关性（Pearson r）**：预测峰值位置（行/列）和真值峰值位置的皮尔逊相关系数
  （行、列分别算完取平均）。这是 third-run 真正想推动的指标——first/second-run 的
  诊断发现所有模型都低于"直接用输入最后一帧的位置预测下一帧位置"这个朴素连续性
  基线的相关性（也就是说模型根本没学会跟着输入变化去跟踪位置，不只是跟踪得不准）。
  这里同时报告朴素连续性基线本身的相关性作为参照天花板。
- **baseline（重复最后一帧）**：把输入历史的最后一帧原样当作未来 4 帧的预测，不用
  模型。任何真正学到时序动态的模型都应该明显超过这个基线。

---
"""


def load_checkpoint(path, shape_in, pred_len, simvp_type, device):
    model = SimVP(shape_in, pred_len, model_type=simvp_type).to(device)
    state_dict = torch.load(path, map_location=device, weights_only=True)
    state_dict = {k.replace("module.", ""): v for k, v in state_dict.items()}
    model.load_state_dict(state_dict)
    model.eval()
    return model


def unblend_last_sm(x: torch.Tensor, sm_ratio: float) -> np.ndarray:
    """x: (B,clip_len,2,H,W) -- ch0=sm_ratio-blended exp(sm), ch1=gray.
    Recovers the last history frame's unblended exp(sm). Returns (B,H,W)."""
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
    """models: name -> callable(x)->pred tensor (B,T,1,H,W), OR the special
    string "baseline" to use baseline_repeat_last. Returns (pd_means,
    psr_means, corr_means, n_windows). corr_means additionally has a
    "naive continuity" entry independent of any model."""
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
            tr, tc = peak_coords(y_np)  # (B,T)
            for t in range(PRED_LEN):
                true_rows[t].extend(tr[:, t].tolist())
                true_cols[t].extend(tc[:, t].tolist())

            last_sm = unblend_last_sm(x, SM_RATIO)  # (B,H,W)
            lr, lc = peak_coords(last_sm)  # (B,)
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

                pr, pc = peak_coords(out_np)  # (B,T)
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


def format_group_section(group_name: str, pd_means: dict, psr_means: dict, corr_means: dict,
                          n_windows: int, test_bags: list[str]) -> list[str]:
    header = "".join(f" t+{t+1} |" for t in range(PRED_LEN))
    sep = "---|" * (PRED_LEN + 1)

    lines = [
        f"### {group_name}",
        "",
        f"测试集：`{test_bags}`（{n_windows} 个窗口）。",
        "",
        f"#### peak_dist (k=1, 格子数, 64x64 网格, 越小越好)",
        "",
        f"| |{header} 平均 |",
        f"|---|{sep}",
    ]
    for name, mean in pd_means.items():
        cells = "".join(f" {v:.2f} |" for v in mean)
        lines.append(f"| {name} |{cells} {mean.mean():.2f} |")

    lines += [
        "",
        f"#### PSR_k{PSR_K}@{PSR_N:g} (成功率, 越大越好)",
        "",
        f"| |{header} Aggregate |",
        f"|---|{sep}",
    ]
    for name, mean in psr_means.items():
        cells = "".join(f" {v:.2%} |" for v in mean)
        lines.append(f"| {name} |{cells} {mean.mean():.2%} |")

    lines += [
        "",
        f"#### 位置相关性 (Pearson r, predicted vs true peak position, 越接近1越好)",
        "",
        f"| |{header} 平均 |",
        f"|---|{sep}",
    ]
    for name, mean in corr_means.items():
        cells = "".join(f" {v:.3f} |" for v in mean)
        lines.append(f"| {name} |{cells} {mean.mean():.3f} |")

    return lines


def format_report(group_results: list[tuple], new_checkpoint: Path, extra_notes: str = "") -> str:
    """group_results: [(group_name, bag_names, pd_means, psr_means, corr_means, n_windows), ...]
    -- one dated header for the whole run, one ### subsection per group
    (chat / wordwolfexp (G13) / combined), see main()."""
    lines = [
        f"## {time.strftime('%Y-%m-%d %H:%M')} 对比运行",
        "",
        f"训练集：{len(TRAIN_BAGS)} 个 bag（2026-07-13 PSSPData 重跑后的全部集合，见 "
        f"dataset.py/DATA_REPORT.md）。checkpoint：旧 = "
        f"`access-model/weights/config_simvp_exp4.pt`，新 = "
        f"`{new_checkpoint.relative_to(REPORT_PATH.parent)}`。",
        "",
    ]
    for group_name, bag_names, pd_means, psr_means, corr_means, n_windows in group_results:
        lines += format_group_section(group_name, pd_means, psr_means, corr_means, n_windows, bag_names)
        lines.append("")

    if extra_notes:
        lines += [extra_notes, ""]

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


def build_loader(bag_names: list[str]) -> tuple[DataLoader, int]:
    paths = [DATA_DIR / f"{b}.npz" for b in bag_names]
    ds = PSSPWindowDataset(paths, clip_len=CLIP_LEN, pred_len=PRED_LEN, sm_ratio=SM_RATIO,
                           bag_min_start_frac=TEST_BAG_MIN_START_FRAC)
    return DataLoader(ds, batch_size=32, shuffle=False, drop_last=True), len(ds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", default="baseline",
                     help="which train/runs/<name>/best_model.pt to compare (default: baseline)")
    ap.add_argument("--test-bags", default=None,
                     help="comma-separated bag names to use as the test set instead of TEST_BAGS "
                          "(e.g. for checking in-training-distribution performance on a bag that "
                          "WAS in TRAIN_BAGS -- not a real held-out eval, just diagnostic). Reported "
                          "as a single combined group, not split into chat/wordwolfexp.")
    args = ap.parse_args()
    new_checkpoint = RUNS_DIR / args.run_name / "best_model.pt"

    device = "cuda" if torch.cuda.is_available() else "cpu"
    shape_in = (CLIP_LEN, 2, 64, 64)

    if args.test_bags:
        test_bag_names = args.test_bags.split(",")
        groups = [("combined", test_bag_names)]
    else:
        chat_bags = [b for b in TEST_BAGS if not WORDWOLF_RE.match(b)]
        wordwolf_bags = [b for b in TEST_BAGS if WORDWOLF_RE.match(b)]
        # "combined" re-evaluates everything together for a single headline
        # number, in addition to the chat/wordwolfexp breakdown the owner
        # asked for -- chat_debate_exp1_topic3 is trimmed to its last 10%
        # (see TEST_BAG_MIN_START_FRAC), G13's 4 bags are used in full.
        groups = [("chat", chat_bags), ("wordwolfexp (G13)", wordwolf_bags), ("combined", TEST_BAGS)]

    print(f"new checkpoint: {new_checkpoint}")
    if args.test_bags:
        print("NOTE: this bag may be part of TRAIN_BAGS -- this is an in-distribution diagnostic, not a real eval")

    old_cfg = json.load(open(ACCESS_MODEL_DIR / "configs" / "simvp_exp4.json"))
    assert old_cfg["sm_ratio"] == SM_RATIO and old_cfg["pred_len"] == PRED_LEN, \
        "old model config doesn't match this comparison's clip_len/pred_len/sm_ratio"
    old_model = load_checkpoint(ACCESS_MODEL_DIR / "weights" / "config_simvp_exp4.pt",
                                 shape_in, old_cfg["pred_len"], old_cfg["simvp_type"], device)
    new_model = load_checkpoint(new_checkpoint, shape_in, PRED_LEN, "gsta", device)

    models = {
        "old (exp4)": lambda x: old_model(x),
        f"new ({args.run_name})": lambda x: new_model(x),
        "baseline (repeat-last)": lambda x: baseline_repeat_last(x, PRED_LEN, SM_RATIO),
    }

    notes = ("**注意**：旧模型是在这个测试集上重新评的，不是它原来 WordWolfExp holdout "
             "上的历史数字，两者不能直接比较。`chat_debate_exp1_topic3` 只用了每个 bag "
             "时间轴上的最后 10%（`TEST_BAG_MIN_START_FRAC`）——旧模型训练代码"
             "（`access-model-train/utils_all_load.py`）确认过对每个 bag 按 `train_ratio=0.9` "
             "做时间切分，`exp_name=[]` 意味着它当时用了 npz_path 下的全部数据，不确定 "
             "chat 数据当时在不在那个目录里；用最后 10% 可以保证这部分数据无论如何都不是"
             "旧模型训练时见过的。G13 的 4 个测试 bag 确认双方模型都没用来训练，按整段使用。")
    if args.test_bags:
        notes += (f"\n\n**这次测试集在 new 模型的 TRAIN_BAGS 里，"
                   f"是训练集内表现的诊断性对比，不是真正的 held-out 评估**，用来看模型在"
                   f"见过的场景类型上到底能不能学会跟踪位置——如果这里位置相关性依然很低，"
                   f"说明问题不在「没见过这个具体场景」，而在模型本身或训练配比更深层的地方。")

    group_results = []
    for group_name, bag_names in groups:
        loader, n = build_loader(bag_names)
        print(f"\n=== {group_name}: {bag_names} ({n} windows, {len(loader)} batches of 32) ===")
        pd_means, psr_means, corr_means, n_windows = compute_metrics(models, loader, device)
        print_report(pd_means, psr_means, corr_means)
        group_results.append((group_name, bag_names, pd_means, psr_means, corr_means, n_windows))

    section = format_report(group_results, new_checkpoint, extra_notes=notes)
    is_new = not REPORT_PATH.exists()
    with open(REPORT_PATH, "a") as f:
        if is_new:
            f.write(REPORT_HEADER)
        f.write(section)
    print(f"\nreport {'created' if is_new else 'appended'} -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
