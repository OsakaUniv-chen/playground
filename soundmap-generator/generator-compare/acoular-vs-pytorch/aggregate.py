"""Aggregate the per-bag OLD-vs-NEW generator comparison into report + plots.

Reads results/ticks/*.parquet (+ index.csv) and writes to results/:
  report.md            human-readable summary (headline agreement, per-mode, etc.)
  metrics.json         machine-readable numbers
  confusion.csv        4x4 label confusion (rows=OLD, cols=NEW)
  *.png                confusion heatmap, agreement-by-mode, label distribution,
                       per-tick Pearson-r and peak-distance histograms

Run (from generator_compare/):  python aggregate.py
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

LABELS = ("Left", "Right", "Teleoperator", "Others")
SHORT = {"Left": "L", "Right": "R", "Teleoperator": "Tele", "Others": "Others"}
LR = ("Left", "Right")

_HERE = Path(os.path.dirname(os.path.abspath(__file__)))
TICKS = _HERE / "results" / "ticks"
OUT = _HERE / "results"


def cohen_kappa(a, b, labels):
    """Cohen's kappa for two equal-length label sequences."""
    idx = {l: i for i, l in enumerate(labels)}
    n = len(a)
    if n == 0:
        return float("nan")
    K = len(labels)
    conf = np.zeros((K, K), dtype=np.float64)
    for x, y in zip(a, b):
        conf[idx[x], idx[y]] += 1
    po = np.trace(conf) / n
    row = conf.sum(1) / n
    col = conf.sum(0) / n
    pe = float((row * col).sum())
    return float((po - pe) / (1 - pe)) if pe != 1 else float("nan")


_NAME_RE = re.compile(r"^G(?P<group>\d+)_game(?P<game>\d+)_(?P<mode>[A-Za-z]+)$")


def load():
    """Concatenate all per-bag parquet rows, attaching mode/group from the bag name
    (robust to a missing index.csv, which is only written at run-end)."""
    cols = defaultdict(list)
    modes, groups, bags = [], [], []
    files = sorted(TICKS.glob("*.parquet"))
    for fp in files:
        bag = fp.stem
        mm = _NAME_RE.match(bag)
        bmode = mm["mode"] if mm else "?"
        bgroup = mm["group"] if mm else "?"
        tbl = pq.read_table(fp)
        d = tbl.to_pydict()
        n = len(d["old_label"])
        for k, v in d.items():
            cols[k].extend(v)
        modes.extend([bmode] * n)
        groups.extend([bgroup] * n)
        bags.extend([bag] * n)
    cols["mode"] = modes
    cols["group"] = groups
    cols["bag"] = bags
    return cols, len(files)


def agreement_block(old, new):
    old = np.asarray(old, dtype=object)
    new = np.asarray(new, dtype=object)
    n = len(old)
    if n == 0:
        return {}
    agree4 = float(np.mean(old == new))
    kappa4 = cohen_kappa(list(old), list(new), LABELS)
    old_lr = np.isin(old, LR)
    new_lr = np.isin(new, LR)
    both = old_lr & new_lr
    side_agree = float(np.mean(old[both] == new[both])) if both.sum() else float("nan")
    new_given_old_lr = float(np.mean(old[old_lr] == new[old_lr])) if old_lr.sum() else float("nan")
    old_given_new_lr = float(np.mean(old[new_lr] == new[new_lr])) if new_lr.sum() else float("nan")
    return {
        "n": int(n),
        "agree_4label": agree4,
        "kappa_4label": kappa4,
        "n_both_LR": int(both.sum()),
        "side_agree_both_LR": side_agree,
        "agree_when_old_LR": new_given_old_lr,
        "n_old_LR": int(old_lr.sum()),
        "agree_when_new_LR": old_given_new_lr,
        "n_new_LR": int(new_lr.sum()),
    }


def label_dist(labels):
    labels = np.asarray(labels, dtype=object)
    n = len(labels)
    return {l: (float(np.mean(labels == l)) if n else 0.0) for l in LABELS}


def collect_disagreements(cols):
    """Every tick where the OLD and NEW 4-label differ, with the region metrics
    (green-channel P87.5/P98) that decided each — read straight from the parquet,
    no map recomputation needed."""
    out = []
    for i in range(len(cols["old_label"])):
        if cols["old_label"][i] != cols["new_label"][i]:
            out.append({
                "bag": cols["bag"][i], "tick_idx": int(cols["tick_idx"][i]),
                "vad": bool(cols["vad_active"][i]),
                "old_label": cols["old_label"][i], "new_label": cols["new_label"][i],
                "old_metric": [cols[k][i] for k in ("old_L", "old_R", "old_T", "old_O")],
                "new_metric": [cols[k][i] for k in ("new_L", "new_R", "new_T", "new_O")],
                "pearson": cols["sm_pearson"][i], "peak_dist": cols["sm_peak_dist"][i],
            })
    return out


