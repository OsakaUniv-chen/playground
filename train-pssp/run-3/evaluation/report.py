"""run-2 evaluation/report generator. Evaluates one run-2 checkpoint on
train+val/test for THREE domains (chat, wordwolfexp, grpmtg -- see
train/dataset.py) against exp4 + the naive continuity baseline, and appends
FOUR scenario blocks to run-2/RESULTS.md: 总的 (combined, weighted-pooled
across the three domains -- NOT a fresh combined forward pass, see below) +
one block per domain. Each block = two tables (peak_dist, PSR_k5@5) with a
Train column-group then a Val/Test column-group, same layout as run-1's
report.py.

Efficiency note: each domain's train/eval windows are scored exactly ONCE.
The "总的" (combined) row is a window-count-WEIGHTED pool of the three
domains' sums, not a separate forward pass over a freshly concatenated
dataset -- mathematically identical to scoring the literal concatenation,
much cheaper (combined_train alone is 116k+ windows; re-scoring it whole
again after already scoring each domain would roughly double compute for no
new information).

exp4 is a fixed clip_len=10, sm_ratio=0.5, 4-step model -- same handling as
run-1's report.py (build eval sets at the EXPERIMENT's clip_len/sm_ratio,
feed exp4 the last 10 frames sliced from that same window so every model
scores the exact same prediction targets).
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

import dataset as ds_mod
from metrics import peak_dist
from simvp import SimVP

ACCESS_MODEL_DIR = _HERE.parent.parent / "access-model"
RUNS_DIR = _HERE.parent / "train" / "runs"
REPORT_PATH = _HERE.parent / "RESULTS.md"

PSR_K, PSR_N = 5, 5.0
EXP4_CLIP_LEN, EXP4_SM_RATIO = 10, 0.5
EXP4_OFFSETS = [1, 2, 3, 4]
BS = 32  # fixed eval batch size regardless of the experiment's own --bs, for cross-experiment comparability

REPORT_HEADER = """# run-3 结果报告

基础设施沿用run-2（`chat + WordWolfExp + GRP_meeting`121-bag池，见
`archive-runs/old-runs-2/run-2/PLAN.md`"训练/评估集设计"）。run-3新增
`expo_reception_2025`/`indy_teleoperation`两个来源，用`--extra-eval-domain
expo indy`按需评估，永远单独成表，不计入"总的"（同atr1f的处理方式，见
`dataset.py`）。每个实验固定对照exp4+朴素基线，train/val(test)都报，
t+1~t+4分步（t+2为重点），域之间永远分开报告，不只看合并数字（参考旧
CONTEXT.md fifth-run的教训）。见`run-3/PLAN.md`获取完整计划。

