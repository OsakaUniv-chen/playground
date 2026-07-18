"""When do PYTORCH (FFT beamformer) and 1-BIT (XOR) disagree on the 4-label decision?

Runs both generators over every 4Hz tick of a bag (no video rendering --
label decisions only need the audio window + recorded head boxes + VAD, not
the camera frame), and breaks disagreements down by VAD state, head-box
validity, and each generator's own decision margin (peak region value minus
runner-up). Reuses the exact same label pipeline as compare_video.py
(mask -> transform_sm -> sm_to_color -> extract_target7), so this explains
the label-agreement number burned into that video.

    python3 analyze_disagreement.py --bag G11_game4_DoA
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "video-generator"))

import bag_io as B
from labeling import get_speaking_box, label_current_sm, vad_active_at
from beamform_soundmap import SoundMapAPI
from onebit_soundmap import OneBitSoundMapAPI

DEFAULT_ROOT = "/media/chen/Extreme SSD/PSSPData/WordWolfExp"
TICK = 0.25
AUDIO_WIN = 160


def margin(metrics):
    """peak region value - runner-up value (both None-safe). Larger = more confident."""
    vals = sorted((v for v in metrics.values() if v is not None), reverse=True)
    if len(vals) < 2:
        return None
    return vals[0] - vals[1]


def box_valid(box):
    return box is not None and not all(int(c) == -99 for c in box)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rosbag-root", default=DEFAULT_ROOT)
    ap.add_argument("--bag", default="G11_game4_DoA")
    ap.add_argument("--start", type=float, default=10.0)
    ap.add_argument("--dur", type=float, default=0.0, help="0 = whole bag")
    ap.add_argument("--pytorch-device", default="cuda")
    args = ap.parse_args()

    bag = os.path.join(args.rosbag_root, args.bag)
    con = B.open_bag(bag)
    audio = B.read_series(con, B.AUDIO_TOPIC)
    a_ts = np.array([t for t, _ in audio]); a_d = [d for _, d in audio]
    head = [(t, v) for t, v in B.read_series(con, B.HEAD_TOPIC) if v and len(v) >= 8]
    h_ts = np.array([t for t, _ in head])
    vad = B.read_series(con, B.VAD_TOPIC)
    vts = [t / 1e9 for t, _ in vad]; vval = [bool(v) for _, v in vad]
    con.close()

    t0 = int(a_ts[0] + args.start * 1e9)
    t_end = int(a_ts[-1]) if args.dur <= 0 else int(min(a_ts[-1], t0 + args.dur * 1e9))
    ticks = np.arange(t0, t_end, int(TICK * 1e9))
    print(f"{args.bag}: {len(ticks)} ticks over {(t_end - t0) / 1e9:.0f}s")

    pt_api = SoundMapAPI(device=args.pytorch_device)
    ob_api = OneBitSoundMapAPI(device="cpu")
    speaking_box = get_speaking_box()

    rows = []
    for t in ticks:
        ja = int(np.searchsorted(a_ts, t, side="right"))
        if ja < AUDIO_WIN:
            continue
        window = a_d[ja - AUDIO_WIN:ja]
        jh = int(np.searchsorted(h_ts, t, side="right")) - 1
        hb = ([list(head[jh][1][0:4]), list(head[jh][1][4:8])] if jh >= 0
              else [[-99] * 4, [-99] * 4])
        va = vad_active_at(vts, vval, t / 1e9)

        sm_pt = pt_api.generate(window)
        sm_ob = ob_api.generate(window)
        lab_pt, met_pt, _ = label_current_sm(sm_pt, hb, va, speaking_box=speaking_box)
        lab_ob, met_ob, _ = label_current_sm(sm_ob, hb, va, speaking_box=speaking_box)

        rows.append(dict(
            t_s=(t - t0) / 1e9, vad=va,
            hb_l=box_valid(hb[0]), hb_r=box_valid(hb[1]),
            lab_pt=lab_pt, lab_ob=lab_ob, agree=(lab_pt == lab_ob),
            margin_pt=margin(met_pt), margin_ob=margin(met_ob),
        ))

    n = len(rows)
    n_agree = sum(r["agree"] for r in rows)
    print(f"\noverall agreement: {n_agree}/{n} ({100*n_agree/n:.1f}%)\n")

    def rate(pred):
        sub = [r for r in rows if pred(r)]
        if not sub:
            return None, 0
        a = sum(r["agree"] for r in sub)
        return 100 * a / len(sub), len(sub)

    print("agreement by VAD state:")
    for label, pred in [("VAD active", lambda r: r["vad"]), ("VAD silent", lambda r: not r["vad"])]:
        pct, cnt = rate(pred)
        print(f"  {label:12s} {pct:5.1f}%  (n={cnt})" if pct is not None else f"  {label:12s} n=0")

    print("\nagreement by head-box validity:")
    for label, pred in [
        ("both boxes valid", lambda r: r["hb_l"] and r["hb_r"]),
        ("one box valid", lambda r: r["hb_l"] != r["hb_r"]),
        ("no boxes valid", lambda r: not r["hb_l"] and not r["hb_r"]),
    ]:
        pct, cnt = rate(pred)
        print(f"  {label:18s} {pct:5.1f}%  (n={cnt})" if pct is not None else f"  {label:18s} n=0")

    print("\nconfusion matrix (rows=PYTORCH label, cols=1-BIT label):")
    labels = sorted({r["lab_pt"] for r in rows} | {r["lab_ob"] for r in rows})
    cm = Counter((r["lab_pt"], r["lab_ob"]) for r in rows)
    header = "".join(f"{l:>14s}" for l in labels)
    print(f"{'':14s}{header}")
    for lp in labels:
        row_str = "".join(f"{cm[(lp, lo)]:>14d}" for lo in labels)
        print(f"{lp:14s}{row_str}")

    dis = [r for r in rows if not r["agree"]]
    agr = [r for r in rows if r["agree"]]
    m_pt_dis = np.nanmean([r["margin_pt"] for r in dis if r["margin_pt"] is not None])
    m_ob_dis = np.nanmean([r["margin_ob"] for r in dis if r["margin_ob"] is not None])
    m_pt_agr = np.nanmean([r["margin_pt"] for r in agr if r["margin_pt"] is not None])
    m_ob_agr = np.nanmean([r["margin_ob"] for r in agr if r["margin_ob"] is not None])
    print(f"\nmean decision margin (peak region - runner-up):")
    print(f"  PYTORCH:  agree-ticks {m_pt_agr:6.2f}   disagree-ticks {m_pt_dis:6.2f}")
    print(f"  1-BIT:    agree-ticks {m_ob_agr:6.2f}   disagree-ticks {m_ob_dis:6.2f}")

    print(f"\nmost common disagreement pairs (PYTORCH -> 1-BIT):")
    pairs = Counter((r["lab_pt"], r["lab_ob"]) for r in dis)
    for (lp, lo), c in pairs.most_common(8):
        print(f"  {lp:>13s} -> {lo:<13s}  {c:4d}  ({100*c/len(dis):.1f}% of disagreements)")

    dis_active = [r for r in dis if r["vad"]]
    print(f"\ndisagreements while VAD active ({len(dis_active)}/{len(dis)} of all disagreements, "
          f"{len(dis_active)}/{sum(r['vad'] for r in rows)} of VAD-active ticks):")
    pairs_active = Counter((r["lab_pt"], r["lab_ob"]) for r in dis_active)
    for (lp, lo), c in pairs_active.most_common(8):
        print(f"  {lp:>13s} -> {lo:<13s}  {c:4d}")

    print(f"\nsample disagreement ticks (first 15):")
    print(f"{'t(s)':>7} {'VAD':>5} {'hbL':>4} {'hbR':>4} {'PYTORCH':>12} {'1-BIT':>12} "
          f"{'marg_pt':>8} {'marg_ob':>8}")
    for r in dis[:15]:
        print(f"{r['t_s']:7.2f} {str(r['vad']):>5} {str(r['hb_l']):>4} {str(r['hb_r']):>4} "
              f"{r['lab_pt']:>12} {r['lab_ob']:>12} "
              f"{r['margin_pt'] if r['margin_pt'] is not None else float('nan'):8.2f} "
              f"{r['margin_ob'] if r['margin_ob'] is not None else float('nan'):8.2f}")


if __name__ == "__main__":
    main()
