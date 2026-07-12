"""report2/report.md §3. 発話の特徴（発話ターン長） — all analysis code for this
section, consolidated into one file so it's not scattered across many scripts.

  §3.1 音響マップ上の連続長（GT）         -> gt_run_turns(), gt_run_hist_plot()
  §3.2 room1全体の発話ターン（room1 VAD）
       - extraction (slow: silero VAD over all 52 bags' room1 audio)
                                          -> true_speech_extract()
       - figures (fast: read the cached extraction output)
                                          -> speaking_ratio_plot(), true_speech_hist_plot()
  §3.3 1秒後話者の変化（GT(t) vs GT(t+1.0s)） -> gt_persistence()

Usage:
    python section3.py                          # runs all FAST parts: 3.1, 3.2 figs, 3.3
    python section3.py --part 3.1
    python section3.py --part 3.2-extract --workers 6   # slow (~mins), only if
                                                          # results/true_speech/ needs (re)building
    python section3.py --part 3.2-plot
    python section3.py --part 3.3
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
LABEL_JA = {"Left": "左", "Right": "右", "Teleoperator": "遠隔者", "Others": "他"}
BAG_RE = re.compile(r"^G(?P<group>\d+)_game(?P<game>\d+)_")
TICK = 0.25

plt.rcParams["font.family"] = "Noto Sans CJK JP"


def load_bags_dict():
    bags = {}
    for p in sorted(TICKS_DIR.glob("*.parquet")):
        bags[p.stem] = pd.read_parquet(p)
    return bags


# ============================================================================
# 3.1 音響マップ上の連続長（GT-run）
# ============================================================================
def _label_runs(seq):
    """Consecutive-equal-label run lengths (in ticks) -> {label: [lengths]}."""
    d = {}
    prev, ln = None, 0
    for x in list(seq) + [None]:
        if x == prev:
            ln += 1
        else:
            if prev is not None:
                d.setdefault(prev, []).append(ln)
            prev, ln = x, 1
    return d


def _run_labels_in_order(seq):
    """Collapse consecutive-equal runs -> [(label, run_len), ...] in order."""
    out = []
    prev, ln = None, 0
    for x in list(seq) + [None]:
        if x == prev:
            ln += 1
        else:
            if prev is not None:
                out.append((prev, ln))
            prev, ln = x, 1
    return out


def gt_run_turns(bags: dict):
    """Turn-length table + >5s breakdown + L/R turn-ending transition, all
    used in report2/report.md §3.1."""
    runs = {}
    for df in bags.values():
        for lab, lens in _label_runs(df["gt_label"]).items():
            runs.setdefault(lab, []).extend(lens)

    print("### GT-run turn length (all bags pooled)")
    print("| ラベル | 平均 ± SD (s) | ターン数 |")
    print("|---|---|---|")
    lr = []
    for lab in LABELS4:
        r = np.array(runs.get(lab, [])) * TICK
        sd = r.std(ddof=1) if len(r) > 1 else 0.0
        print(f"| {lab} | {r.mean():.2f} ± {sd:.2f} | {len(r)} |")
        if lab in ("Left", "Right"):
            lr.extend(r.tolist())
    lr = np.array(lr)
    print(f"| **Left+Right 計** | **{lr.mean():.2f} ± {lr.std(ddof=1):.2f}** | {len(lr)} |")

    print()
    print("### 5s超のターン")
    print("| ラベル | >5s 件数 | 割合 | 最長 |")
    print("|---|---|---|---|")
    for lab in LABELS4:
        r = np.array(runs.get(lab, [])) * TICK
        over = r[r > 5]
        pct = len(over) / len(r) * 100 if len(r) else float("nan")
        mx = over.max() if len(over) else float("nan")
        print(f"| {lab} | {len(over)} | {pct:.1f}% | {mx:.2f} s |")

    # L/R turn-ending transition: when an L/R run ends, where does the next run land?
    dest_counts = {"other_participant": 0, "Teleoperator": 0, "Others": 0}
    total = 0
    for df in bags.values():
        seq = _run_labels_in_order(df["gt_label"])
        for i in range(len(seq) - 1):
            lab, _ = seq[i]
            nxt, _ = seq[i + 1]
            if lab not in ("Left", "Right"):
                continue
            total += 1
            other = "Right" if lab == "Left" else "Left"
            if nxt == other:
                dest_counts["other_participant"] += 1
            elif nxt == "Teleoperator":
                dest_counts["Teleoperator"] += 1
            elif nxt == "Others":
                dest_counts["Others"] += 1
    print()
    print(f"### L/R ターン終了後の遷移先 (n={total})")
    for k, v in dest_counts.items():
        print(f"  {k}: {v} ({v/total*100:.1f}%)")
    non_participant_pct = (dest_counts["Teleoperator"] + dest_counts["Others"]) / total * 100
    print(f"  -> Teleoperator/Others 計: {non_participant_pct:.1f}%")


def room1_vad_crosscheck_supplementary(bags: dict):
    """SUPPLEMENTARY -- not currently cited in report2/report.md. Of the ticks
    labeled Left/Right, what fraction have no real room1 speech underneath
    (per room1_vad.py's silero VAD)? Kept for reference; needs its own room1
    VAD pass per bag (slow-ish, reprocesses room1 audio)."""
    import room1_vad as V

    bag_root = B.resolve_bag_root()
    n_lr = 0
    n_lr_silent = 0
    for bag_name, df in bags.items():
        con = B.open_bag(os.path.join(bag_root, bag_name))
        tick_ts = df["tick_ts"].to_numpy()
        speaking = V.speaking_for_bag(con, tick_ts)
        con.close()
        is_lr = df["gt_label"].isin(("Left", "Right")).to_numpy()
        n_lr += is_lr.sum()
        n_lr_silent += (is_lr & ~speaking).sum()
        print(f"  {bag_name}: n_lr={is_lr.sum()} silent_frac="
              f"{(is_lr & ~speaking).sum() / max(is_lr.sum(),1) * 100:.1f}%", flush=True)
    print()
    print("### room1 VAD cross-check: GT が L/R のとき実際に無音である割合")
    print(f"  n(L/R ticks)={n_lr}  silent={n_lr_silent}  ({n_lr_silent/n_lr*100:.1f}%)")


def gt_run_hist_plot(bags: dict):
    """Small-multiples histogram (one panel/label), for report2/report.md §3.1.
    >5s turns folded into one hatched overflow bar per panel."""
    COLORS = {"Left": "#2a78d6", "Right": "#1baf7a", "Teleoperator": "#eda100", "Others": "#008300"}
    TEXT, MUTED = "#0b0b0b", "#52514e"
    BIN_W, CAP = 0.5, 5.0
    out_path = FIGURES_DIR / "turn_length_hist.png"

    runs = {}
    for df in bags.values():
        for lab, lens in _label_runs(df["gt_label"]).items():
            runs.setdefault(lab, []).extend(lens)

    bins = np.arange(0, CAP + BIN_W, BIN_W)
    fig, axes = plt.subplots(1, 4, figsize=(13, 3.2), dpi=150, sharey=True)

    for ax, lab in zip(axes, LABELS4):
        durs = np.array(runs.get(lab, [])) * TICK
        under = durs[durs <= CAP]
        over_n = int((durs > CAP).sum())

        counts, edges = np.histogram(under, bins=bins)
        centers = (edges[:-1] + edges[1:]) / 2
        ax.bar(centers, counts, width=BIN_W * 0.9, color=COLORS[lab])
        ax.bar(CAP + BIN_W, over_n, width=BIN_W * 0.9, color=COLORS[lab],
               hatch="///", edgecolor="white", linewidth=0.5)

        ax.set_title(f"{LABEL_JA[lab]} (n={len(durs)})", color=TEXT, fontsize=10)
        ax.set_xlabel("ターン長 (s)", color=MUTED, fontsize=8)
        ax.set_xticks([0, 1, 2, 3, 4, 5.5])
        ax.set_xticklabels(["0", "1", "2", "3", "4", "5+"], fontsize=7, color=MUTED)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color("#c3c2b7")
        ax.tick_params(colors=MUTED, labelsize=7)
        ax.set_axisbelow(True)
        ax.yaxis.grid(True, color="#e5e4df", linewidth=0.8)

    axes[0].set_ylabel("ターン数", color=TEXT, fontsize=9)
    fig.suptitle("音響マップ上の発話ターン長（GT-run、5s超は斜線バーに集約）",
                 color=TEXT, fontsize=11, y=1.03)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white", bbox_inches="tight")
    print(f"wrote {out_path}")


# ============================================================================
# 3.2 room1全体の発話ターン（room1 VAD による切分）
# ============================================================================
TRUE_SPEECH_DIR = Path(__file__).resolve().parent.parent / "results" / "true_speech"


def _true_speech_process_bag(bag_name: str, out_dir: Path):
    import room1_vad as V

    df = pd.read_parquet(TICKS_DIR / f"{bag_name}.parquet")
    tick_ts = df["tick_ts"].to_numpy()
    t0_ns = int(tick_ts.min())
    t1_ns = int(tick_ts.max()) + int(0.25e9)  # +1 tick step, matches extract.py's grid

    bag_root = B.resolve_bag_root()
    con = B.open_bag(Path(bag_root) / bag_name)
    mono, clip_t0 = V.load_bag_mono(con, t0_ns, t1_ns)
    con.close()

    duration_s = (t1_ns - t0_ns) / 1e9
    if clip_t0 is None or len(mono) == 0:
        segments = []
    else:
        segments = V.speech_segments(mono, 44100)

    durations = np.asarray([e - s for s, e in segments], dtype=np.float64)
    np.save(out_dir / f"{bag_name}.npy", durations)

    speaking_s = float(durations.sum())
    m = B.DIR_RE.match(bag_name)
    return {
        "bag": bag_name, "group": int(m["group"]), "game": int(m["game"]), "mode": m["mode"],
        "duration_s": round(duration_s, 1),
        "speaking_s": round(speaking_s, 1),
        "speaking_pct": round(speaking_s / duration_s * 100, 1) if duration_s else float("nan"),
        "n_turns": len(durations),
    }


def _true_speech_worker(args):
    bag_name, out_str = args
    t0 = time.time()
    try:
        r = _true_speech_process_bag(bag_name, Path(out_str))
        print(f"  done {r['bag']}: {r['speaking_pct']:.1f}% speaking, "
              f"{r['n_turns']} turns ({time.time()-t0:.0f}s)", flush=True)
        return r
    except Exception as e:  # noqa: BLE001
        print(f"  FAILED {bag_name}: {e}", flush=True)
        return {"bag": bag_name, "error": str(e)}


def true_speech_extract(workers: int = 4):
    """SLOW (~mins): runs silero VAD on room1 audio for all 52 bags, over the
    SAME [t0, t_end] window extract.py used for ticks. Writes
    results/true_speech/summary.csv + {bag}.npy (per-turn durations, s)."""
    TRUE_SPEECH_DIR.mkdir(parents=True, exist_ok=True)
    bags = sorted(p.stem for p in TICKS_DIR.glob("*.parquet"))
    print(f"{len(bags)} bags, workers={workers}")

    jobs = [(b, str(TRUE_SPEECH_DIR)) for b in bags]
    results = []
    if workers > 1:
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
            for r in ex.map(_true_speech_worker, jobs):
                results.append(r)
    else:
        for j in jobs:
            results.append(_true_speech_worker(j))

    ok = [r for r in results if "error" not in r]
    fields = ["bag", "group", "game", "mode", "duration_s", "speaking_s", "speaking_pct", "n_turns"]
    pd.DataFrame(ok)[fields].sort_values("bag").to_csv(TRUE_SPEECH_DIR / "summary.csv", index=False)
    print(f"done: {len(ok)}/{len(bags)} -> {TRUE_SPEECH_DIR}")


def speaking_ratio_plot():
    """Fast: per-game speaking ratio bar chart, for report2/report.md §3.2."""
    BLUE, TEXT, MUTED = "#2a78d6", "#0b0b0b", "#52514e"
    out_path = FIGURES_DIR / "speaking_ratio_by_game.png"

    df = pd.read_csv(TRUE_SPEECH_DIR / "summary.csv")
    parsed = df["bag"].str.extract(BAG_RE).astype(int)
    df = df.assign(group=parsed["group"], game=parsed["game"]) \
           .sort_values(["group", "game"]).reset_index(drop=True)
    print(f"speaking_pct: mean={df.speaking_pct.mean():.1f} std={df.speaking_pct.std():.1f} "
          f"min={df.speaking_pct.min():.1f} max={df.speaking_pct.max():.1f}")

    fig, ax = plt.subplots(figsize=(13, 4), dpi=150)
    x = np.arange(len(df))
    ax.bar(x, df["speaking_pct"], width=0.7, color=BLUE)
    ax.axhline(df["speaking_pct"].mean(), color=TEXT, linewidth=1, linestyle="--")
    ax.text(len(df) - 1, df["speaking_pct"].mean() + 2,
            f"平均 {df['speaking_pct'].mean():.1f}%", color=TEXT, fontsize=9, ha="right")

    group_starts = df.reset_index().groupby("group")["index"].min()
    ax.set_xticks(group_starts.values)
    ax.set_xticklabels([f"G{g}" for g in group_starts.index], color=MUTED, fontsize=8)
    ax.set_xlabel("ゲーム（bag、n=52、時系列順：G1_game3→G13_game6）", color=MUTED, fontsize=9)
    ax.set_ylabel("発話率（room1 VAD、%）", color=TEXT, fontsize=10)
    ax.set_title("ゲームごとの発話率（room1 VADで判定した実際の発話時間の割合）",
                 color=TEXT, fontsize=11)
    ax.set_ylim(0, 100)

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


def true_speech_hist_plot():
    """Fast: pooled true speech-turn duration histogram, for report2/report.md §3.2."""
    BLUE, TEXT, MUTED = "#2a78d6", "#0b0b0b", "#52514e"
    CAP, BIN_W = 6.0, 0.25
    out_path = FIGURES_DIR / "true_speech_turn_hist.png"

    durs = np.concatenate([np.load(p) for p in sorted(TRUE_SPEECH_DIR.glob("*.npy"))])
    print(f"n turns: {len(durs)}  mean={durs.mean():.2f}  std={durs.std(ddof=1):.2f}  "
          f"median={np.median(durs):.2f}  max={durs.max():.2f}")

    under = durs[durs <= CAP]
    over_n = int((durs > CAP).sum())
    bins = np.arange(0, CAP + BIN_W, BIN_W)

    fig, ax = plt.subplots(figsize=(6.5, 4), dpi=150)
    counts, edges = np.histogram(under, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2
    ax.bar(centers, counts, width=BIN_W * 0.9, color=BLUE)
    ax.bar(CAP + BIN_W, over_n, width=BIN_W * 0.9, color=BLUE,
           hatch="///", edgecolor="white", linewidth=0.5)

    ax.axvline(durs.mean(), color=TEXT, linewidth=1, linestyle="--")
    ax.text(durs.mean() + 0.1, ax.get_ylim()[1] * 0.9, f"平均 {durs.mean():.2f}s",
            color=TEXT, fontsize=9)

    ax.set_xticks(list(np.arange(0, CAP + 1, 1)) + [CAP + BIN_W])
    ax.set_xticklabels([str(int(v)) for v in np.arange(0, CAP + 1, 1)] + [f"{CAP:.0f}+"],
                       fontsize=9, color=MUTED)
    ax.set_xlabel("発話ターン長 (s)", color=MUTED, fontsize=10)
    ax.set_ylabel(f"ターン数 (n={len(durs)})", color=TEXT, fontsize=10)
    ax.set_title("室1音声のVAD（silero）による真の発話ターン長", color=TEXT, fontsize=11)

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
# 3.3 1秒後話者の変化（GT(t) vs GT(t+1.0s)）
# ============================================================================
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


def gt_persistence(bags: dict):
    """4x4 confusion matrix, GT(t) vs GT(t+1.0s), for report2/report.md §3.3.
    Base = all ticks where GT(t+1.0s) is defined (does NOT restrict to L/R)."""
    a_parts, b_parts = [], []
    for df in bags.values():
        a_parts.append(df["gt_label"])
        b_parts.append(_lagged(df, "gt_label", 4))
    a = pd.concat(a_parts, ignore_index=True)
    b = pd.concat(b_parts, ignore_index=True)

    mat, agree, n = _confusion(a, b, list(LABELS4))
    print(f"\n### GT(t) vs GT(t+1.0s) -- n={n}, agree={agree*100:.1f}%")
    print("| GT(t)\\GT(t+1.0s) | " + " | ".join(LABELS4) + " |")
    print("|---|" + "---|" * len(LABELS4))
    for idx, row in mat.iterrows():
        print(f"| {idx} | " + " | ".join(str(int(v)) for v in row) + " |")

    m = mat.to_numpy()
    print("\n自己持続率（対角線 / 行合計）:")
    print("| ラベル | 自己持続率 |")
    print("|---|---|")
    for i, lab in enumerate(LABELS4):
        persist = m[i, i] / m[i, :].sum() if m[i, :].sum() else float("nan")
        print(f"| {lab} | {persist:.3f} |")

    # L/R-restricted persistence: base = GT(t+1.0s) in {Left,Right} only
    lr_mask = b.isin(["Left", "Right"])
    a_lr, b_lr = a[lr_mask], b[lr_mask]
    n_lr = len(b_lr)
    same_side = int(((a_lr == "Left") & (b_lr == "Left")).sum()
                     + ((a_lr == "Right") & (b_lr == "Right")).sum())
    print(f"\nL/R限定一致率（母数=GT(t+1.0s)∈{{左,右}}のみ）: "
          f"{same_side/n_lr*100:.1f}%  (n={n_lr})")


# ============================================================================
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", default="all-fast",
                    choices=["all-fast", "3.1", "3.1-hist", "3.1-vad-crosscheck",
                             "3.2-extract", "3.2-plot", "3.3"])
    ap.add_argument("--workers", type=int, default=4, help="for 3.2-extract only")
    args = ap.parse_args()

    if args.part == "3.1":
        gt_run_turns(load_bags_dict())
    elif args.part == "3.1-hist":
        gt_run_hist_plot(load_bags_dict())
    elif args.part == "3.1-vad-crosscheck":
        room1_vad_crosscheck_supplementary(load_bags_dict())
    elif args.part == "3.2-extract":
        true_speech_extract(workers=args.workers)
    elif args.part == "3.2-plot":
        speaking_ratio_plot()
        true_speech_hist_plot()
    elif args.part == "3.3":
        gt_persistence(load_bags_dict())
    else:  # all-fast
        bags = load_bags_dict()
        print("=" * 20, "3.1 GT-run turns", "=" * 20)
        gt_run_turns(bags)
        gt_run_hist_plot(bags)
        print("\n" + "=" * 20, "3.2 true-speech plots (needs results/true_speech/ already built)", "=" * 20)
        speaking_ratio_plot()
        true_speech_hist_plot()
        print("\n" + "=" * 20, "3.3 GT persistence", "=" * 20)
        gt_persistence(bags)
