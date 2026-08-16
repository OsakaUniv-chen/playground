"""Does the 1-bit generator make the same 4-label call as the live acoular one?

The speed answer (`bench_n100.py`) is only interesting if the cheap generator
also decides the same thing, so this runs both over every 4 Hz tick of one or
more bags -- label decisions only need the audio window plus the recorded head
boxes and VAD, no camera frame, so it is much cheaper than rendering the video --
and breaks the disagreements down by VAD state, head-box validity, and each
generator's own decision margin (peak region metric minus runner-up).

Same label pipeline as `compare_video.py` (mask-if-silent -> exp(x-max) ->
colorize -> extract_target7), and head box + VAD are shared between the two, so
every disagreement is attributable to the beamformer alone.

    python3 analyze_agreement.py --host                       # default bag, fast
    python3 analyze_agreement.py --host --bags G11_game4_DoA,G2_game3_PSSP

The labels do not depend on which cores the process runs on, so `--host` is the
sensible way to run this one; the N100 emulation is the default only to keep
every script in this folder consistent (and it costs ~3x the wall time here).

Writes results/agreement.json + results/agreement.md.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import n100
n100.activate_from_argv()

# acoular before numpy -- see bench_n100.py / README "the import-order trap".
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "generator-acoular", "soundmap")))
import acoular  # noqa: F401,E402

import argparse  # noqa: E402
import json  # noqa: E402
from collections import Counter  # noqa: E402

import numpy as np  # noqa: E402

for _p in (os.path.join(_HERE, "..", "..", "generator-acoular"),
           os.path.join(_HERE, "..", "..", "generator-1bit"),
           os.path.join(_HERE, "..")):
    _p = os.path.normpath(_p)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import utils as B  # noqa: E402
from utils import get_speaking_box, label_current_sm, vad_active_at  # noqa: E402
from soundmap_api import SoundMapAPI  # noqa: E402
from onebit_soundmap import OneBitSoundMapAPI  # noqa: E402

DEFAULT_ROOT = "/media/chen/Extreme SSD/PSSPData/WordWolfExp"
TICK = 0.25
AUDIO_WIN = 160
RESULTS = os.path.join(_HERE, "results")


def margin(metrics):
    """peak region metric - runner-up (None-safe). Larger = more confident."""
    vals = sorted((v for v in metrics.values() if v is not None), reverse=True)
    return vals[0] - vals[1] if len(vals) >= 2 else None


def box_valid(box):
    return box is not None and not all(int(c) == -99 for c in box)


def scan_bag(bag_dir, bag_name, ac_api, ob_api, speaking_box, start_s, dur_s):
    con = B.open_bag(bag_dir)
    audio = B.read_series(con, B.AUDIO_TOPIC)
    head = [(t, v) for t, v in B.read_series(con, B.HEAD_TOPIC) if v and len(v) >= 8]
    vad = B.read_series(con, B.VAD_TOPIC)
    con.close()
    a_ts = np.array([t for t, _ in audio]); a_d = [d for _, d in audio]
    h_ts = np.array([t for t, _ in head])
    vts = [t / 1e9 for t, _ in vad]; vval = [bool(v) for _, v in vad]

    t0 = int(a_ts[0] + start_s * 1e9)
    t_end = int(a_ts[-1]) if dur_s <= 0 else int(min(a_ts[-1], t0 + dur_s * 1e9))
    ticks = np.arange(t0, t_end, int(TICK * 1e9))
    print(f"{bag_name}: {len(ticks)} ticks over {(t_end - t0) / 1e9:.0f}s", flush=True)

    rows = []
    for k, t in enumerate(ticks):
        ja = int(np.searchsorted(a_ts, t, side="right"))
        if ja < AUDIO_WIN:
            continue
        window = a_d[ja - AUDIO_WIN:ja]
        jh = int(np.searchsorted(h_ts, t, side="right")) - 1
        hb = ([list(head[jh][1][0:4]), list(head[jh][1][4:8])] if jh >= 0
              else [[-99] * 4, [-99] * 4])
        va = vad_active_at(vts, vval, t / 1e9)

        lab_ac, met_ac, _ = label_current_sm(ac_api.generate(window), hb, va,
                                             speaking_box=speaking_box)
        lab_ob, met_ob, _ = label_current_sm(ob_api.generate(window), hb, va,
                                             speaking_box=speaking_box)
        rows.append(dict(bag=bag_name, t_s=(t - t0) / 1e9, vad=bool(va),
                         hb_l=box_valid(hb[0]), hb_r=box_valid(hb[1]),
                         lab_ac=lab_ac, lab_ob=lab_ob, agree=lab_ac == lab_ob,
                         margin_ac=margin(met_ac), margin_ob=margin(met_ob)))
        if k % 100 == 0:
            n_ok = sum(r["agree"] for r in rows)
            print(f"  {bag_name} tick {k:4d}/{len(ticks)}  agree {n_ok}/{len(rows)}",
                  flush=True)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rosbag-root", default=DEFAULT_ROOT)
    ap.add_argument("--bags", default="G11_game4_DoA",
                    help="comma-separated bag names (default: %(default)s)")
    ap.add_argument("--start", type=float, default=10.0,
                    help="skip the first N s (audio-window fill)")
    ap.add_argument("--dur", type=float, default=0.0, help="0 = to the end of the bag")
    ap.add_argument("--out", default=os.path.join(RESULTS, "agreement"))
    n100.add_arguments(ap)
    args = ap.parse_args()

    ac_api = SoundMapAPI()
    ob_api = OneBitSoundMapAPI(device="cpu")
    speaking_box = get_speaking_box()

    names = [b for b in args.bags.split(",") if b]
    for name in names:
        if not os.path.isdir(os.path.join(args.rosbag_root, name)):
            raise SystemExit(f"bag not found: {os.path.join(args.rosbag_root, name)}")

    os.makedirs(RESULTS, exist_ok=True)
    rows = []
    for name in names:
        rows += scan_bag(os.path.join(args.rosbag_root, name), name,
                         ac_api, ob_api, speaking_box, args.start, args.dur)
        # Flush after every bag. A full multi-bag scan is ~15 min per bag with
        # acoular in the loop, and writing only at the end means an interrupted
        # run leaves nothing behind at all -- which is exactly what happened once.
        write_results(summarize(rows, args), args.out)
        print(f"  ... wrote partial results after {name} ({len(rows)} ticks)", flush=True)

    if not rows:
        raise SystemExit("no ticks scanned")
    out = summarize(rows, args)
    write_results(out, args.out)
    print("\n" + out["markdown"])
    print(f"wrote {args.out}.json / {args.out}.md")


def write_results(out, stem):
    with open(stem + ".json", "w") as fh:
        json.dump(out, fh, indent=1)
    with open(stem + ".md", "w") as fh:
        fh.write(out["markdown"])


def summarize(rows, args) -> dict:
    n = len(rows)
    n_agree = sum(r["agree"] for r in rows)

    def rate(pred):
        sub = [r for r in rows if pred(r)]
        if not sub:
            return None, 0
        return 100.0 * sum(r["agree"] for r in sub) / len(sub), len(sub)

    subsets = {
        "overall": (100.0 * n_agree / n, n),
        "VAD active": rate(lambda r: r["vad"]),
        "VAD silent": rate(lambda r: not r["vad"]),
        "both head boxes valid": rate(lambda r: r["hb_l"] and r["hb_r"]),
        "one head box valid": rate(lambda r: r["hb_l"] != r["hb_r"]),
        "no head box valid": rate(lambda r: not r["hb_l"] and not r["hb_r"]),
    }
    per_bag = {b: rate(lambda r, b=b: r["bag"] == b) for b in dict.fromkeys(r["bag"] for r in rows)}

    labels = sorted({r["lab_ac"] for r in rows} | {r["lab_ob"] for r in rows})
    cm = Counter((r["lab_ac"], r["lab_ob"]) for r in rows)
    confusion = {la: {lo: cm[(la, lo)] for lo in labels} for la in labels}

    dis = [r for r in rows if not r["agree"]]
    agr = [r for r in rows if r["agree"]]

    def mean_margin(sub, key):
        vals = [r[key] for r in sub if r[key] is not None]
        return float(np.mean(vals)) if vals else None

    margins = {
        "acoular": {"agree": mean_margin(agr, "margin_ac"), "disagree": mean_margin(dis, "margin_ac")},
        "1bit": {"agree": mean_margin(agr, "margin_ob"), "disagree": mean_margin(dis, "margin_ob")},
    }
    pairs = Counter((r["lab_ac"], r["lab_ob"]) for r in dis)
    pairs_vad = Counter((r["lab_ac"], r["lab_ob"]) for r in dis if r["vad"])

    out = {
        "machine": n100.describe(),
        "bags": args.bags, "start_s": args.start, "dur_s": args.dur,
        "ticks": n, "agree": n_agree, "agree_pct": 100.0 * n_agree / n,
        "by_subset": {k: {"pct": v[0], "n": v[1]} for k, v in subsets.items()},
        "by_bag": {k: {"pct": v[0], "n": v[1]} for k, v in per_bag.items()},
        "confusion_acoular_rows_1bit_cols": confusion,
        "mean_margin": margins,
        "top_disagreement_pairs": [{"acoular": a, "1bit": b, "n": c} for (a, b), c in pairs.most_common(8)],
        "top_disagreement_pairs_vad_active": [{"acoular": a, "1bit": b, "n": c}
                                              for (a, b), c in pairs_vad.most_common(8)],
        "rows": rows,
    }
    out["markdown"] = render_md(out, labels)
    return out


def render_md(out, labels) -> str:
    L = [f"# acoular vs 1-bit -- 4-label agreement\n",
         f"- bags: `{out['bags']}` ({out['ticks']} ticks at 4 Hz, first {out['start_s']:.0f}s skipped)",
         f"- machine: {out['machine']['label']} (labels are machine-independent; "
         f"this only affects how long the scan took)\n",
         f"**overall agreement: {out['agree']}/{out['ticks']} ({out['agree_pct']:.1f}%)**\n",
         "| subset | agreement | n |", "|---|---:|---:|"]
    for k, v in out["by_subset"].items():
        if v["n"]:
            L.append(f"| {k} | {v['pct']:.1f}% | {v['n']} |")
    if len(out["by_bag"]) > 1:
        L.append("\n| bag | agreement | n |")
        L.append("|---|---:|---:|")
        for k, v in out["by_bag"].items():
            L.append(f"| {k} | {v['pct']:.1f}% | {v['n']} |")

    L.append("\nconfusion (rows = acoular label, cols = 1-bit label):\n")
    L.append("| acoular \\ 1-bit | " + " | ".join(labels) + " |")
    L.append("|---" * (len(labels) + 1) + "|")
    cm = out["confusion_acoular_rows_1bit_cols"]
    for la in labels:
        L.append(f"| **{la}** | " + " | ".join(str(cm[la][lo]) for lo in labels) + " |")

    m = out["mean_margin"]
    L.append("\nmean decision margin (peak region metric - runner-up):\n")
    L.append("| generator | on agreeing ticks | on disagreeing ticks |")
    L.append("|---|---:|---:|")
    for name in ("acoular", "1bit"):
        a, d = m[name]["agree"], m[name]["disagree"]
        L.append(f"| {name} | " + (f"{a:.2f}" if a is not None else "-") + " | "
                 + (f"{d:.2f}" if d is not None else "-") + " |")

    if out["top_disagreement_pairs"]:
        L.append("\nmost common disagreements (acoular -> 1-bit):\n")
        for p in out["top_disagreement_pairs"]:
            L.append(f"- `{p['acoular']}` -> `{p['1bit']}`: {p['n']}")
    if out["top_disagreement_pairs_vad_active"]:
        L.append("\nsame, restricted to VAD-active ticks (a real talker, not room noise):\n")
        for p in out["top_disagreement_pairs_vad_active"]:
            L.append(f"- `{p['acoular']}` -> `{p['1bit']}`: {p['n']}")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
