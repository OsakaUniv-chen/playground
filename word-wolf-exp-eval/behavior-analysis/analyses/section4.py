"""report2/report.md §4. モードの行動分析 — all analysis code for this section,
consolidated into one file so it's not scattered across many scripts.

  §4.1.1 予測がうまくいっているか（GT+1.0s vs PSSP+1.0s）  -> pssp_forecast_accuracy()
  §4.1.2 学習の効果はあったのか（RAND vs PSSP+1.0s）        -> pssp_rand_similarity()
  §4.2.1 遠隔操作とPSSPの類似度（TELE vs PSSP+1.0s）          -> tele_pssp_similarity()
  §4.2.2 遠隔操作とベースライン（TELE vs DoA）                -> tele_doa_similarity()
  §4.2.3 偶然水準の確認（TELE vs RAND）                       -> tele_rand_similarity()
  §4.3   遠隔操作の追従ラグ（TELE vs GT(lag)）                -> tele_gt_lag_sweep()
  §4.4   Randomのベースライン確認（RAND vs GT(t)/GT(t+1.0s)） -> rand_gt_baseline()

Add each new subsection's implementation as its own function below, with a
`# === §4.x.y ... ===` header comment, same pattern as §4.1.1.

Usage:
    python section4.py --part 4.1.1
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

TICKS_DIR = Path(__file__).resolve().parent.parent / "results" / "ticks"
LABELS4 = ["Left", "Right", "Teleoperator", "Others"]


def load_bags():
    return [pd.read_parquet(p) for p in sorted(TICKS_DIR.glob("*.parquet"))]


def _lagged(df: pd.DataFrame, col: str, lag_ticks: int) -> pd.Series:
    step_ns = 250_000_000
    s = df.set_index("tick_ts")[col]
    vals = s.reindex(df["tick_ts"].to_numpy() + lag_ticks * step_ns).to_numpy()
    return pd.Series(vals, index=df.index)


def _confusion(a: pd.Series, b: pd.Series, labels):
    a, b = a.reset_index(drop=True), b.reset_index(drop=True)
    mask = a.notna() & b.notna()
    a, b = a[mask], b[mask]
    n = len(a)
    mat = (pd.crosstab(a, b).reindex(index=labels, columns=labels, fill_value=0)
           if n else pd.DataFrame(0, index=labels, columns=labels))
    agree = (mat.to_numpy().trace() / n) if n else float("nan")
    return mat, agree, n


def _print_precision_recall(mat, labels, row_name, col_name):
    """Per-class precision (row-normalized: of all row==X, how often col==X
    too) and recall (col-normalized: of all col==X, how often row==X too).
    Only worth printing where the marginals are skewed away from uniform --
    see the discussion in report2/report.md §4 on when this adds signal
    beyond the plain agreement rate (skip whenever one side is RAND)."""
    m = mat.to_numpy()
    row_sums = m.sum(axis=1)
    col_sums = m.sum(axis=0)
    print(f"\nprecision（{row_name}側で正規化）／recall（{col_name}側で正規化）:")
    print(f"| ラベル | precision | recall |")
    print("|---|---|---|")
    for i, lab in enumerate(labels):
        prec = m[i, i] / row_sums[i] if row_sums[i] else float("nan")
        rec = m[i, i] / col_sums[i] if col_sums[i] else float("nan")
        print(f"| {lab} | {prec*100:.1f}% | {rec*100:.1f}% |")


# ============================================================================
# 4.1.1 予測がうまくいっているか（GT+1.0s vs PSSP+1.0s）
# ============================================================================
def pssp_forecast_accuracy(bags):
    """Full 4x4 confusion matrix. Base = all ticks where GT(t+1.0s) is
    defined (same population as §3.3). Also reports the L/R-restricted
    accuracy (base = GT(t+1.0s) in {Left,Right} only), matching the
    appendix's 旧5.2 p_o=0.548 for direct comparison."""
    a_parts, b_parts = [], []
    for df in bags:
        a_parts.append(df["pssp_p10"])
        b_parts.append(_lagged(df, "gt_label", 4))
    a = pd.concat(a_parts, ignore_index=True)
    b = pd.concat(b_parts, ignore_index=True)

    mat, agree, n = _confusion(a, b, LABELS4)
    print(f"\n### PSSP+1.0s vs GT(t+1.0s) -- n={n}, agree={agree*100:.1f}%")
    print("| PSSP+1.0s＼GT(t+1.0s) | " + " | ".join(LABELS4) + " |")
    print("|---|" + "---|" * len(LABELS4))
    for idx, row in mat.iterrows():
        print(f"| {idx} | " + " | ".join(str(int(v)) for v in row) + " |")
    _print_precision_recall(mat, LABELS4, "PSSP+1.0s", "GT(t+1.0s)")

    # L/R-restricted accuracy: base = GT(t+1.0s) in {Left,Right} only
    lr_mask = b.isin(["Left", "Right"])
    a_lr, b_lr = a[lr_mask], b[lr_mask]
    n_lr = len(b_lr)
    correct_lr = int(((a_lr == "Left") & (b_lr == "Left")).sum()
                      + ((a_lr == "Right") & (b_lr == "Right")).sum())
    print(f"\nL/R限定精度（母数=GT(t+1.0s)∈{{左,右}}のみ、旧5.2と同じ口径）: "
          f"{correct_lr/n_lr*100:.1f}%  (n={n_lr})")


