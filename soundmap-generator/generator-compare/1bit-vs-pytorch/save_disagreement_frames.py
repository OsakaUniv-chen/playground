"""Dump one annotated frame per disagreement tick (PYTORCH label != 1-BIT label),
sorted into results/disagreement_frames/vad_active/ and .../vad_silent/ for
manual visual inspection. Same rendering as compare_video.py's columns (camera
+ sound-map overlay + head boxes + speaking box + region values), just written
to PNGs instead of an mp4, and only for the ticks that actually disagree.

    python3 save_disagreement_frames.py --bag G11_game4_DoA
"""
from __future__ import annotations

import argparse
import os
import sys

import cv2
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
# Reused generators + shared utils now live outside this folder:
#   ../../generator-pytorch/new_soundmap_api.py     (FFT/pytorch reference generator)
#   ../../generator-1bit/onebit_soundmap.py          (the 1-bit generator)
#   ../utils.py                                       (shared comparison helpers)
for _p in (os.path.join(_HERE, "..", "..", "generator-pytorch"),
           os.path.join(_HERE, "..", "..", "generator-1bit"),
           os.path.join(_HERE, "..")):
    _p = os.path.normpath(_p)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import utils as B
from utils import get_speaking_box, label_current_sm, vad_active_at
from new_soundmap_api import NewSoundMapAPI as SoundMapAPI
from onebit_soundmap import OneBitSoundMapAPI
from compare_video import (PANEL, HEADER_H, TITLE_H, TIME_H, GAP, WHITE, GREY,
                           YELLOW, GREEN, _text, render_column)

DEFAULT_ROOT = "/media/chen/Extreme SSD/PSSPData/WordWolfExp"
TICK = 0.25
AUDIO_WIN = 160
OUT_ROOT = os.path.join(_HERE, "results", "disagreement_frames")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rosbag-root", default=DEFAULT_ROOT)
    ap.add_argument("--bag", default="G11_game4_DoA")
    ap.add_argument("--start", type=float, default=10.0)
    ap.add_argument("--dur", type=float, default=0.0, help="0 = whole bag")
    ap.add_argument("--pytorch-device", default="cuda")
    ap.add_argument("--out", default=OUT_ROOT)
    args = ap.parse_args()

    active_dir = os.path.join(args.out, "vad_active")
    silent_dir = os.path.join(args.out, "vad_silent")
    os.makedirs(active_dir, exist_ok=True)
    os.makedirs(silent_dir, exist_ok=True)

    bag = os.path.join(args.rosbag_root, args.bag)
    con = B.open_bag(bag)
    audio = B.read_series(con, B.AUDIO_TOPIC)
    a_ts = np.array([t for t, _ in audio]); a_d = [d for _, d in audio]
    cam_tid = B.topic_id(con, B.CAMERA_TOPIC)
    c_ts = np.array([r[0] for r in con.execute(
        "SELECT timestamp FROM messages WHERE topic_id=? ORDER BY timestamp", (cam_tid,))])
    head = [(t, v) for t, v in B.read_series(con, B.HEAD_TOPIC) if v and len(v) >= 8]
    h_ts = np.array([t for t, _ in head])
    vad = B.read_series(con, B.VAD_TOPIC)
    vts = [t / 1e9 for t, _ in vad]; vval = [bool(v) for _, v in vad]

    def frame_at(ts_ns):
        row = con.execute("SELECT data FROM messages WHERE topic_id=? AND timestamp=? LIMIT 1",
                          (cam_tid, int(ts_ns))).fetchone()
        return B.decode_compressed_image(row[0]) if row else None

    t0 = int(a_ts[0] + args.start * 1e9)
    t_end = int(a_ts[-1]) if args.dur <= 0 else int(min(a_ts[-1], t0 + args.dur * 1e9))
    ticks = np.arange(t0, t_end, int(TICK * 1e9))
    print(f"{args.bag}: {len(ticks)} ticks over {(t_end - t0) / 1e9:.0f}s")

    pt_api = SoundMapAPI(device=args.pytorch_device)
    ob_api = OneBitSoundMapAPI(device="cpu")
    speaking_box = get_speaking_box()

    panel_w = PANEL * 2 + GAP
    frame_h = HEADER_H + TITLE_H + PANEL + TIME_H
    n_used = n_dis = n_active = n_silent = 0
    for t in ticks:
        t_s = (t - t0) / 1e9
        ja = int(np.searchsorted(a_ts, t, side="right"))
        jc = int(np.searchsorted(c_ts, t, side="right")) - 1
        if ja < AUDIO_WIN or jc < 0:
            continue
        window = a_d[ja - AUDIO_WIN:ja]
        jh = int(np.searchsorted(h_ts, t, side="right")) - 1
        hb = ([list(head[jh][1][0:4]), list(head[jh][1][4:8])] if jh >= 0
              else [[-99] * 4, [-99] * 4])
        va = vad_active_at(vts, vval, t / 1e9)

        sm_pt = pt_api.generate(window)
        sm_ob = ob_api.generate(window)
        # peek the label without rendering, to skip agreeing ticks cheaply
        lab_pt, _, _ = label_current_sm(sm_pt, hb, va, speaking_box=speaking_box)
        lab_ob, _, _ = label_current_sm(sm_ob, hb, va, speaking_box=speaking_box)
        n_used += 1
        if lab_pt == lab_ob:
            continue
        n_dis += 1

        frame = frame_at(c_ts[jc])
        if frame is None:
            continue

        c_pt, _ = render_column(frame, sm_pt, hb, va, speaking_box,
                                f"PYTORCH  (FFT beamformer, {pt_api.device.upper()})",
                                0.0, 0.0, (120, 70, 20))
        c_ob, _ = render_column(frame, sm_ob, hb, va, speaking_box,
                                f"1-BIT  (bit-shift + XOR, {ob_api.device.upper()})",
                                0.0, 0.0, (20, 90, 30))
        canvas = np.zeros((frame_h, panel_w, 3), np.uint8)
        canvas[HEADER_H:, 0:PANEL] = c_pt
        canvas[HEADER_H:, PANEL + GAP:] = c_ob
        _text(canvas, f"{args.bag}   t={t_s:7.2f}s   VAD {'SPEAK' if va else 'silent'}",
              (14, 40), 0.95, WHITE if va else GREY, 2)
        _text(canvas, f"PYTORCH -> {lab_pt}      1-BIT -> {lab_ob}", (14, 90), 0.9, YELLOW, 2)
        cv2.line(canvas, (PANEL + GAP // 2, HEADER_H), (PANEL + GAP // 2, frame_h), (60, 60, 60), 1)

        sub = active_dir if va else silent_dir
        fname = f"t{t_s:07.2f}s_PT-{lab_pt}_1BIT-{lab_ob}.png"
        cv2.imwrite(os.path.join(sub, fname), canvas)
        if va:
            n_active += 1
        else:
            n_silent += 1

    con.close()
    print(f"\n{n_dis}/{n_used} ticks disagreed "
          f"({100*n_dis/max(n_used,1):.1f}%)")
    print(f"saved {n_active} frames -> {active_dir}")
    print(f"saved {n_silent} frames -> {silent_dir}")


if __name__ == "__main__":
    main()