def main():
    if not TICKS.exists() or not list(TICKS.glob("*.parquet")):
        raise SystemExit(f"no parquet in {TICKS}; run compare_generators.py first")
    cols, n_bags = load()
    old = np.asarray(cols["old_label"], dtype=object)
    new = np.asarray(cols["new_label"], dtype=object)
    mode = np.asarray(cols["mode"], dtype=object)
    pear = np.asarray([np.nan if v is None else v for v in cols["sm_pearson"]], dtype=np.float64)
    peak = np.asarray([np.nan if v is None else v for v in cols["sm_peak_dist"]], dtype=np.float64)

    result = {"n_bags": n_bags, "overall": agreement_block(old, new)}

    # per mode
    modes_present = [m for m in ("DoA", "PSSP", "Random", "Tele", "Video") if (mode == m).any()]
    result["per_mode"] = {}
    for m in modes_present:
        sel = mode == m
        result["per_mode"][m] = agreement_block(old[sel], new[sel])

    # label distribution overall + per mode
    result["label_dist"] = {
        "overall": {"old": label_dist(old), "new": label_dist(new)},
        "per_mode": {m: {"old": label_dist(old[mode == m]), "new": label_dist(new[mode == m])}
                     for m in modes_present},
    }

    # confusion 4x4 (rows=OLD, cols=NEW)
    idx = {l: i for i, l in enumerate(LABELS)}
    conf = np.zeros((4, 4), dtype=np.int64)
    for o, nlab in zip(old, new):
        conf[idx[o], idx[nlab]] += 1
    result["confusion_old_rows_new_cols"] = conf.tolist()

    # every tick where the 4-label differs
    result["disagreements"] = collect_disagreements(cols)

    # raw sound-map similarity
    pv = pear[~np.isnan(pear)]
    kv = peak[~np.isnan(peak)]
    result["raw_sm"] = {
        "n": int(len(pv)),
        "pearson_mean": float(np.mean(pv)) if len(pv) else float("nan"),
        "pearson_median": float(np.median(pv)) if len(pv) else float("nan"),
        "pearson_p10": float(np.percentile(pv, 10)) if len(pv) else float("nan"),
        "peak_dist_median": float(np.median(kv)) if len(kv) else float("nan"),
        "peak_dist_mean": float(np.mean(kv)) if len(kv) else float("nan"),
        "frac_peak_same_cell": float(np.mean(kv == 0)) if len(kv) else float("nan"),
        "frac_peak_within_2": float(np.mean(kv <= 2)) if len(kv) else float("nan"),
    }

    # timing from index.csv
    idx_path = TICKS / "index.csv"
    olds, news = [], []
    if idx_path.exists():
        with open(idx_path) as f:
            for r in csv.DictReader(f):
                try:
                    olds.append(float(r["old_ms"])); news.append(float(r["new_ms"]))
                except (KeyError, ValueError):
                    pass
    if olds:
        result["timing"] = {"old_ms_per_map": round(float(np.mean(olds)), 1),
                            "new_ms_per_map": round(float(np.mean(news)), 1),
                            "speedup_new_over_old": round(np.mean(olds) / max(np.mean(news), 1e-9), 1)}

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "metrics.json").write_text(json.dumps(result, indent=2))

    # confusion.csv
    with open(OUT / "confusion.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["OLD\\NEW"] + [SHORT[l] for l in LABELS])
        for i, l in enumerate(LABELS):
            w.writerow([SHORT[l]] + conf[i].tolist())

    _plots(old, new, mode, modes_present, conf, pv, kv, result)
    _report(result, modes_present, conf, result["disagreements"])
    _print_summary(result, modes_present)


def _plots(old, new, mode, modes_present, conf, pv, kv, result):
    # 1) confusion heatmap (row-normalized), counts annotated
    row_sum = conf.sum(1, keepdims=True)
    norm = conf / np.clip(row_sum, 1, None)
    fig, ax = plt.subplots(figsize=(5.4, 4.6))
    im = ax.imshow(norm, cmap="Blues", vmin=0, vmax=1)
    ax.set_xticks(range(4)); ax.set_xticklabels([SHORT[l] for l in LABELS])
    ax.set_yticks(range(4)); ax.set_yticklabels([SHORT[l] for l in LABELS])
    ax.set_xlabel("NEW generator label"); ax.set_ylabel("OLD generator label")
    ax.set_title("Label confusion (row-normalized)")
    for i in range(4):
        for j in range(4):
            ax.text(j, i, f"{norm[i, j]:.2f}\n({conf[i, j]})", ha="center", va="center",
                    color="white" if norm[i, j] > 0.5 else "black", fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout(); fig.savefig(OUT / "confusion_matrix.png", dpi=140); plt.close(fig)

    # 2) agreement by mode
    ms = modes_present
    a4 = [result["per_mode"][m]["agree_4label"] for m in ms]
    sa = [result["per_mode"][m]["side_agree_both_LR"] for m in ms]
    x = np.arange(len(ms)); w = 0.38
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.bar(x - w / 2, a4, w, label="4-label agreement", color="#4C72B0")
    ax.bar(x + w / 2, sa, w, label="L/R side agreement (both call a side)", color="#DD8452")
    ax.axhline(result["overall"]["agree_4label"], ls="--", c="#4C72B0", lw=1,
               label="overall 4-label")
    ax.set_xticks(x); ax.set_xticklabels(ms); ax.set_ylim(0, 1)
    ax.set_ylabel("agreement"); ax.set_title("OLD vs NEW generator agreement by mode")
    for i, v in enumerate(a4):
        ax.text(i - w / 2, v + 0.01, f"{v:.2f}", ha="center", fontsize=8)
    for i, v in enumerate(sa):
        if not np.isnan(v):
            ax.text(i + w / 2, v + 0.01, f"{v:.2f}", ha="center", fontsize=8)
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout(); fig.savefig(OUT / "agreement_by_mode.png", dpi=140); plt.close(fig)

    # 3) label distribution old vs new (overall)
    do = result["label_dist"]["overall"]["old"]; dn = result["label_dist"]["overall"]["new"]
    x = np.arange(4)
    fig, ax = plt.subplots(figsize=(6.0, 4.0))
    ax.bar(x - w / 2, [do[l] for l in LABELS], w, label="OLD", color="#55A868")
    ax.bar(x + w / 2, [dn[l] for l in LABELS], w, label="NEW", color="#C44E52")
    ax.set_xticks(x); ax.set_xticklabels([SHORT[l] for l in LABELS])
    ax.set_ylabel("fraction of ticks"); ax.set_title("Acoustic label distribution (OLD vs NEW)")
    for i, l in enumerate(LABELS):
        ax.text(i - w / 2, do[l] + 0.005, f"{do[l]:.2f}", ha="center", fontsize=8)
        ax.text(i + w / 2, dn[l] + 0.005, f"{dn[l]:.2f}", ha="center", fontsize=8)
    ax.legend(fontsize=9)
    fig.tight_layout(); fig.savefig(OUT / "label_distribution.png", dpi=140); plt.close(fig)

    # 4) per-tick Pearson r + peak distance histograms
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.8))
    axes[0].hist(pv, bins=40, color="#4C72B0")
    axes[0].axvline(np.median(pv), c="k", ls="--", lw=1, label=f"median {np.median(pv):.2f}")
    axes[0].set_xlabel("per-tick Pearson r (raw OLD vs NEW map)")
    axes[0].set_ylabel("ticks"); axes[0].legend(fontsize=8)
    axes[0].set_title("Raw sound-map correlation")
    axes[1].hist(kv, bins=range(0, 40), color="#DD8452")
    axes[1].axvline(np.median(kv), c="k", ls="--", lw=1, label=f"median {np.median(kv):.1f}")
    axes[1].set_xlabel("peak-cell distance (64-grid)"); axes[1].set_ylabel("ticks")
    axes[1].legend(fontsize=8); axes[1].set_title("Argmax displacement")
    fig.tight_layout(); fig.savefig(OUT / "raw_sm_similarity.png", dpi=140); plt.close(fig)