# ============================================================================
# 4.1.2 学習の効果はあったのか（RAND vs PSSP+1.0s）
# ============================================================================
def pssp_rand_similarity(bags):
    """2x2 confusion matrix, RAND(t) vs PSSP+1.0s(t), same tick (no lag on
    either side -- PSSP+1.0s already encodes the +1s forecast at this row).
    Base = PSSP+1.0s in {Left,Right} only (RAND is L/R-only by construction)."""
    LR = ["Left", "Right"]
    a_parts, b_parts = [], []
    for df in bags:
        a_parts.append(df["rand_side"].map({"left": "Left", "right": "Right"}))
        b_parts.append(df["pssp_p10"])
    a = pd.concat(a_parts, ignore_index=True)
    b = pd.concat(b_parts, ignore_index=True)

    lr_mask = b.isin(LR)
    a, b = a[lr_mask], b[lr_mask]

    mat, agree, n = _confusion(a, b, LR)
    print(f"\n### RAND vs PSSP+1.0s -- n={n}, agree={agree*100:.1f}%")
    print("| RAND＼PSSP+1.0s | 左 | 右 |")
    print("|---|---|---|")
    for idx, row in mat.iterrows():
        print(f"| {idx} | " + " | ".join(str(int(v)) for v in row) + " |")


# ============================================================================
# 4.2.1 遠隔操作とPSSPの類似度（TELE vs PSSP+1.0s）
# ============================================================================
def tele_pssp_similarity(bags):
    """2x2 confusion matrix, TELE(t) vs PSSP+1.0s(t), same tick (no lag on
    either side). Base = PSSP+1.0s in {Left,Right} (Tele, if detected, is
    always Left/Right by construction)."""
    LR = ["Left", "Right"]
    a_parts, b_parts = [], []
    for df in bags:
        a_parts.append(df["tel_side"].map({"left": "Left", "right": "Right"}))
        b_parts.append(df["pssp_p10"])
    a = pd.concat(a_parts, ignore_index=True)
    b = pd.concat(b_parts, ignore_index=True)

    lr_mask = b.isin(LR)
    a, b = a[lr_mask], b[lr_mask]

    mat, agree, n = _confusion(a, b, LR)
    print(f"\n### TELE vs PSSP+1.0s -- n={n}, agree={agree*100:.1f}%")
    print("| TELE＼PSSP+1.0s | 左 | 右 |")
    print("|---|---|---|")
    for idx, row in mat.iterrows():
        print(f"| {idx} | " + " | ".join(str(int(v)) for v in row) + " |")
    # PSSP is the side being evaluated here (does its prediction look like
    # real teleoperator gaze?), so precision is normalized by PSSP (the
    # "predicted" side) and recall by TELE (the "reference" side) -- the
    # opposite convention from 4.2.2/4.3, where TELE is the side being
    # evaluated against a reference (DoA / GT).
    _print_precision_recall(mat.T, LR, "PSSP+1.0s", "TELE")


