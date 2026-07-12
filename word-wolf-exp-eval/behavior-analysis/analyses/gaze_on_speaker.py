"""§5.1 発話者注視率 p_o — reimplements analysis2/code/analyses/wp4.py's
run_kappa (10.2 gaze-on-speaker rate) from this repo's own 52-bag extraction.

p_o = P(policy looks at the speaker | a participant (L/R) is the true
speaker). Base = GT in {Left,Right} ticks (same n for every policy); a policy
deciding Teleoperator/Others (or Tele with no face detected) counts as a MISS,
not excluded. PSSP uses pssp_p10 (+1.0s) compared directly against the SAME-
tick gt_label (not shifted) -- verbatim from the original's `dec` mapping.

    python gaze_on_speaker.py
"""
from pathlib import Path

import pandas as pd

TICKS_DIR = Path(__file__).resolve().parent.parent / "results" / "ticks"


def lr4(s: pd.Series) -> pd.Series:
    """4-label Series -> L/R (Teleoperator/Others/None -> NaN)."""
    return s.map({"Left": "L", "Right": "R"})


def lr2(s: pd.Series) -> pd.Series:
    """'left'/'right' Series -> L/R (None -> NaN)."""
    return s.map({"left": "L", "right": "R"})


def lagged(df: pd.DataFrame, col: str, lag_ticks: int) -> pd.Series:
    """Value of df[col] at tick_ts + lag_ticks, within THIS bag only (NaN if
    missing/out of range). Must be called per-bag, not on a concatenated df,
    or ticks would leak across bag boundaries."""
    step_ns = 250_000_000  # 0.25s
    s = df.set_index("tick_ts")[col]
    vals = s.reindex(df["tick_ts"].to_numpy() + lag_ticks * step_ns).to_numpy()
    return pd.Series(vals, index=df.index)


def po_vs_future_gt(bags, pred_col, lag_ticks):
    """p_o of df[pred_col](t) vs the TRUE future gt_label(t+lag_ticks*0.25s),
    computed per-bag (lagged must not cross bag boundaries) then pooled."""
    pred_parts, fut_parts = [], []
    for df in bags:
        fut_parts.append(lr4(lagged(df, "gt_label", lag_ticks)))
        s = df[pred_col]
        pred_parts.append(lr2(s) if s.isin(["left", "right"]).any() else lr4(s))
    fut = pd.concat(fut_parts, ignore_index=True)
    pred = pd.concat(pred_parts, ignore_index=True)
    base = fut.notna()
    n = int(base.sum())
    po = int(((pred == fut) & base).sum()) / n if n else float("nan")
    return po, n


def true_forecast_table(bags):
    """Real forecast accuracy: each PSSP horizon vs ITS OWN matching future
    GT; DoA/Tele/persistence (current decision) vs GT(t+1.0s) as shared
    +1.0s-ahead baselines for comparison -- contrast with §5.1's same-tick
    (not shifted) comparison."""
    horizons = [("PSSP +0.5s", "pssp_p05", 2), ("PSSP +1.0s", "pssp_p10", 4),
                ("PSSP +1.5s", "pssp_p15", 6), ("PSSP +2.0s", "pssp_p20", 8)]
    cols = {}
    n_ref = None
    for name, col, lag in horizons:
        po, n = po_vs_future_gt(bags, col, lag)
        cols[name] = po
        if lag == 4:
            n_ref = n
    for name, col in (("DoA", "doa_label"), ("Tele", "tel_side")):
        po, _ = po_vs_future_gt(bags, col, 4)
        cols[name] = po
    po, _ = po_vs_future_gt(bags, "gt_label", 4)
    cols["持続予測 GT(t)"] = po
    po, _ = po_vs_future_gt(bags, "rand_side", 4)
    cols["Random"] = po

    print()
    print(f"### 真の forecast 精度（各予測をその対象時刻の未来GTと比較。"
          f"DoA/Tele/持続予測はGT+1.0s基準、n≈{n_ref}）")
    print("| 方策 | " + " | ".join(cols.keys()) + " |")
    print("|---|" + "---|" * len(cols))
    print("| p_o | " + " | ".join(f"{v:.3f}" for v in cols.values()) + " |")


def main():
    bags = [pd.read_parquet(p) for p in sorted(TICKS_DIR.glob("*.parquet"))]
    df = pd.concat(bags, ignore_index=True)
    print(f"bags: {len(bags)}  total ticks: {len(df)}")

    gt = lr4(df["gt_label"])
    base = gt.notna()
    n = int(base.sum())

    dec = {
        "GT": gt,
        "DoA": lr4(df["doa_label"]),
        "PSSP +0.5s": lr4(df["pssp_p05"]),
        "PSSP +1.0s": lr4(df["pssp_p10"]),
        "PSSP +1.5s": lr4(df["pssp_p15"]),
        "PSSP +2.0s": lr4(df["pssp_p20"]),
        "Tele": lr2(df["tel_side"]),
        "Random": lr2(df["rand_side"]),
    }

    print()
    print(f"### 発話者注視率 p_o（base = GT∈{{L,R}} ticks, n={n}）")
    print("| 方策 | p_o | n |")
    print("|---|---|---|")
    for pol, s in dec.items():
        po = int(((s == gt) & base).sum()) / n if n else float("nan")
        print(f"| {pol} | {po:.3f} | {n} |")

    true_forecast_table(bags)


if __name__ == "__main__":
    main()