---
"""


def load_exp4(device: str):
    cfg = json.load(open(ACCESS_MODEL_DIR / "configs" / "simvp_exp4.json"))
    assert cfg["sm_ratio"] == EXP4_SM_RATIO and cfg["pred_len"] == len(EXP4_OFFSETS)
    shape_in = (EXP4_CLIP_LEN, 2, 64, 64)
    model = SimVP(shape_in, cfg["pred_len"], model_type=cfg["simvp_type"]).to(device)
    state_dict = torch.load(ACCESS_MODEL_DIR / "weights" / "config_simvp_exp4.pt",
                             map_location=device, weights_only=True)
    state_dict = {k.replace("module.", ""): v for k, v in state_dict.items()}
    model.load_state_dict(state_dict)
    model.eval()
    return model


def load_experiment(run_name: str, device: str):
    run_dir = RUNS_DIR / run_name
    cfg = json.load(open(run_dir / "config.json"))
    shape_in = (cfg["clip_len"], 2, 64, 64)
    model = SimVP(shape_in, cfg["pred_len"], model_type=cfg["simvp_type"],
                   N_S=cfg["n_s"], N_T=cfg["n_t"]).to(device)
    state_dict = torch.load(run_dir / "best_model.pt", map_location=device, weights_only=True)
    model.load_state_dict(state_dict)
    model.eval()
    return model, cfg


def baseline_repeat_last(x: torch.Tensor, pred_len: int, sm_ratio: float) -> torch.Tensor:
    last_blend, last_gray = x[:, -1, 0], x[:, -1, 1]
    last_sm = (last_blend - (1.0 - sm_ratio) * last_gray) / sm_ratio
    return last_sm.unsqueeze(1).expand(-1, pred_len, -1, -1)


def build_domain_pair(domain: str, split_kind: str, clip_len: int, sm_ratio_old: float, sm_ratio_new: float,
                       ww_test_group: str):
    """Returns ((loader_old, loader_new), n_windows) for one domain's
    train/eval set, built at both exp4's sm_ratio (old) and the experiment's
    own sm_ratio (new) -- same (bag,start) index order in both, see
    run-1/evaluation/report.py's docstring for why this is safe."""
    if domain == "chat":
        bags, tr_split, ev_split = ds_mod.CHAT_BAGS, "train", "val"
        split = tr_split if split_kind == "train" else ev_split
        ds_old = ds_mod.TimeSplitWindowDataset(bags, split, clip_len=clip_len, sm_ratio=sm_ratio_old,
                                                train_ratio=ds_mod.CHAT_TRAIN_RATIO)
        ds_new = ds_mod.TimeSplitWindowDataset(bags, split, clip_len=clip_len, sm_ratio=sm_ratio_new,
                                                train_ratio=ds_mod.CHAT_TRAIN_RATIO)
    elif domain == "other":
        assert split_kind == "train", "OTHER_BAGS has no held-out counterpart"
        bag_list = ds_mod.OTHER_BAGS
        ds_old = ds_mod.FullBagWindowDataset(bag_list, clip_len=clip_len, sm_ratio=sm_ratio_old)
        ds_new = ds_mod.FullBagWindowDataset(bag_list, clip_len=clip_len, sm_ratio=sm_ratio_new)
        assert len(ds_old) == len(ds_new) and ds_old.index == ds_new.index
        loader_old = DataLoader(ds_old, batch_size=BS, shuffle=False, drop_last=True)
        loader_new = DataLoader(ds_new, batch_size=BS, shuffle=False, drop_last=True)
        return loader_old, loader_new, len(ds_new)
    else:
        bags = {"wordwolfexp": ds_mod.ww_split(ww_test_group),
                "grpmtg": (ds_mod.MTG_TRAIN_BAGS, ds_mod.MTG_TEST_BAGS),
                "atr1f": (ds_mod.OTHER_TRAIN_BAGS_EX_ATR1F, ds_mod.ATR1F_TEST_BAGS),
                "expo": (ds_mod.EXPO_TRAIN_BAGS, ds_mod.EXPO_TEST_BAGS),
                "indy": (ds_mod.INDY_TRAIN_BAGS, ds_mod.INDY_TEST_BAGS)}[domain]
        bag_list = bags[0] if split_kind == "train" else bags[1]
        ds_old = ds_mod.FullBagWindowDataset(bag_list, clip_len=clip_len, sm_ratio=sm_ratio_old)
        ds_new = ds_mod.FullBagWindowDataset(bag_list, clip_len=clip_len, sm_ratio=sm_ratio_new)

    assert len(ds_old) == len(ds_new) and ds_old.index == ds_new.index
    loader_old = DataLoader(ds_old, batch_size=BS, shuffle=False, drop_last=True)
    loader_new = DataLoader(ds_new, batch_size=BS, shuffle=False, drop_last=True)
    return loader_old, loader_new, len(ds_new)


