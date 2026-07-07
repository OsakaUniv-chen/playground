"""10.3b — VAD-gated speaking-turn length (追加; does NOT replace §10.3 / 10_3_speech.md).

Report §4 flags that GT turn length is an acoustic proxy that *under*-estimates true turns:
during room1 silence the beamformer still emits a Left/Right label, so silence flicker
chops turns into 1-tick fragments. Here we gate each tick by room1_vad (silero, the "strict"
operating point chosen in vad_check/): a tick is a real turn tick only if someone is actually
speaking in room1. VAD-gated turns = runs of gt_label over *speaking* ticks (silence breaks a
run and is dropped); ungated = the original §10.3 definition.

Per-bag room1 speaking is cached to results/room1_vad/<bag>.parquet (tick_ts, speaking) so it
is computed once and can be reused (e.g. merged back by extract.py).

    OPENBLAS_NUM_THREADS=1 python turn_length_vad.py            # all 52 bags
    OPENBLAS_NUM_THREADS=1 python turn_length_vad.py --bags G11_game4_DoA G12_game5_Random
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import bag_io as B          # noqa: E402
import room1_vad as R1      # noqa: E402
from wp4 import LABELS4, mode_of  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TICKS = os.path.join(HERE, "..", "..", "results", "ticks")
CACHE = os.path.join(HERE, "..", "..", "results", "room1_vad")
OUT = os.path.join(HERE, "..", "..", "results", "metrics")
BIN = 0.25
XMAX = 5.0


def runs(seq):
    """[(value, length)] for consecutive-equal runs."""
    s = list(seq)
    out, j = [], 0
    while j < len(s):
        k = j
        while k < len(s) and s[k] == s[j]:
            k += 1
        out.append((s[j], k - j))
        j = k
    return out


def speaking_for(bag, tick_ts, force=False):
    """Per-tick room1 speaking (cached)."""
    os.makedirs(CACHE, exist_ok=True)
    cp = os.path.join(CACHE, f"{bag}.parquet")
    if os.path.exists(cp) and not force:
        c = pd.read_parquet(cp)
        if len(c) == len(tick_ts) and np.array_equal(c["tick_ts"].to_numpy(), tick_ts):
            return c["speaking"].to_numpy().astype(bool)
    con = B.open_bag(os.path.join(B.resolve_bag_root(), bag))
    spk = R1.speaking_for_bag(con, tick_ts)
    con.close()
    pd.DataFrame({"tick_ts": tick_ts, "speaking": spk}).to_parquet(cp, index=False)
    return spk


def dense_segments_for(bag, force=False):
    """Cached silero NATIVE (dense, sample-resolution) speech-segment durations (seconds).

    Unlike the 4 Hz gate, this is not tied to the GT tick grid — it is the actual silero
    speech-segment length, the right resolution for a turn-DURATION distribution.
    """
    os.makedirs(CACHE, exist_ok=True)
    cp = os.path.join(CACHE, f"{bag}_seg.parquet")
    if os.path.exists(cp) and not force:
        c = pd.read_parquet(cp)
        return (c["end_s"] - c["start_s"]).to_numpy() if len(c) else np.zeros(0)
    con = B.open_bag(os.path.join(B.resolve_bag_root(), bag))
    mono, _ = R1.load_bag_mono(con)
    con.close()
    segs = R1.speech_segments(mono, 44100)
    df = (pd.DataFrame(segs, columns=["start_s", "end_s"]) if segs
          else pd.DataFrame({"start_s": [], "end_s": []}))
    df.to_parquet(cp, index=False)
    return (df["end_s"] - df["start_s"]).to_numpy() if len(df) else np.zeros(0)


def stat_line(lengths):
    r = np.asarray(lengths, float) * BIN
    if not len(r):
        return "—", r
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    return f"{r.mean():.2f} ± {sd:.2f} (med {np.median(r):.2f}, n {len(r)})", r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bags", nargs="*", help="bag names (default: all in ticks dir)")
    ap.add_argument("--force", action="store_true", help="recompute VAD cache")
    ap.add_argument("--ticks", default=TICKS)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    paths = sorted(f for f in os.listdir(args.ticks) if f.endswith(".parquet"))
    bags = [p[:-8] for p in paths]
    if args.bags:
        bags = [b for b in bags if b in set(args.bags)]
    print(f"{len(bags)} bags")

    un = {lab: [] for lab in LABELS4}       # ungated turn lengths (ticks)
    ga = {lab: [] for lab in LABELS4}       # VAD-gated turn lengths (ticks)
    dense_seg = []                          # silero native speech-segment durations (s)
    n_tot = n_spk = 0
    lr_tot = lr_silent = 0                  # L/R ticks and how many fall in silence
    by_cond = {}                            # mode -> [tot, spk]
    skipped = []
    for i, bag in enumerate(bags):
        df = pd.read_parquet(os.path.join(args.ticks, f"{bag}.parquet")).sort_values(
            "tick_ts").reset_index(drop=True)
        tick_ts = df["tick_ts"].to_numpy(np.int64)
        gt = df["gt_label"].tolist()
        try:
            spk = speaking_for(bag, tick_ts, args.force)
            dseg = dense_segments_for(bag, args.force)
        except Exception as e:                # e.g. malformed .db3 read from the SSD
            skipped.append(bag)
            print(f"  [{i+1}/{len(bags)}] {bag}: SKIP ({type(e).__name__}: {e})", flush=True)
            continue
        n_tot += len(gt); n_spk += int(spk.sum())
        c = by_cond.setdefault(mode_of(bag), [0, 0])
        c[0] += len(gt); c[1] += int(spk.sum())

        gated = [lab if s else None for lab, s in zip(gt, spk)]
        for lab, ln in runs(gt):
            if lab in un:
                un[lab].append(ln)
        for lab, ln in runs(gated):
            if lab in ga:
                ga[lab].append(ln)
        dense_seg.extend(dseg.tolist())
        gt_arr = np.array(gt, dtype=object)
        is_lr = np.isin(gt_arr, ["Left", "Right"])
        lr_tot += int(is_lr.sum())
        lr_silent += int((is_lr & ~spk).sum())
        print(f"  [{i+1}/{len(bags)}] {bag}: speaking {100*spk.mean():.0f}%", flush=True)

    # ---- markdown ----
    n_ok = len(bags) - len(skipped)
    skip_note = (f" {len(skipped)} bag(s) skipped (unreadable .db3): "
                 f"{', '.join(skipped)}." if skipped else "")
    L = ["## 10.3b VAD-gated speaking-turn length (追加 — silero room1 gate)", "",
         f"Does **not** replace §10.3. Room1 speech gate = silero-vad strict "
         f"(threshold {R1.THRESHOLD}, ratio {R1.RATIO}, window [t-{R1.WIN_S:.2f}s, t], "
         f"hangover {R1.HANGOVER_TICKS} tick). A tick is a turn tick only if someone is "
         f"really speaking in room1; silence breaks a run and is dropped. Pooled over "
         f"{n_ok} bags.{skip_note}", "",
         "### Room1 speaking coverage",
         f"Overall speaking = **{100*n_spk/n_tot:.0f}%** of ticks (n={n_tot}). "
         f"Of ungated GT=Left/Right ticks, **{100*lr_silent/lr_tot:.0f}%** "
         f"({lr_silent}/{lr_tot}) fall in room1 silence — spurious acoustic flicker the "
         f"gate removes.", "",
         "| condition | speaking % | ticks |", "|---|---|---|"]
    for m in ("Tele", "PSSP", "DoA", "Random"):
        if m in by_cond:
            tot, sp = by_cond[m]
            L.append(f"| {m} | {100*sp/tot:.0f}% | {tot} |")

    L += ["", "### Speaking-turn duration per label — ungated vs VAD-gated",
          "mean ± SD (median, n turns), seconds.", "",
          "| label | ungated | VAD-gated | Δ mean |", "|---|---|---|---|"]
    for lab in LABELS4:
        su, ru = stat_line(un[lab])
        sg, rg = stat_line(ga[lab])
        dm = (rg.mean() - ru.mean()) if len(ru) and len(rg) else float("nan")
        L.append(f"| {lab} | {su} | {sg} | {dm:+.2f} |")
    lu = np.array(un["Left"] + un["Right"], float) * BIN
    lg = np.array(ga["Left"] + ga["Right"], float) * BIN
    L += ["", f"**Left+Right (facing participants)**: ungated "
          f"{lu.mean():.2f} ± {lu.std(ddof=1):.2f} s (median {np.median(lu):.2f}, n {len(lu)}) "
          f"→ VAD-gated **{lg.mean():.2f} ± {lg.std(ddof=1):.2f} s** "
          f"(median {np.median(lg):.2f}, n {len(lg)}).", ""]

    seg = np.asarray(dense_seg, float)
    L += ["### Room1-VAD speaking-turn duration (silero native segments) — overall VAD feature",
          "One turn = one uninterrupted room1 speech episode from the room1 VAD (gate on→off), "
          "independent of who / of the beamformed label. **Uses silero's native "
          "sample-resolution segments** (not the 4 Hz gate), so turn boundaries are not "
          "quantized to 0.25 s — the right resolution for a duration distribution. "
          "Silence-robust, speaker-agnostic (histogram: `room1_vad_segment_hist.png`).",
          f"\nmean **{seg.mean():.2f} ± {seg.std(ddof=1):.2f} s**, median {np.median(seg):.2f}, "
          f"p90 {np.percentile(seg,90):.2f}, min {seg.min():.2f}, max {seg.max():.2f}, "
          f"n {len(seg)} turns.", ""]

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "10_3b_speech_vadgated.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    print("wrote 10_3b_speech_vadgated.md")

    # ---- comparison histogram (ungated outline vs gated filled), per label ----
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    bins = np.arange(0, XMAX + BIN, BIN)
    colors = {"Left": "#1f77b4", "Right": "#ff7f0e",
              "Teleoperator": "#2ca02c", "Others": "#9467bd"}
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.5), sharex=True)
    for ax, lab in zip(axes.ravel(), LABELS4):
        ru = np.array(un[lab], float) * BIN
        rg = np.array(ga[lab], float) * BIN
        ax.hist(np.clip(ru, 0, XMAX), bins=bins, color="0.7", edgecolor="white",
                alpha=0.9, label=f"ungated (mean {ru.mean():.2f}s, n {len(ru)})")
        ax.hist(np.clip(rg, 0, XMAX), bins=bins, histtype="step", lw=2,
                color=colors[lab], label=f"VAD-gated (mean {rg.mean():.2f}s, n {len(rg)})")
        ax.axvline(ru.mean(), color="0.4", lw=1.5)
        ax.axvline(rg.mean(), color=colors[lab], lw=2)
        ax.set_title(lab, fontsize=11)
        ax.set_xlabel("turn length (s)")
        ax.set_ylabel("count")
        ax.legend(fontsize=8)
    fig.suptitle("Speaking-turn length: ungated vs room1-VAD-gated (silero strict) — "
                 f"{n_ok} bags", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    png = os.path.join(args.out, "turn_length_vadgated_hist.png")
    fig.savefig(png, dpi=130)
    print(f"wrote {png}")

    # ---- room1-VAD speaking-turn (speech-segment) histogram — overall VAD feature ----
    SEGMAX = 8.0
    sbins = np.arange(0, SEGMAX + BIN, BIN)
    over = (seg > SEGMAX).mean() * 100
    fig2, ax2 = plt.subplots(figsize=(8.5, 5))
    ax2.hist(np.clip(seg, 0, SEGMAX), bins=sbins, color="#1f77b4", edgecolor="white", alpha=0.9)
    ax2.axvline(seg.mean(), color="red", lw=2,
                label=f"mean {seg.mean():.2f} ± {seg.std(ddof=1):.2f} s")
    ax2.axvline(np.median(seg), color="black", ls="--", lw=1.5,
                label=f"median {np.median(seg):.2f} s")
    ax2.set_title("Room1-VAD speaking-turn duration (silero native segments, any speaker) — "
                  f"{n_ok} bags, n={len(seg)}  (>{SEGMAX:.0f}s: {over:.1f}%)", fontsize=11)
    ax2.set_xlabel("speaking-turn duration (s)")
    ax2.set_ylabel("count")
    ax2.legend()
    fig2.tight_layout()
    png2 = os.path.join(args.out, "room1_vad_segment_hist.png")
    fig2.savefig(png2, dpi=130)
    print(f"wrote {png2}")


if __name__ == "__main__":
    main()
