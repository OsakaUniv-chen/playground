"""WP4 — behavior analyses over the tick tables (results/ticks/*.parquet).

Implements SPEC §10:
  10.1 confusion   same-observation agreement / confusion matrices (Todo §2.5)
  10.2 kappa       gaze-on-speaker kappa (decision + executed levels)
  10.3 speech      acoustic scene (4-label ratios) + run-length / switch stats
  10.4 prediction  PSSP +Δt prediction accuracy vs persistence
  10.5 pswitch     P(switch) of executed gaze

Recomputed signals (gt/doa/pssp/tel/rand) exist in every bag, so confusion /
prediction / decision-kappa POOL over all bags. Executed gaze (motors_side)
reflects each bag's real policy, so executed-kappa and P(switch) group BY
condition. Reads only the parquet tables (Mac OK).

    python wp4.py all --ticks ../results/ticks --out ../results/metrics
    python wp4.py confusion            # a single analysis
"""
from __future__ import annotations

import argparse
import glob
import os
import re

import numpy as np
import pandas as pd

STEP_NS = 250_000_000               # 0.25 s
LABELS4 = ["Left", "Right", "Teleoperator", "Others"]
LR = ["L", "R"]
LAGS_S = (-1, -0.5, 0, 0.5, 1, 1.5, 2, 2.5, 3, 5, 10)


# --------------------------------------------------------------------------
# loading + label helpers
# --------------------------------------------------------------------------
def mode_of(bag):
    m = re.match(r"G\d+_game\d+_([A-Za-z]+)", bag)
    return m.group(1) if m else "?"


def load(ticks_dir):
    """[(bag, mode, df)] sorted by tick_ts, one per parquet."""
    out = []
    for p in sorted(glob.glob(os.path.join(ticks_dir, "*.parquet"))):
        bag = os.path.basename(p)[:-8]
        df = pd.read_parquet(p).sort_values("tick_ts").reset_index(drop=True)
        out.append((bag, mode_of(bag), df))
    if not out:
        raise SystemExit(f"no parquet files in {ticks_dir}")
    return out


def lr4(s):
    """4-label Series -> L/R (Teleoperator/Others/None -> NaN)."""
    s = pd.Series(list(s))
    return s.map({"Left": "L", "Right": "R"})


def lr2(s):
    """'left'/'right' Series -> L/R."""
    s = pd.Series(list(s))
    return s.map({"left": "L", "right": "R"})


def lagged(df, col, lag_ticks):
    """Value of df[col] at tick_ts + lag_ticks (within the bag; NaN if missing)."""
    s = df.set_index("tick_ts")[col]
    vals = s.reindex(df["tick_ts"].to_numpy() + int(lag_ticks) * STEP_NS).to_numpy()
    return pd.Series(vals, index=df.index)


def confusion(a, b, labels):
    a, b = pd.Series(list(a)), pd.Series(list(b))
    mask = a.notna() & b.notna()
    a, b = a[mask], b[mask]
    n = len(a)
    mat = (pd.crosstab(a, b).reindex(index=labels, columns=labels, fill_value=0)
           if n else pd.DataFrame(0, index=labels, columns=labels))
    agree = np.trace(mat.to_numpy()) / n if n else float("nan")
    return mat, agree, n


def kappa(a, b):
    mat, po, n = confusion(a, b, LR)
    if n == 0:
        return float("nan"), float("nan"), float("nan"), 0
    m = mat.to_numpy()
    pe = ((m.sum(1) / n) * (m.sum(0) / n)).sum()
    k = (po - pe) / (1 - pe) if pe < 1 else float("nan")
    return po, pe, k, n


def pool(bags, fn):
    """Concatenate (a, b) pairs produced by fn(df) across all bags."""
    A, Bb = [], []
    for _, _, df in bags:
        a, b = fn(df)
        A.append(pd.Series(list(a))); Bb.append(pd.Series(list(b)))
    return pd.concat(A, ignore_index=True), pd.concat(Bb, ignore_index=True)


def mat_md(mat, agree, n):
    lines = ["| A\\B | " + " | ".join(map(str, mat.columns)) + " |",
             "|---|" + "---|" * len(mat.columns)]
    for idx, row in mat.iterrows():
        lines.append(f"| {idx} | " + " | ".join(str(int(v)) for v in row) + " |")
    lines.append(f"\nagreement = **{agree*100:.1f}%**  (n = {n})")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 10.1 confusion / agreement
