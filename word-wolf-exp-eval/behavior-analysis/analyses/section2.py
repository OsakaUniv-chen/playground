"""report2/report.md §2. マニピュレーションチェック — all analysis code for this
section, consolidated into one file so it's not scattered across many scripts.

  §2.1 ラベル分布                        -> label_distribution()
  §2.2 Tele のラベル分布が右偏りの検証
       - raw yaw extraction (slow: mediapipe over all 52 bags' room2 video)
                                          -> tele_yaw_extract()
       - box-plot figure (fast: reads the cached extraction output)
                                          -> tele_yaw_plot()
  §2.3 P(switch)                         -> p_switch()

Usage:
    python section2.py                          # runs all FAST parts: 2.1, 2.3, 2.2-plot
    python section2.py --part 2.1
    python section2.py --part 2.2-extract --workers 6   # slow (~mins), only if
                                                          # results/tele_yaw/ needs (re)building
    python section2.py --part 2.2-plot
    python section2.py --part 2.3
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_UTILS_DIR = os.path.join(_HERE, "..", "..", "utils")
if _UTILS_DIR not in sys.path:
    sys.path.insert(0, _UTILS_DIR)

import bag_io as B

TICKS_DIR = Path(__file__).resolve().parent.parent / "results" / "ticks"
FIGURES_DIR = Path(__file__).resolve().parent.parent.parent / "report2" / "figures"
LABELS4 = ("Left", "Right", "Teleoperator", "Others")
BAG_RE = re.compile(r"^G(?P<group>\d+)_game(?P<game>\d+)_")

plt.rcParams["font.family"] = "Noto Sans CJK JP"


def load_bags():
    return [pd.read_parquet(p) for p in sorted(TICKS_DIR.glob("*.parquet"))]


# ============================================================================
# 2.1 ラベル分布
# ============================================================================
# Per-bag % for each label, mean +- SD across the 52 bags (not a single pooled
# percentage), so every policy shows its bag-to-bag variability, not just Tele.
HORIZONS = [("pssp_p05", "+0.5s"), ("pssp_p10", "+1.0s"),
            ("pssp_p15", "+1.5s"), ("pssp_p20", "+2.0s")]


def _per_bag_dist(bags, col, labels):
    per_label_pcts = {lab: [] for lab in labels}
    n_total = 0
    for df in bags:
        s = df[col].dropna()
        n_total += len(s)
        if len(s) == 0:
            continue
        for lab in labels:
            per_label_pcts[lab].append((s == lab).mean() * 100)
    stats = [(np.mean(per_label_pcts[lab]), np.std(per_label_pcts[lab], ddof=1))
             for lab in labels]
    return stats, n_total


def label_distribution(bags):
    print(f"bags: {len(bags)}  total ticks: {sum(len(df) for df in bags)}")

    g, ng = _per_bag_dist(bags, "gt_label", LABELS4)
    d, nd = _per_bag_dist(bags, "doa_label", LABELS4)
    pssp = [_per_bag_dist(bags, col, LABELS4) for col, _ in HORIZONS]
    t, nt = _per_bag_dist(bags, "tel_side", ("left", "right"))
    r, nr = _per_bag_dist(bags, "rand_side", ("left", "right"))

    cols = ["GT", "DoA"] + [hs for _, hs in HORIZONS] + ["Tele", "Random"]
    print()
    print("| ラベル | " + " | ".join(cols) + " |")
    print("|---|" + "---|" * len(cols))
    for i, lab in enumerate(LABELS4):
        row = [f"{g[i][0]:.1f} ± {g[i][1]:.1f}%", f"{d[i][0]:.1f} ± {d[i][1]:.1f}%"]
        row += [f"{pv[i][0]:.1f} ± {pv[i][1]:.1f}%" for pv, _ in pssp]
        if lab == "Left":
            row += [f"{t[0][0]:.1f} ± {t[0][1]:.1f}%", f"{r[0][0]:.1f} ± {r[0][1]:.1f}%"]
        elif lab == "Right":
            row += [f"{t[1][0]:.1f} ± {t[1][1]:.1f}%", f"{r[1][0]:.1f} ± {r[1][1]:.1f}%"]
        else:
            row += ["–", "–"]
        print(f"| {lab} | " + " | ".join(row) + " |")
    n_row = [str(ng), str(nd)] + [str(pn) for _, pn in pssp] + [str(nt), str(nr)]
    print(f"| **n** | " + " | ".join(n_row) + " |")


# ============================================================================
# 2.2 Tele のラベル分布が右偏りの検証
# ============================================================================
TELE_YAW_DIR = Path(__file__).resolve().parent.parent / "results" / "tele_yaw"
TELE_YAW_MODES = ("Tele", "PSSP", "DoA", "Random")


def _tele_yaw_discover(root):
    out = []
    for d in sorted(Path(root).iterdir()):
        mm = B.DIR_RE.match(d.name)
        if d.is_dir() and mm and mm["mode"] in TELE_YAW_MODES:
            out.append(d)
    return out


def _tele_yaw_process_bag(bag_dir: Path, out_dir: Path):
    from head_orientation import HeadOrientationAPI

    con = B.open_bag(bag_dir)
    tid = B.topic_id(con, B.ROOM2_CAMERA_TOPIC)
    rows = con.execute(
        "SELECT timestamp, data FROM messages WHERE topic_id=? ORDER BY timestamp", (tid,)
    ).fetchall()
    con.close()

    api = HeadOrientationAPI()
    try:
        n_decoded = 0
        yaws = []
        for _, data in rows:
            frame = B.decode_compressed_image(data)
            if frame is None:
                continue
            n_decoded += 1
            res = api.detect(frame)
            if res is not None:
                yaws.append(res[1])
    finally:
        api.face_mesh.close()  # avoid GPU/EGL leak across bags in the same worker

    valid = np.asarray(yaws, dtype=np.float32)
    np.save(out_dir / f"{bag_dir.name}.npy", valid)
    m = B.DIR_RE.match(bag_dir.name)
    return {
        "bag": bag_dir.name, "group": int(m["group"]),
        "n_decoded": n_decoded, "n_valid": len(valid),
        "det_rate": len(valid) / n_decoded if n_decoded else float("nan"),
        "yaw_mean": float(valid.mean()) if len(valid) else float("nan"),
        "yaw_std": float(valid.std(ddof=1)) if len(valid) > 1 else float("nan"),
        # HeadOrientationAPI.yaw_to_side: yaw > 0 -> left, so right = yaw < 0.
        "pct_right": float((valid < 0).mean() * 100) if len(valid) else float("nan"),
    }


def _tele_yaw_worker(args):
    bag_str, out_str = args
    t0 = time.time()
    bag_dir = Path(bag_str)
    try:
        r = _tele_yaw_process_bag(bag_dir, Path(out_str))
        print(f"  done {r['bag']}: det={r['det_rate']*100:.1f}% "
              f"mean={r['yaw_mean']:+.1f} std={r['yaw_std']:.1f} "
              f"({time.time()-t0:.0f}s)", flush=True)
        return r
    except Exception as e:  # noqa: BLE001
        print(f"  FAILED {bag_dir.name}: {e}", flush=True)
        return {"bag": bag_dir.name, "error": str(e)}


def tele_yaw_extract(workers: int = 4):
    """SLOW (~mins): re-detects head yaw on every room2 frame of all 52 bags.
    Writes results/tele_yaw/summary.csv + {bag}.npy. Only re-run if that
    directory needs to be rebuilt from scratch."""
    root = B.resolve_bag_root()
    TELE_YAW_DIR.mkdir(parents=True, exist_ok=True)
    bags = _tele_yaw_discover(root)
    print(f"{len(bags)} bags to process, workers={workers}")

    jobs = [(str(b), str(TELE_YAW_DIR)) for b in bags]
    results = []
    if workers > 1:
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
            for r in ex.map(_tele_yaw_worker, jobs):
                results.append(r)
    else:
        for j in jobs:
            results.append(_tele_yaw_worker(j))

    import csv
    ok = [r for r in results if "error" not in r]
    fields = ["bag", "group", "n_decoded", "n_valid", "det_rate", "yaw_mean", "yaw_std", "pct_right"]
    with open(TELE_YAW_DIR / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in sorted(ok, key=lambda r: r["bag"]):
            w.writerow(r)
    print(f"done: {len(ok)}/{len(bags)} bags -> {TELE_YAW_DIR}")


def tele_yaw_plot():
    """Fast: reads results/tele_yaw/ (already extracted) and renders the
    box-plot figure used by report2/report.md §2.2. Sign convention matches
    utils/head_orientation.py's yaw_to_side: yaw > 0 -> left, yaw < 0 -> right."""
    BLUE, RED, TEXT, MUTED = "#2a78d6", "#e34948", "#0b0b0b", "#52514e"
    out_path = FIGURES_DIR / "tele_yaw_bias.png"

    summary = pd.read_csv(TELE_YAW_DIR / "summary.csv")
    print(f"overall detection rate: {summary['n_valid'].sum() / summary['n_decoded'].sum() * 100:.1f}%")

    parsed = summary["bag"].str.extract(BAG_RE).astype(int)
    df = (summary.assign(group=parsed["group"], game=parsed["game"])
          .sort_values(["group", "game"]).reset_index(drop=True))
    yaws = [np.load(TELE_YAW_DIR / f"{bag}.npy") for bag in df["bag"]]
    medians = [np.median(y) for y in yaws]

    fig, ax = plt.subplots(figsize=(13, 4.5), dpi=150)
    x = np.arange(len(df))
    bp = ax.boxplot(yaws, positions=x, widths=0.6, patch_artist=True,
                     showfliers=False, medianprops={"color": TEXT, "linewidth": 1.2},
                     whiskerprops={"color": MUTED}, capprops={"color": MUTED})
    for box, med in zip(bp["boxes"], medians):
        box.set_facecolor(BLUE if med > 0 else RED)
        box.set_edgecolor(MUTED)
        box.set_linewidth(0.6)
    ax.axhline(0, color=TEXT, linewidth=1)

    group_starts = df.reset_index().groupby("group")["index"].min()
    ax.set_xticks(group_starts.values)
    ax.set_xticklabels([f"G{g}" for g in group_starts.index], color=MUTED, fontsize=8)
    ax.set_xlabel(f"操作者（bag、n={len(df)}、時系列順：G1_game3→G13_game6）", color=MUTED, fontsize=9)
    ax.set_ylabel("head yaw (deg)　正=左　負=右", color=TEXT, fontsize=10)
    ax.set_title("遠隔操作者の頭部 yaw 分布：局ごとの箱ひげ図（全52局＝52名、時系列順）",
                 color=TEXT, fontsize=11)

    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED)
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, color="#e5e4df", linewidth=0.8)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white")
    print(f"wrote {out_path}")


