"""2026-07-16 owner requirement: report train-set AND test-set peak_dist/PSR_k5@5,
per t+1..t+4 SEPARATELY (t+2 primary, others auxiliary). See
first-run/evaluation/eval_train_test.py for the full rationale. fourth-run's
model (DMVFN) predicts autoregressively via rollout(), unlike SimVP's direct
regression, so its forward wrapper differs from the other runs' scripts.
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

from dataset import TRAIN_BAGS, TEST_BAGS, PSSPWindowDataset
from dmvfn import DMVFN
from metrics import peak_dist, psr_at
from train import rollout

DATA_DIR = _HERE.parent.parent.parent / "train-data"
NEW_CHECKPOINT = _HERE.parent / "train" / "runs" / "baseline" / "best_model.pt"
REPORT_PATH = _HERE.parent / "RESULTS.md"

CLIP_LEN, PRED_LEN, SM_RATIO = 10, 4, 0.5
PSR_K, PSR_N = 5, 5.0


def load_dmvfn(path, channels, device):
    model = DMVFN(channels=channels).to(device)
    state_dict = torch.load(path, map_location=device, weights_only=True)
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


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"

    train_paths = [DATA_DIR / f"{b}.npz" for b in TRAIN_BAGS]
    test_paths = [DATA_DIR / f"{b}.npz" for b in TEST_BAGS]
    train_ds = PSSPWindowDataset(train_paths, clip_len=CLIP_LEN, pred_len=PRED_LEN, sm_ratio=SM_RATIO)
    test_ds = PSSPWindowDataset(test_paths, clip_len=CLIP_LEN, pred_len=PRED_LEN, sm_ratio=SM_RATIO)
    train_loader = DataLoader(train_ds, batch_size=32, shuffle=False, drop_last=True)
    test_loader = DataLoader(test_ds, batch_size=32, shuffle=False, drop_last=True)
    print(f"train: {len(train_ds)} windows ({len(TRAIN_BAGS)} bags), test: {len(test_ds)} windows")

    dmvfn_model = load_dmvfn(NEW_CHECKPOINT, channels=2, device=device)

    models = {
        "fourth-run (DMVFN)": lambda x: rollout(dmvfn_model, x, PRED_LEN)[:, :, 0:1],
        "baseline (repeat-last)": lambda x: baseline_repeat_last(x, PRED_LEN, SM_RATIO),
    }

    train_pd, train_psr, n_train = compute_metrics(models, train_loader, device)
    test_pd, test_psr, n_test = compute_metrics(models, test_loader, device)
    print_report("TRAIN", train_pd, train_psr, n_train)
    print_report("TEST", test_pd, test_psr, n_test)

    lines = [f"## {time.strftime('%Y-%m-%d %H:%M')} train/test 分列复核（新指标口径：逐步不合并，重点t+2）", "",
             f"训练集：`TRAIN_BAGS`（{len(TRAIN_BAGS)} 个 bag，同 third-run）。测试集：`TEST_BAGS`"
             "（chat_debate_exp1_topic3）。自回归滚动模型，t+3/t+4 已知会因误差累积大幅下滑"
             "（见 CONTEXT.md fourth-run 结论）。", ""]
    lines += format_split_tables("TRAIN", train_pd, train_psr, n_train)
    lines += format_split_tables("TEST", test_pd, test_psr, n_test)
    lines.append("\n---\n")

    with open(REPORT_PATH, "a") as f:
        f.write("\n".join(lines))
    print(f"\nappended -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