# --------------------------------------------------------------------------
def run_confusion(bags, out):
    L = ["## 10.1 Same-observation agreement / confusion matrices",
         "Pooled over all bags. 4-label pairs = 4×4; TEL/RAND pairs restricted to "
         "L/R ticks = 2×2. GT = truth (window→t); DoA = mode decision (window→t−0.2).", ""]

    pairs4 = [
        ("PSSP_p10(t) vs GT(t+1s)", lambda d: (d["pssp_p10"], lagged(d, "gt_label", 4)),
         "+1s prediction accuracy"),
        ("GT(t) vs GT(t+1s)", lambda d: (d["gt_label"], lagged(d, "gt_label", 4)),
         "1s persistence base rate"),
        ("DoA(t) vs GT(t)", lambda d: (d["doa_label"], d["gt_label"]),
         "DoA 0.2s delay penalty"),
    ]
    for name, fn, desc in pairs4:
        a, b = pool(bags, fn)
        mat, ag, n = confusion(a, b, LABELS4)
        L += [f"### {name} — {desc}", mat_md(mat, ag, n), ""]

    pairs2 = [
        ("TEL(t) vs DoA(t)", lambda d: (lr2(d["tel_side"]), lr4(d["doa_label"]))),
        ("TEL(t) vs PSSP_p10(t)", lambda d: (lr2(d["tel_side"]), lr4(d["pssp_p10"]))),
        ("TEL(t) vs GT(t)", lambda d: (lr2(d["tel_side"]), lr4(d["gt_label"]))),
        ("PSSP_p10(t) vs RAND(t)", lambda d: (lr4(d["pssp_p10"]), lr2(d["rand_side"]))),
        ("RAND(t) vs GT(t)", lambda d: (lr2(d["rand_side"]), lr4(d["gt_label"]))),
    ]
    for name, fn in pairs2:
        a, b = pool(bags, fn)
        mat, ag, n = confusion(a, b, LR)
        L += [f"### {name} (2×2)", mat_md(mat, ag, n), ""]

    # TEL vs GT(t+lag) lag sweep
    L += ["### TEL(t) vs GT(t+lag) — human tracking-lag curve (2×2 agreement)",
          "| lag(s) | " + " | ".join(f"{x:g}" for x in LAGS_S) + " |",
          "|---|" + "---|" * len(LAGS_S)]
    row = []
    for lag in LAGS_S:
        a, b = pool(bags, lambda d, lg=lag: (lr2(d["tel_side"]),
                                             lr4(lagged(d, "gt_label", int(lg / 0.25)))))
        _, ag, _ = confusion(a, b, LR)
        row.append(f"{ag*100:.1f}")
    L += ["| agree% | " + " | ".join(row) + " |", ""]

    # precision / recall for PSSP_p10 vs GT(t+1s) (4-label)
    a, b = pool(bags, lambda d: (d["pssp_p10"], lagged(d, "gt_label", 4)))
    mat, _, _ = confusion(a, b, LABELS4)  # rows=pred(pssp), cols=actual(gt+1)
    m = mat.to_numpy()
    L += ["### PSSP_p10 vs GT(t+1s) — per-label precision / recall",
          "| label | precision | recall |", "|---|---|---|"]
    for i, lab in enumerate(LABELS4):
        prec = m[i, i] / m[i, :].sum() if m[i, :].sum() else float("nan")
        rec = m[i, i] / m[:, i].sum() if m[:, i].sum() else float("nan")
        L.append(f"| {lab} | {prec:.3f} | {rec:.3f} |")
    _write(out, "10_1_confusion.md", L)


# --------------------------------------------------------------------------
# 10.2 gaze-on-speaker kappa
# --------------------------------------------------------------------------
def run_kappa(bags, out):
    """Gaze-on-speaker rate p_o = of all ticks where a participant (L/R) is the
    true speaker, the fraction where the policy looks at that participant.
    Base = GT∈{L,R} (same n for every policy); a policy deciding
    Teleoperator/Others (or no Tele face) counts as a MISS, not excluded."""
    L = ["## 10.2 Gaze-on-speaker rate p_o (base = GT∈{L,R} ticks)",
         "p_o = P(policy looks at the speaker | a participant is speaking). "
         "Base is the same for all policies; deciding Teleoperator/Others counts as a miss.", "",
         "| policy | **p_o** | n |", "|---|---|---|"]
    dec = {"DoA": lambda d: lr4(d["doa_label"]), "PSSP": lambda d: lr4(d["pssp_p10"]),
           "Tele": lambda d: lr2(d["tel_side"]), "Random": lambda d: lr2(d["rand_side"])}
    for pol, fn in dec.items():
        S, G = pool(bags, lambda d, f=fn: (f(d), lr4(d["gt_label"])))
        base = G.notna()
        n = int(base.sum())
        po = int(((S == G) & base).sum()) / n if n else float("nan")
        L.append(f"| {pol} | **{po:.3f}** | {n} |")
    _write(out, "10_2_gaze.md", L)