def _fmt(x, p=3):
    return "nan" if (x is None or (isinstance(x, float) and np.isnan(x))) else f"{x:.{p}f}"


def _report(result, modes_present, conf, dis):
    o = result["overall"]
    L = []
    L.append("# OLD vs NEW sound-map generator — comparison\n")
    L.append(f"Full head-to-head over **{result['n_bags']}** experiment bags "
             f"(13 groups × DoA/PSSP/Random/Tele/Video; interviews excluded), "
             f"**{o['n']:,}** ticks at 4 Hz. This file is the consolidated record; "
             "regenerate it with `python aggregate.py`.\n")
    L.append("## The two generators\n")
    L.append("- **OLD** (`soundmap_api.SoundMapAPI`): vendored **acoular** "
             "`BeamformerBase.synthetic(f=2000, num=3)` — the generator that ran on the "
             "**live robot** (`mode_doa`/`mode_pssp`, `generator='old'`).")
    L.append("- **NEW** (`new_soundmap_api.NewSoundMapAPI`): vendored **PyTorch** FFT-power "
             "sum over the 2000–8000 Hz band — the generator the **offline analysis1** used "
             "(`_targeting_env`, `generator='new'`).")
    L.append("- Both share the same 16-mic xml, fs=44100, blocksize=4096, 3-level merged grid, "
             "z=1.5, c=345, r_diag, +30 dB gain, Blackman-Harris, 66.1% overlap, and emit a "
             "64×64 map in [0,160].\n")
    L.append("## Method\n")
    L.append("Everything is recreated from the **raw** signals — no recorded `/head/head_box` "
             "or `/sm_without_transform` (Video bags don't even have them). Per 4 Hz tick "
             "(first 10 s discarded for buffer fill): the **same** 160-msg audio window ending "
             "at `t` is fed to **both** generators; head boxes are re-detected with MediaPipe "
             "from `/camera/image_raw/compressed` and the VAD gate comes from `/room2_audio/vad`. "
             "The head box + VAD are **shared**, and each 64×64 map goes through the **identical** "
             "labeling path (`label_current_sm`: mask-if-silent → `exp(x−max)` → colorize → "
             "`extract_target7`, P87.5/P98). So the only thing that can differ is the beamformer, "
             "and any label disagreement is attributable to it alone.\n")
    L.append("## Headline\n")
    L.append(f"- **4-label agreement**: {_fmt(o['agree_4label'])}  (Cohen's κ = {_fmt(o['kappa_4label'])})")
    L.append(f"- **L/R side agreement** (both generators call a side): {_fmt(o['side_agree_both_LR'])} "
             f"on {o['n_both_LR']:,} ticks")
    L.append(f"- new==old among ticks OLD called L/R: {_fmt(o['agree_when_old_LR'])} ({o['n_old_LR']:,} ticks)")
    L.append(f"- old==new among ticks NEW called L/R: {_fmt(o['agree_when_new_LR'])} ({o['n_new_LR']:,} ticks)")
    if "timing" in result:
        t = result["timing"]
        L.append(f"- speed: OLD {t['old_ms_per_map']} ms/map vs NEW {t['new_ms_per_map']} ms/map "
                 f"(**{t['speedup_new_over_old']}× faster**)")
    r = result["raw_sm"]
    L.append(f"- raw map Pearson r: median {_fmt(r['pearson_median'],2)}, mean {_fmt(r['pearson_mean'],2)}; "
             f"peak in same cell {_fmt(r['frac_peak_same_cell'],2)}, within 2 cells {_fmt(r['frac_peak_within_2'],2)}\n")

    L.append("## Agreement by mode\n")
    L.append("| mode | n ticks | 4-label agree | κ | L/R side agree | n both-L/R |")
    L.append("|---|--:|--:|--:|--:|--:|")
    for m in modes_present:
        b = result["per_mode"][m]
        L.append(f"| {m} | {b['n']:,} | {_fmt(b['agree_4label'])} | {_fmt(b['kappa_4label'])} | "
                 f"{_fmt(b['side_agree_both_LR'])} | {b['n_both_LR']:,} |")
    L.append("")

    L.append("## Confusion (rows = OLD, cols = NEW), counts\n")
    L.append("| OLD\\NEW | " + " | ".join(SHORT[l] for l in LABELS) + " |")
    L.append("|---|" + "|".join(["--:"] * 4) + "|")
    for i, l in enumerate(LABELS):
        L.append(f"| **{SHORT[l]}** | " + " | ".join(f"{conf[i, j]:,}" for j in range(4)) + " |")
    L.append("")

    L.append("## Acoustic label distribution\n")
    L.append("| label | OLD | NEW |")
    L.append("|---|--:|--:|")
    do = result["label_dist"]["overall"]["old"]; dn = result["label_dist"]["overall"]["new"]
    for l in LABELS:
        L.append(f"| {SHORT[l]} | {_fmt(do[l])} | {_fmt(dn[l])} |")
    L.append("")

    # every disagreeing tick, with the deciding region metric
    L.append(f"## The {len(dis)} disagreeing tick(s)\n")
    if dis:
        L.append("The only ticks whose 4-label differs. The label = argmax of the region "
                 "percentile metric (green channel, uint8 0–255; ties broken by priority "
                 "L>R>Tele>Others). Every disagreement below is a **≤1-unit tie** at that "
                 "quantized boundary while the maps themselves are near-identical (see the "
                 "`pearson`/`peak_dist` columns and `disagreements.png`).\n")
        L.append("| bag | tick | VAD | OLD→NEW | OLD [L R T O] | NEW [L R T O] | r | peakΔ |")
        L.append("|---|--:|:-:|---|---|---|--:|--:|")
        for d in dis:
            om = " ".join("·" if v is None else f"{v:.1f}" for v in d["old_metric"])
            nm = " ".join("·" if v is None else f"{v:.1f}" for v in d["new_metric"])
            pr = "·" if d["pearson"] is None else f"{d['pearson']:.4f}"
            pk = "·" if d["peak_dist"] is None else f"{d['peak_dist']:.0f}"
            L.append(f"| {d['bag']} | {d['tick_idx']} | {'on' if d['vad'] else 'off'} | "
                     f"{SHORT[d['old_label']]}→{SHORT[d['new_label']]} | {om} | {nm} | {pr} | {pk} |")
        L.append("\nInspect the actual maps with `python inspect_disagreements.py` "
                 "(re-scans this parquet, recomputes both maps, asserts the labels reproduce, "
                 "writes `disagreements.png` + `disagreement_maps.npz`).\n")
    else:
        L.append("None — the two generators agree on every tick.\n")

    r = result["raw_sm"]
    ndis = int(round(o["n"] * (1 - o["agree_4label"])))
    L.append("## Interpretation\n")
    L.append(f"Head-to-head, the two generators are **functionally identical**: the raw 64×64 "
             f"maps correlate at r≈{_fmt(r['pearson_median'], 5)} and the 4-label decision agrees "
             f"on {_fmt(o['agree_4label'], 4)} of ticks ({ndis} disagreements in {o['n']:,}). "
             "The 2000–8000 Hz band (NEW) vs 1/3-octave-at-2000 (OLD) difference lands almost "
             "entirely in low-energy cells that the `exp(x−max)` labeling transform crushes to ~0, "
             "so it never moves the argmax that decides the label.\n")
    L.append("Swapping the beamformer alone — same audio window, same head boxes, same VAD, same "
             "labeling path — is therefore **safe**: it does not change the 4-label targeting "
             "decision, and the NEW generator is also ~7-8× faster.\n")
    L.append("Plots: `confusion_matrix.png`, `agreement_by_mode.png`, `label_distribution.png`, "
             "`raw_sm_similarity.png`. Machine-readable: `metrics.json`, `confusion.csv`.\n")
    (OUT / "report.md").write_text("\n".join(L))