def compute_sums(fns: dict, which_x: dict, loader_old, loader_new, device: str) -> tuple[dict, dict, int]:
    """Returns (pd_sums, psr_sums, n_windows) -- SUMS (not means) over all
    windows, so callers can combine multiple domains by summing sums and
    dividing by total window count (exact weighted pooling, see module
    docstring)."""
    names = list(fns)
    pd_sums = {name: None for name in names}
    psr_sums = {name: None for name in names}
    n_windows = 0

    with torch.no_grad():
        for (x_old, y), (x_new, _) in zip(loader_old, loader_new):
            x_map = {"old": x_old.to(device), "new": x_new.to(device)}
            y = y.to(device)
            y_np = y.detach().cpu().numpy()[:, :, 0]
            bs_actual = y_np.shape[0]

            for name in names:
                out = fns[name](x_map[which_x[name]])
                out_np = out.detach().cpu().numpy() if torch.is_tensor(out) else out
                if out_np.ndim == 5:
                    out_np = out_np[:, :, 0]
                pd = peak_dist(out_np, y_np, k=1).sum(axis=0)  # sum over batch -> (pred_len,)
                psr_frac = (peak_dist(out_np, y_np, k=PSR_K) < PSR_N)  # (B, pred_len) bool via psr_at's filter
                psr_sum = psr_frac.sum(axis=0)
                pd_sums[name] = pd if pd_sums[name] is None else pd_sums[name] + pd
                psr_sums[name] = psr_sum if psr_sums[name] is None else psr_sums[name] + psr_sum

            n_windows += bs_actual

    return pd_sums, psr_sums, n_windows


DISPLAY_NAMES = {"exp4": "exp4", "baseline": "朴素基线"}