# --------------------------------------------------------------------------
# 10.3 acoustic scene + speech stats
# --------------------------------------------------------------------------
def _runs(seq):
    """Consecutive equal-label run lengths -> {label: [lengths]}."""
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


def run_speech(bags, out):
    L = ["## 10.3 Per-policy output-label distribution + speech statistics (all bags pooled)", "",
         "### Output-label distribution over all bags",
         "GT = truth / acoustic scene; DoA/PSSP = the label each policy decided to look at. "
         "Same population (all ticks) for every policy.", "",
         "| label | GT | DoA | PSSP |", "|---|---|---|---|"]
    S = {c: pd.concat([pd.Series(list(df[c])) for _, _, df in bags], ignore_index=True)
         for c in ("gt_label", "doa_label", "pssp_p10", "tel_side", "rand_side")}

    def dist4(s):
        s = s.dropna(); n = len(s)
        return [(s == k).mean() * 100 for k in LABELS4], n
    g, ng = dist4(S["gt_label"]); d, nd = dist4(S["doa_label"]); p, npp = dist4(S["pssp_p10"])
    for i, lab in enumerate(LABELS4):
        L.append(f"| {lab} | {g[i]:.1f}% | {d[i]:.1f}% | {p[i]:.1f}% |")
    L.append(f"| **n** | {ng} | {nd} | {npp} |")

    def dist2(s):
        s = s.dropna(); n = len(s)
        return (s == "left").mean() * 100, (s == "right").mean() * 100, n
    tl, tr, nt = dist2(S["tel_side"]); rl, rr, nr = dist2(S["rand_side"])
    L += ["", f"L/R-only policies: **Tele** left {tl:.1f}% / right {tr:.1f}% (n={nt}), "
          f"**Random** left {rl:.1f}% / right {rr:.1f}% (n={nr})."]

    # run-length (utterance-length proxy) per label, pooled
    runs = {}
    for _, _, df in bags:
        for lab, lens in _runs(df["gt_label"]).items():
            runs.setdefault(lab, []).extend(lens)
    L += ["", "### Speaking-turn duration per label (run length × 0.25 s)",
          "Each run of a label = one continuous turn as the dominant acoustic source; "
          "i.e. how long they keep speaking each time they start.",
          "| label | mean ± SD (s) | median(s) | p90(s) | n turns |", "|---|---|---|---|---|"]
    for lab in LABELS4:
        r = np.array(runs.get(lab, [])) * 0.25
        if len(r):
            sd = r.std(ddof=1) if len(r) > 1 else 0.0
            L.append(f"| {lab} | {r.mean():.2f} ± {sd:.2f} | {np.median(r):.2f} | "
                     f"{np.percentile(r,90):.2f} | {len(r)} |")

    # Left+Right (facing participants) combined
    lr_runs = np.array(runs.get("Left", []) + runs.get("Right", [])) * 0.25
    if len(lr_runs):
        L += ["", f"**Left+Right (facing participants) turn duration: "
              f"{lr_runs.mean():.2f} ± {lr_runs.std(ddof=1):.2f} s** "
              f"(median {np.median(lr_runs):.2f}, p90 {np.percentile(lr_runs,90):.2f}, n={len(lr_runs)})"]
    _write(out, "10_3_speech.md", L)