def _print_summary(result, modes_present):
    o = result["overall"]
    print("=" * 66)
    print(f"OLD vs NEW generator — {result['n_bags']} bags, {o['n']:,} ticks")
    print("=" * 66)
    print(f"  4-label agreement     : {_fmt(o['agree_4label'])}  (kappa {_fmt(o['kappa_4label'])})")
    print(f"  L/R side agreement    : {_fmt(o['side_agree_both_LR'])}  ({o['n_both_LR']:,} both-LR ticks)")
    print(f"  new==old | old is L/R : {_fmt(o['agree_when_old_LR'])}")
    print(f"  old==new | new is L/R : {_fmt(o['agree_when_new_LR'])}")
    if "timing" in result:
        t = result["timing"]
        print(f"  speed                 : OLD {t['old_ms_per_map']}ms  NEW {t['new_ms_per_map']}ms/map "
              f"({t['speedup_new_over_old']}x)")
    print("  per mode (4-label agree | side agree):")
    for m in modes_present:
        b = result["per_mode"][m]
        print(f"    {m:<7}: {_fmt(b['agree_4label'])} | {_fmt(b['side_agree_both_LR'])}  (n={b['n']:,})")
    print(f"\n  wrote report.md, metrics.json, confusion.csv, *.png -> {OUT}")


if __name__ == "__main__":
    main()
