"""report3/report.md §4. 頭部yaw偏移と問卷得点の相関分析

導師のTODO（2026-07-16 ミーティング、action_items_zh.md #4）の簡単版：
「頭向の中央値/平均とアンケート得点の相関を見る」。

Teleモードの遠隔操作者の頭部yaw（組ごとの中央値・平均、degree、正=左/負=右）
と、その組のTele条件アンケート得点（PTL・GQS5尺度・GEQ5尺度、テレオペレータ
本人を除いた参加者の平均）を組単位（n=13）でSpearman相関する。

Usage:
    python yaw_survey_corr.py
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path
from statistics import mean

import numpy as np
from scipy.stats import spearmanr

# This machine's IPv6 route to Google is a black hole; requests (unlike curl)
# tries the IPv6 addresses first with no Happy-Eyeballs fallback in py3.10,
# so it hangs for minutes before falling back to IPv4. Force IPv4-only DNS.
import urllib3.util.connection as _urllib3_cn  # noqa: E402
_urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

TELE_YAW_DIR = Path(__file__).resolve().parent.parent / "results" / "tele_yaw"
BASIC_RESULTS_DIR = Path(__file__).resolve().parents[2] / "basic-results"
sys.path.insert(0, str(BASIC_RESULTS_DIR))

from post_survey import (  # noqa: E402
    fetch_rows, enrich_with_wolf, drop_teleoperator_rows, build_long_rows,
    GQS_COLS, GEQ_COLS,
)

SCORE_COLS = ["PTL"] + GQS_COLS + GEQ_COLS


def tele_yaw_by_group() -> dict[str, float]:
    """{group: mean yaw (deg)} from the 13 Tele-mode bags."""
    out = {}
    for p in sorted(TELE_YAW_DIR.glob("G*_Tele.npy")):
        group = p.stem.split("_")[0].lstrip("G")
        out[group] = float(np.load(p).mean())
    return out


def survey_by_group() -> dict[str, dict[str, float]]:
    """{group: {score_col: mean over the non-operator raters}} for Tele mode."""
    header, rows = fetch_rows()
    header, rows = enrich_with_wolf(header, rows)
    rows, _dropped = drop_teleoperator_rows(rows)
    long_rows = [r for r in build_long_rows(rows) if r["mode"] == "Tele"]

    by_group: dict[str, list[dict]] = {}
    for r in long_rows:
        by_group.setdefault(r["group"], []).append(r)

    out = {}
    for group, recs in by_group.items():
        out[group] = {
            col: (mean(vals) if (vals := [r[col] for r in recs
                                           if r.get(col) is not None]) else None)
            for col in SCORE_COLS
        }
    return out


def main():
    yaw = tele_yaw_by_group()
    survey = survey_by_group()
    groups = sorted(set(yaw) & set(survey), key=int)
    print(f"groups (n={len(groups)}): {groups}")

    yaw_vals = [yaw[g] for g in groups]
    print(f"\n=== yaw mean (deg, +left/-right) vs Tele survey scores "
          f"(n={len(groups)}) ===")
    print("yaw:", [round(v, 1) for v in yaw_vals])
    print(f"{'scale':<24}{'rho':>8}{'p':>8}")
    for col in SCORE_COLS:
        score_vals = [survey[g].get(col) for g in groups]
        if any(v is None for v in score_vals):
            print(f"{col:<24}{'NA (missing)':>16}")
            continue
        rho, p = spearmanr(yaw_vals, score_vals)
        print(f"{col:<24}{rho:>8.2f}{p:>8.3f}")

    print("\n=== robustness: drop outlier groups (Likeability) ===")
    for drop in ([], ["11", "12"], ["5"], ["11", "12", "5"]):
        idx = [i for i, g in enumerate(groups) if g not in drop]
        yv = [yaw_vals[i] for i in idx]
        sv = [survey[groups[i]]["Likeability"] for i in idx]
        rho, p = spearmanr(yv, sv)
        label = f"drop {drop}" if drop else "none"
        print(f"  {label:<16}(n={len(idx)}): rho={rho:.2f} p={p:.3f}")


if __name__ == "__main__":
    main()