# ============================================================================
# 4.2.2 遠隔操作とベースライン（TELE vs DoA(0)）
# ============================================================================
def tele_doa_similarity(bags):
    """2x2 confusion matrix, TELE(t) vs DoA(t), same tick. Base = DoA(t) in
    {Left,Right} only."""
    LR = ["Left", "Right"]
    a_parts, b_parts = [], []
    for df in bags:
        a_parts.append(df["tel_side"].map({"left": "Left", "right": "Right"}))
        b_parts.append(df["doa_label"])
    a = pd.concat(a_parts, ignore_index=True)
    b = pd.concat(b_parts, ignore_index=True)

    lr_mask = b.isin(LR)
    a, b = a[lr_mask], b[lr_mask]

    mat, agree, n = _confusion(a, b, LR)
    print(f"\n### TELE vs DoA -- n={n}, agree={agree*100:.1f}%")
    print("| TELE＼DoA | 左 | 右 |")
    print("|---|---|---|")
    for idx, row in mat.iterrows():
        print(f"| {idx} | " + " | ".join(str(int(v)) for v in row) + " |")
    # Same convention as 4.2.1: DoA is the candidate "model" being tested for
    # resemblance to real teleoperator behavior, TELE is the reference/truth
    # -- so precision is normalized by DoA, recall by TELE.
    _print_precision_recall(mat.T, LR, "DoA", "TELE")


# ============================================================================
# 4.2.3 偶然水準の確認（TELE vs RAND）
# ============================================================================
def tele_rand_similarity(bags):
    """2x2 confusion matrix, TELE(t) vs RAND(t), same tick. Base = Tele
    detected (tel_side not null); RAND is always defined."""
    LR = ["Left", "Right"]
    a_parts, b_parts = [], []
    for df in bags:
        a_parts.append(df["tel_side"].map({"left": "Left", "right": "Right"}))
        b_parts.append(df["rand_side"].map({"left": "Left", "right": "Right"}))
    a = pd.concat(a_parts, ignore_index=True)
    b = pd.concat(b_parts, ignore_index=True)

    mat, agree, n = _confusion(a, b, LR)
    print(f"\n### TELE vs RAND -- n={n}, agree={agree*100:.1f}%")
    print("| TELE＼RAND | 左 | 右 |")
    print("|---|---|---|")
    for idx, row in mat.iterrows():
        print(f"| {idx} | " + " | ".join(str(int(v)) for v in row) + " |")


# ============================================================================
# 4.3 遠隔操作の追従ラグ（TELE vs GT(lag)）
# ============================================================================
LAGS_S = (-3, -2.5, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 2.5, 3, 5, 10)
TICK = 0.25