# ============================================================================
# 2.3 P(switch)
# ============================================================================
# P(switch) = L/R reversals in motors_side (execution level, the actual
# /boxie/boxie_motors command) / n_ticks -- per-tick reversal probability,
# same units as Random's per-tick flip probability (0.065 in extract.py).
P_SWITCH_MODES = ("Tele", "PSSP", "DoA", "Random")


def _p_switch_for_bag(df: pd.DataFrame):
    seq = df["motors_side"].dropna().tolist()
    n_switch = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
    n_ticks = len(df)
    return n_switch, n_switch / n_ticks


def p_switch():
    rows = []
    for p in sorted(TICKS_DIR.glob("*.parquet")):
        df = pd.read_parquet(p)
        mode = None
        for mm in P_SWITCH_MODES:
            if p.stem.endswith(f"_{mm}"):
                mode = mm
                break
        n_switch, ps = _p_switch_for_bag(df)
        rows.append({"bag": p.stem, "mode": mode, "n_ticks": len(df),
                     "n_switch": n_switch, "p_switch": ps})

    res = pd.DataFrame(rows)
    print(f"bags: {len(res)}")
    print()
    print("| 条件 | P(switch) |")
    print("|---|---|")
    for mode in P_SWITCH_MODES:
        sub = res[res["mode"] == mode]["p_switch"]
        print(f"| {mode} | {sub.mean():.3f} ± {sub.std(ddof=1):.3f} |  (n={len(sub)})")


# ============================================================================
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", default="all-fast",
                    choices=["all-fast", "2.1", "2.2-extract", "2.2-plot", "2.3"])
    ap.add_argument("--workers", type=int, default=4, help="for 2.2-extract only")
    args = ap.parse_args()

    if args.part == "2.1":
        label_distribution(load_bags())
    elif args.part == "2.2-extract":
        tele_yaw_extract(workers=args.workers)
    elif args.part == "2.2-plot":
        tele_yaw_plot()
    elif args.part == "2.3":
        p_switch()
    else:  # all-fast
        print("=" * 20, "2.1 ラベル分布", "=" * 20)
        label_distribution(load_bags())
        print("\n" + "=" * 20, "2.2 Tele yaw plot (needs results/tele_yaw/ already built)", "=" * 20)
        tele_yaw_plot()
        print("\n" + "=" * 20, "2.3 P(switch)", "=" * 20)
        p_switch()
