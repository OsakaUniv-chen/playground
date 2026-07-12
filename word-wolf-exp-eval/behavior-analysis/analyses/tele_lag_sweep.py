"""§5.3 Tele の追従ラグ — reimplements analysis2/report-wp4.md's §6.3 (TEL(t)
vs GT(t+lag) tracking-lag curve) from this repo's own 52-bag extraction.

Verbatim from analysis2/code/analyses/wp4.py's run_confusion: for each lag,
build a 2x2 confusion matrix of (tel_side, gt_label shifted by lag) RESTRICTED
to ticks where both are L/R (rows with either NaN/Teleoperator/Others are
DROPPED, not counted as a miss -- this is confusion()'s convention, different
from p_o's "miss" convention used in 5.1/5.2). The report table shows only
the diagonal agreement% per lag (a curve); the full 2x2 matrix is computed
too and printed for the peak lag, to go in the report's appendix.

    python tele_lag_sweep.py
"""
from pathlib import Path

import pandas as pd

TICKS_DIR = Path(__file__).resolve().parent.parent / "results" / "ticks"
LAGS_S = (-3, -2.5, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 2.5, 3, 5, 10)
TICK = 0.25
LR = ["L", "R"]


def lr4(s: pd.Series) -> pd.Series:
    return s.map({"Left": "L", "Right": "R"})


def lr2(s: pd.Series) -> pd.Series:
    return s.map({"left": "L", "right": "R"})


def lagged(df: pd.DataFrame, col: str, lag_ticks: int) -> pd.Series:
    step_ns = 250_000_000
    s = df.set_index("tick_ts")[col]
    vals = s.reindex(df["tick_ts"].to_numpy() + lag_ticks * step_ns).to_numpy()
    return pd.Series(vals, index=df.index)


def confusion(a: pd.Series, b: pd.Series, labels):
    a, b = a.reset_index(drop=True), b.reset_index(drop=True)
    mask = a.notna() & b.notna()
    a, b = a[mask], b[mask]
    n = len(a)
    mat = (pd.crosstab(a, b).reindex(index=labels, columns=labels, fill_value=0)
           if n else pd.DataFrame(0, index=labels, columns=labels))
    agree = (mat.to_numpy().trace() / n) if n else float("nan")
    return mat, agree, n


def pool_tel_vs_gt_lag(bags, lag_s):
    lag_ticks = int(round(lag_s / TICK))
    a_parts, b_parts = [], []
    for df in bags:
        a_parts.append(lr2(df["tel_side"]))
        b_parts.append(lr4(lagged(df, "gt_label", lag_ticks)))
    a = pd.concat(a_parts, ignore_index=True)
    b = pd.concat(b_parts, ignore_index=True)
    return confusion(a, b, LR)


def main():
    bags = [pd.read_parquet(p) for p in sorted(TICKS_DIR.glob("*.parquet"))]
    print(f"bags: {len(bags)}")

    print()
    print("### Tele(t) vs GT(t+lag) — 追従ラグ曲線")
    print("| lag(s) | " + " | ".join(f"{x:g}" for x in LAGS_S) + " |")
    print("|---|" + "---|" * len(LAGS_S))
    row, results = [], {}
    for lag in LAGS_S:
        mat, ag, n = pool_tel_vs_gt_lag(bags, lag)
        row.append(f"{ag*100:.1f}")
        results[lag] = (mat, ag, n)
    print("| 一致% | " + " | ".join(row) + " |")

    peak_lag = max(results, key=lambda k: results[k][1])
    mat, ag, n = results[peak_lag]
    print()
    print(f"### peak lag = {peak_lag}s, agree={ag*100:.1f}%, n={n} -- full 2x2 confusion matrix")
    print("| Tele\\GT(t+lag) | L | R |")
    print("|---|---|---|")
    for idx, r in mat.iterrows():
        print(f"| {idx} | {int(r['L'])} | {int(r['R'])} |")

    # also print lag=0 for reference (same-tick, matches p_o roughly)
    mat0, ag0, n0 = results[0]
    print()
    print(f"### lag=0 (reference), agree={ag0*100:.1f}%, n={n0}")
    print("| Tele\\GT(t) | L | R |")
    print("|---|---|---|")
    for idx, r in mat0.iterrows():
        print(f"| {idx} | {int(r['L'])} | {int(r['R'])} |")


if __name__ == "__main__":
    main()