# --------------------------------------------------------------------------
# 10.4 PSSP prediction accuracy
# --------------------------------------------------------------------------
def run_prediction(bags, out):
    L = ["## 10.4 PSSP prediction accuracy (pooled all bags)",
         "L/R-restricted: base = ticks where GT(t+h)∈{L,R}; a prediction of "
         "Teleoperator/Others counts as wrong. 4-label = full match. "
         "Baseline = persistence (GT(t)).", "",
         "| horizon | GT lag(ticks) | PSSP L/R acc | persist L/R acc | PSSP 4-lab acc | persist 4-lab acc | n(L/R) |",
         "|---|---|---|---|---|---|---|"]
    for col, h_ticks, hs in (("pssp_p05", 2, "+0.5s"), ("pssp_p10", 4, "+1.0s"),
                             ("pssp_p15", 6, "+1.5s"), ("pssp_p20", 8, "+2.0s")):
        pred_lr, persist_lr, pred_4, persist_4, gt_now4, gt_fut4 = [], [], [], [], [], []
        for _, _, df in bags:
            fut = lagged(df, "gt_label", h_ticks)
            pred_lr.append(lr4(df[col])); persist_lr.append(lr4(df["gt_label"]))
            gt_fut4.append(pd.Series(list(fut)))
            pred_4.append(pd.Series(list(df[col]))); persist_4.append(pd.Series(list(df["gt_label"])))
        P = pd.concat(pred_lr, ignore_index=True); Q = pd.concat(persist_lr, ignore_index=True)
        F4 = pd.concat(gt_fut4, ignore_index=True)
        Flr = lr4(F4)
        P4 = pd.concat(pred_4, ignore_index=True); Q4 = pd.concat(persist_4, ignore_index=True)
        base = Flr.notna()                                   # GT(t+h) ∈ {L,R}
        nb = int(base.sum())
        pssp_lr = ((P == Flr) & base).sum() / nb if nb else float("nan")
        pers_lr = ((Q == Flr) & base).sum() / nb if nb else float("nan")
        m4 = P4.notna() & F4.notna()
        pssp_4 = (P4[m4] == F4[m4]).mean()
        pers_4 = (Q4[F4.notna() & Q4.notna()] == F4[F4.notna() & Q4.notna()]).mean()
        L.append(f"| {hs} | {h_ticks} | {pssp_lr:.3f} | {pers_lr:.3f} | "
                 f"{pssp_4:.3f} | {pers_4:.3f} | {nb} |")

    # why 4-label PSSP loses: predicted-label distribution + per-actual-label accuracy
    Pp, Ff, Qq = [], [], []
    for _, _, df in bags:
        Pp.append(pd.Series(list(df["pssp_p10"])))
        Ff.append(pd.Series(list(lagged(df, "gt_label", 4))))
        Qq.append(pd.Series(list(df["gt_label"])))
    Pp = pd.concat(Pp, ignore_index=True); Ff = pd.concat(Ff, ignore_index=True); Qq = pd.concat(Qq, ignore_index=True)
    md = Pp.notna() & Ff.notna()
    Pm, Fm = Pp[md], Ff[md]
    L += ["", f"### pssp_p10 (+1.0s): predicted-label distribution & per-actual-label accuracy (n={len(Pm)})",
          "PSSP over-predicts L/R and under-predicts the persistent Teleoperator/Others.", "",
          "| label | PSSP predicts | GT(t+1s) actual | PSSP acc | persist acc |", "|---|---|---|---|---|"]
    for lab in LABELS4:
        mq = Qq.notna() & Ff.notna() & (Ff == lab)
        L.append(f"| {lab} | {(Pm==lab).mean()*100:.1f}% | {(Fm==lab).mean()*100:.1f}% | "
                 f"{(Pm[Fm==lab]==lab).mean():.3f} | {(Qq[mq]==Ff[mq]).mean():.3f} |")
    L.append(f"| **L/R total** | **{Pm.isin(['Left','Right']).mean()*100:.1f}%** | "
             f"{Fm.isin(['Left','Right']).mean()*100:.1f}% | | |")
    _write(out, "10_4_prediction.md", L)


# --------------------------------------------------------------------------
# 10.5 P(switch) of executed gaze
# --------------------------------------------------------------------------
def run_pswitch(bags, out, window_ticks=720):
    L = ["## 10.5 P(switch) — executed gaze (motors_side)",
         f"Sign flips of motors_side over the first {window_ticks} ticks "
         "(3 min @4Hz) / window. By condition.", "",
         "| condition | P(switch) mean±sd | n bags |", "|---|---|---|"]
    by = {}
    for _, mode, df in bags:
        s = pd.Series(list(df["motors_side"])).dropna().to_numpy()[:window_ticks]
        if len(s) < 2:
            continue
        flips = int((s[1:] != s[:-1]).sum())
        by.setdefault(mode, []).append(flips / window_ticks)
    for mode in ("Tele", "PSSP", "DoA", "Random"):
        if mode in by:
            v = np.array(by[mode])
            L.append(f"| {mode} | {v.mean():.4f} ± {v.std():.4f} | {len(v)} |")
    _write(out, "10_5_pswitch.md", L)


def _write(out, name, lines):
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, name), "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {name}")


ANALYSES = {"confusion": run_confusion, "kappa": run_kappa, "speech": run_speech,
            "prediction": run_prediction, "pswitch": run_pswitch}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("which", nargs="?", default="all", choices=["all", *ANALYSES])
    ap.add_argument("--ticks", default=os.path.join(os.path.dirname(__file__), "..", "..", "results", "ticks"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "..", "results", "metrics"))
    args = ap.parse_args()
    bags = load(args.ticks)
    print(f"loaded {len(bags)} bags")
    todo = ANALYSES if args.which == "all" else {args.which: ANALYSES[args.which]}
    for fn in todo.values():
        fn(bags, args.out)


if __name__ == "__main__":
    main()