def tele_gt_lag_sweep(bags):
    """2x2 confusion matrix per lag, TELE(t) vs GT(t+lag). Base = Tele
    detected AND GT(t+lag) in {Left,Right} (rows with either NaN or GT in
    {Teleoperator,Others} are dropped). Reimplements tele_lag_sweep.py's
    logic (formerly feeding the appendix's 旧5.3) directly here."""
    LR = ["Left", "Right"]

    def pool(lag_s):
        lag_ticks = int(round(lag_s / TICK))
        a_parts, b_parts = [], []
        for df in bags:
            a_parts.append(df["tel_side"].map({"left": "Left", "right": "Right"}))
            b_parts.append(_lagged(df, "gt_label", lag_ticks))
        a = pd.concat(a_parts, ignore_index=True)
        b = pd.concat(b_parts, ignore_index=True)
        lr_mask = b.isin(LR)
        return _confusion(a[lr_mask], b[lr_mask], LR)

    print("\n### TELE(t) vs GT(t+lag) -- 追従ラグ曲線")
    print("| lag(s) | " + " | ".join(f"{x:g}" for x in LAGS_S) + " |")
    print("|---|" + "---|" * len(LAGS_S))
    row, results = [], {}
    for lag in LAGS_S:
        mat, agree, n = pool(lag)
        row.append(f"{agree*100:.1f}")
        results[lag] = (mat, agree, n)
    print("| 一致率% | " + " | ".join(row) + " |")

    peak_lag = max(results, key=lambda k: results[k][1])
    mat, agree, n = results[peak_lag]
    print(f"\n### peak lag = {peak_lag}s, agree={agree*100:.1f}%, n={n}")
    print("| TELE＼GT(t+lag) | 左 | 右 |")
    print("|---|---|---|")
    for idx, r in mat.iterrows():
        print(f"| {idx} | " + " | ".join(str(int(v)) for v in r) + " |")
    _print_precision_recall(mat, LR, "TELE", f"GT(t{peak_lag:+g}s)")

    mat0, agree0, n0 = results[0]
    print(f"\n### lag=0（参考）, agree={agree0*100:.1f}%, n={n0}")
    print("| TELE＼GT(t) | 左 | 右 |")
    print("|---|---|---|")
    for idx, r in mat0.iterrows():
        print(f"| {idx} | " + " | ".join(str(int(v)) for v in r) + " |")
    _print_precision_recall(mat0, LR, "TELE", "GT(t)")


# ============================================================================
# 4.4 Randomのベースライン確認（RAND vs GT(t) / RAND vs GT(t+1.0s)）
# ============================================================================
def rand_gt_baseline(bags):
    """2x2 confusion matrix x2: RAND(t) vs GT(t), and RAND(t) vs GT(t+1.0s).
    Base = GT(該当時刻) in {Left,Right} only (RAND is L/R-only by
    construction)."""
    LR = ["Left", "Right"]

    def pool(lag_ticks):
        a_parts, b_parts = [], []
        for df in bags:
            a_parts.append(df["rand_side"].map({"left": "Left", "right": "Right"}))
            b_parts.append(df["gt_label"] if lag_ticks == 0
                            else _lagged(df, "gt_label", lag_ticks))
        a = pd.concat(a_parts, ignore_index=True)
        b = pd.concat(b_parts, ignore_index=True)
        lr_mask = b.isin(LR)
        return _confusion(a[lr_mask], b[lr_mask], LR)

    mat0, agree0, n0 = pool(0)
    print(f"\n### RAND vs GT(t) -- n={n0}, agree={agree0*100:.1f}%")
    print("| RAND＼GT(t) | 左 | 右 |")
    print("|---|---|---|")
    for idx, r in mat0.iterrows():
        print(f"| {idx} | " + " | ".join(str(int(v)) for v in r) + " |")

    mat1, agree1, n1 = pool(4)
    print(f"\n### RAND vs GT(t+1.0s) -- n={n1}, agree={agree1*100:.1f}%")
    print("| RAND＼GT(t+1.0s) | 左 | 右 |")
    print("|---|---|---|")
    for idx, r in mat1.iterrows():
        print(f"| {idx} | " + " | ".join(str(int(v)) for v in r) + " |")


# ============================================================================
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", default="4.1.1",
                     choices=["4.1.1", "4.1.2", "4.2.1", "4.2.2", "4.2.3", "4.3", "4.4"])
    args = ap.parse_args()

    if args.part == "4.1.1":
        pssp_forecast_accuracy(load_bags())
    elif args.part == "4.1.2":
        pssp_rand_similarity(load_bags())
    elif args.part == "4.2.1":
        tele_pssp_similarity(load_bags())
    elif args.part == "4.2.2":
        tele_doa_similarity(load_bags())
    elif args.part == "4.2.3":
        tele_rand_similarity(load_bags())
    elif args.part == "4.3":
        tele_gt_lag_sweep(load_bags())
    elif args.part == "4.4":
        rand_gt_baseline(load_bags())