def format_table(title: str, unit_fmt, offsets: list[int], model_names: list[str], exp_label: str,
                  train_means: dict, val_means: dict) -> list[str]:
    cols = [DISPLAY_NAMES.get(n, exp_label) for n in model_names]
    header = "| 步长 |" + "".join(f" {c} train |" for c in cols) + "".join(f" {c} val/test |" for c in cols)
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
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--desc", default="")
    ap.add_argument("--extra-eval-domain", nargs="*", default=[],
                     help="score this checkpoint against additional domains not implied by its own training"
                          " config (e.g. 'atr1f') -- reported as their own scenario blocks, train+eval both,"
                          " but excluded from the pooled '总的' row so it stays comparable across runs")
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    exp4 = load_exp4(device)
    experiment, cfg = load_experiment(args.run_name, device)
    pred_len = cfg["pred_len"]
    clip_len = cfg["clip_len"]
    sm_ratio = cfg["sm_ratio"]
    ww_test_group = cfg.get("ww_test_group", "G1")  # older run-name's config.json predates this field
    print(f"checkpoint: {args.run_name}  clip_len={clip_len}  sm_ratio={sm_ratio}  "
          f"N_S={cfg['n_s']}  N_T={cfg['n_t']}  ww_test_group={ww_test_group}")

    def exp4_forward(x):
        return exp4(x[:, -EXP4_CLIP_LEN:])

    fns = {"exp4": exp4_forward, "baseline": lambda x: baseline_repeat_last(x, pred_len, sm_ratio),
           "experiment": lambda x: experiment(x)}
    which_x = {"exp4": "old", "baseline": "new", "experiment": "new"}
    model_names = ["exp4", "baseline", "experiment"]
    exp_label = f"本实验({args.run_name})"

    # per-domain sums -- computed ONCE, reused for the pooled "总的" row.
    # "other" (Phase 4's full-pool extra bags) only has a "train" split -- no
    # held-out counterpart -- so it contributes to the "总的" TRAIN row only.
    use_full_pool = cfg.get("use_full_pool", False)
    domains = ["chat", "wordwolfexp", "grpmtg"] + (["other"] if use_full_pool else [])
    domain_results = {}  # domain -> split_kind -> (pd_sums, psr_sums, n_windows)
    for domain in domains:
        domain_results[domain] = {}
        split_kinds = ["train"] if domain == "other" else ["train", "eval"]
        for split_kind in split_kinds:
            loader_old, loader_new, n = build_domain_pair(domain, split_kind, clip_len, EXP4_SM_RATIO, sm_ratio,
                                                            ww_test_group)
            print(f"=== {domain} [{split_kind}]: {n} windows, {len(loader_new)} batches of {BS} ===")
            pd_sums, psr_sums, n_windows = compute_sums(fns, which_x, loader_old, loader_new, device)
            domain_results[domain][split_kind] = (pd_sums, psr_sums, n_windows)
            print(f"  peak_dist: " + "  ".join(f"{m}={pd_sums[m].sum()/n_windows/pred_len:.2f}" for m in model_names))

    def pooled_means(split_kind: str) -> tuple[dict, dict, int]:
        ds = [d for d in domain_results if split_kind in domain_results[d]]
        total_n = sum(domain_results[d][split_kind][2] for d in ds)
        pd_means = {m: sum(domain_results[d][split_kind][0][m] for d in ds) / total_n for m in model_names}
        psr_means = {m: sum(domain_results[d][split_kind][1][m] for d in ds) / total_n for m in model_names}
        return pd_means, psr_means, total_n

    def domain_means(domain: str, split_kind: str, results: dict = None) -> tuple[dict, dict, int]:
        results = domain_results if results is None else results
        pd_sums, psr_sums, n = results[domain][split_kind]
        return {m: pd_sums[m] / n for m in model_names}, {m: psr_sums[m] / n for m in model_names}, n

    # extra domains (e.g. 'atr1f'): scored the same way as a normal domain
    # (both splits), but kept in a SEPARATE dict so they never leak into
    # pooled_means()'s "总的" row -- this checkpoint may not have trained on
    # them at all (that's the point: same eval path scores both a zero-shot
    # baseline and a run that did train on them).
    extra_domains = [d for d in args.extra_eval_domain if d not in domains]
    extra_results = {}
    for domain in extra_domains:
        extra_results[domain] = {}
        for split_kind in ["train", "eval"]:
            loader_old, loader_new, n = build_domain_pair(domain, split_kind, clip_len, EXP4_SM_RATIO, sm_ratio,
                                                            ww_test_group)
            print(f"=== {domain} [{split_kind}, extra]: {n} windows, {len(loader_new)} batches of {BS} ===")
            pd_sums, psr_sums, n_windows = compute_sums(fns, which_x, loader_old, loader_new, device)
            extra_results[domain][split_kind] = (pd_sums, psr_sums, n_windows)
            print(f"  peak_dist: " + "  ".join(f"{m}={pd_sums[m].sum()/n_windows/pred_len:.2f}" for m in model_names))

    offsets = EXP4_OFFSETS[:pred_len]
    is_new = not REPORT_PATH.exists()
    with open(REPORT_PATH, "a") as f:
        if is_new:
            f.write(REPORT_HEADER)
        f.write(f"## {time.strftime('%Y-%m-%d %H:%M')} {args.run_name}\n\n")
        f.write(f"配置：{args.desc or args.run_name}（clip_len={clip_len}, bs={cfg['bs']}, lr={cfg['lr']}, "
                f"sm_ratio={sm_ratio}, N_S={cfg['n_s']}, N_T={cfg['n_t']}, augment={cfg['augment']}）。\n\n")

        total_title = ("总的（四域按窗口数加权合并，train含other/其余283-bag来源）" if use_full_pool
                       else "总的（三域按窗口数加权合并）")
        scenarios = ([(total_title, None)] + [(dom, dom) for dom in ["chat", "wordwolfexp", "grpmtg"]]
                     + [(f"{dom}（额外评估，不计入总的）", dom) for dom in extra_domains])
        for title, domain in scenarios:
            if domain is None:
                pd_tr, psr_tr, n_tr = pooled_means("train")
                pd_ev, psr_ev, n_ev = pooled_means("eval")
            elif domain in extra_domains:
                pd_tr, psr_tr, n_tr = domain_means(domain, "train", extra_results)
                pd_ev, psr_ev, n_ev = domain_means(domain, "eval", extra_results)
            else:
                pd_tr, psr_tr, n_tr = domain_means(domain, "train")
                pd_ev, psr_ev, n_ev = domain_means(domain, "eval")
            f.write(f"### 场景: {title}\n\ntrain windows={n_tr}, val/test windows={n_ev}\n\n")
            f.writelines(l + "\n" for l in format_table("peak_dist (k=1, 格子数, 64x64网格, 越小越好)",
                                                          lambda v: f"{v:.2f}", offsets, model_names, exp_label,
                                                          pd_tr, pd_ev))
            f.writelines(l + "\n" for l in format_table(f"PSR_k{PSR_K}@{PSR_N:g} (成功率, 越大越好)",
                                                          lambda v: f"{v:.2%}", offsets, model_names, exp_label,
                                                          psr_tr, psr_ev))
        f.write("\n---\n\n")
    print(f"\nreport {'created' if is_new else 'appended'} -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
