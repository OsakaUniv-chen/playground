"""report3/report.md §3. 被注視後の発話開始行動分析

イベント = motors_side（実行層、ロボットの実際の向き）が新しい側へ切り替わり、
その後3秒間（12tick）変わらず同じ側を向き続けた tick。母集団はさらに、その
切り替わりの瞬間に注視された側の人がまだ発話していなかった（gt_label != 注視側）
場合に限定する。

結果指標 = そのイベント後3秒以内（切り替わり直後のtickから12tick先まで）に、
注視された側の人が発話状態になる（gt_label == 注視側）確率。

Usage:
    python gaze_speech_onset.py
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

TICKS_DIR = Path(__file__).resolve().parent.parent / "results" / "ticks"
MODES = ("Tele", "PSSP", "DoA", "Random")
TICK = 0.25
WINDOW_TICKS = 12  # 3.0s
BAG_RE = re.compile(r"^G(?P<group>\d+)_game(?P<game>\d+)_(?P<mode>\w+)$")

SIDE_MAP = {"left": "Left", "right": "Right"}
OPPOSITE = {"Left": "Right", "Right": "Left"}


def load_bags_by_mode():
    bags = {m: [] for m in MODES}
    for p in sorted(TICKS_DIR.glob("*.parquet")):
        for m in MODES:
            if p.stem.endswith(f"_{m}"):
                bags[m].append(pd.read_parquet(p))
                break
    return bags


def gaze_onsets(motors_side: np.ndarray):
    """List of (tick_idx, side) where motors_side transitions INTO 'side'
    (Left/Right)."""
    out = []
    for i in range(1, len(motors_side)):
        m = motors_side[i]
        if m in ("left", "right") and m != motors_side[i - 1]:
            out.append((i, SIDE_MAP[m]))
    return out


def events_for_bag(df: pd.DataFrame):
    """Onsets that (1) hold the same side for the full 3s afterward, and
    (2) the target was not already speaking at the onset tick."""
    motors = df["motors_side"].to_numpy()
    gt = df["gt_label"].to_numpy()
    n = len(df)
    events = []
    for i0, side in gaze_onsets(motors):
        if i0 + WINDOW_TICKS >= n:
            continue
        if not np.all(motors[i0 + 1:i0 + 1 + WINDOW_TICKS] == side.lower()):
            continue
        if gt[i0] == side:
            continue
        events.append((i0, side))
    return events


def t0_breakdown(bags):
    """For this mode's events, what was gt_label AT t0 (the onset tick)?
    Since events already exclude gt[i0]==side (target), it's always one of
    Teleoperator / Others / opposite (the other participant already
    speaking) -- returns {label: pct} out of n_events."""
    counts = {"Teleoperator": 0, "Others": 0, "opposite": 0}
    total = 0
    for df in bags:
        gt = df["gt_label"].to_numpy()
        for i0, side in events_for_bag(df):
            total += 1
            g = gt[i0]
            counts["opposite" if g == OPPOSITE[side] else g] += 1
    if total == 0:
        return {k: float("nan") for k in counts}
    return {k: v / total * 100 for k, v in counts.items()}


def result_for_bags(bags):
    """Target-side hit count, plus the same-event opposite-side (control)
    hit count -- did the person who was NOT looked at start speaking in the
    same window? Also returns the discordant-pair counts (target-only /
    other-only) needed for a paired McNemar test."""
    n_events = 0
    n_hit = 0
    n_hit_other = 0
    n_target_only = 0
    n_other_only = 0
    for df in bags:
        gt = df["gt_label"].to_numpy()
        for i0, side in events_for_bag(df):
            n_events += 1
            window = gt[i0 + 1:i0 + 1 + WINDOW_TICKS]
            t_hit = bool((window == side).any())
            o_hit = bool((window == OPPOSITE[side]).any())
            n_hit += t_hit
            n_hit_other += o_hit
            if t_hit and not o_hit:
                n_target_only += 1
            elif o_hit and not t_hit:
                n_other_only += 1
    p = n_hit / n_events if n_events else float("nan")
    p_other = n_hit_other / n_events if n_events else float("nan")
    return n_events, n_hit, p, n_hit_other, p_other, n_target_only, n_other_only


def per_bag_table():
    """(group, mode) -> (n, hit, p, hit_other, p_other), one row per bag --
    for the group-blocked significance test below."""
    rows = []
    for p in sorted(TICKS_DIR.glob("*.parquet")):
        m = BAG_RE.match(p.stem)
        if m["mode"] not in MODES:
            continue
        df = pd.read_parquet(p)
        n, hit, prob, hit_other, prob_other, _, _ = result_for_bags([df])
        rows.append({"group": int(m["group"]), "mode": m["mode"], "n": n,
                      "hit": hit, "p": prob, "hit_other": hit_other, "p_other": prob_other})
    return pd.DataFrame(rows)


def significance_tests(res: pd.DataFrame):
    """Two checks for whether the 4 modes' hit-probabilities actually differ:
    (1) Friedman test on the 13(group) x 4(mode) per-bag proportion matrix
        (group as the repeated-measures block, matches every other section's
        13-group design);
    (2) plain chi-square of homogeneity on the pooled 4x2 (mode x hit/miss)
        table, ignoring blocking, as a simpler cross-check."""
    from scipy.stats import chi2_contingency, friedmanchisquare

    piv_p = res.pivot(index="group", columns="mode", values="p")[list(MODES)]
    stat, p_friedman = friedmanchisquare(*[piv_p[m].to_numpy() for m in MODES])

    pooled = res.groupby("mode")[["n", "hit"]].sum().loc[list(MODES)]
    pooled["miss"] = pooled["n"] - pooled["hit"]
    chi2, p_chi2, dof, _ = chi2_contingency(pooled[["hit", "miss"]].to_numpy())

    print("\n### 4モード間の差の検定")
    print(f"Friedman検定（13組ブロック、モード間の反復測定）: "
          f"chi2={stat:.2f}, df=3, p={p_friedman:.3f}")
    print(f"プールした4x2表のカイ二乗検定（ブロックなし）: "
          f"chi2={chi2:.2f}, df={dof}, p={p_chi2:.3f}")


def main():
    from scipy.stats import chi2 as chi2_dist

    bags_by_mode = load_bags_by_mode()
    print("bags per mode:", {m: len(v) for m, v in bags_by_mode.items()})

    print("\n### 被注視後3秒以内に発話を開始する確率（対側=同じイベントで見られなかった人）")
    print("n列の内訳 = イベント時（t0）のgt_label（Teleoperator:Others:対側、%）")
    print("| モード | n（イベント数、内訳） | 注視された側 | 対側（対照） |")
    print("|---|---|---|---|")
    all_n, all_hit, all_hit_other = 0, 0, 0
    all_target_only, all_other_only = 0, 0
    all_bags = []
    for mode in MODES:
        n, hit, p, hit_other, p_other, t_only, o_only = result_for_bags(bags_by_mode[mode])
        bd = t0_breakdown(bags_by_mode[mode])
        all_n += n
        all_hit += hit
        all_hit_other += hit_other
        all_target_only += t_only
        all_other_only += o_only
        all_bags += bags_by_mode[mode]
        n_str = (f"{n} (Teleoperator:Others:対側="
                 f"{bd['Teleoperator']:.0f}%:{bd['Others']:.0f}%:{bd['opposite']:.0f}%)")
        print(f"| {mode} | {n_str} | {p*100:.1f}% | {p_other*100:.1f}% |")
    p_all = all_hit / all_n if all_n else float("nan")
    p_all_other = all_hit_other / all_n if all_n else float("nan")
    bd_all = t0_breakdown(all_bags)
    n_all_str = (f"{all_n} (Teleoperator:Others:対側="
                 f"{bd_all['Teleoperator']:.0f}%:{bd_all['Others']:.0f}%:{bd_all['opposite']:.0f}%)")
    print(f"| **全部** | {n_all_str} | {p_all*100:.1f}% | {p_all_other*100:.1f}% |")

    b, c = all_target_only, all_other_only
    stat = (abs(b - c) - 1) ** 2 / (b + c)
    p_mcnemar = float(chi2_dist.sf(stat, df=1))
    print(f"\n注視された側 vs 対側（対応あり、McNemar検定、プール全568件）: "
          f"注視側のみ的中={b}, 対側のみ的中={c}, p={p_mcnemar:.3f}")

    significance_tests(per_bag_table())


if __name__ == "__main__":
    main()
