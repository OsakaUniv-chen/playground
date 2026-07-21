"""FFT beamformer (pytorch) vs 1-bit (XOR) sound-map generator — side-by-side video.

Replay one bag, and at each 4 Hz tick feed the IDENTICAL 160-msg audio window to
BOTH generators, render their (64,64) maps overlaid on the room1 camera side by
side, and burn in each generator's per-map processing time (this-tick ms + running
mean) plus the 4-label decision. A header bar shows the live speedup and whether
the two labels agree. Mux with the mic audio into an mp4.

  LEFT  = generator-pytorch/new_soundmap_api.NewSoundMapAPI (FFT power, 2000-8000Hz)
                                                             GPU by default (--pytorch-device)
  RIGHT = onebit_soundmap.OneBitSoundMapAPI                 (bit-shift + XOR, always CPU --
                                                             not needing a GPU is the point)

Each side runs on the device it's actually meant for, not a matched device
for benchmarking symmetry: the FFT beamformer normally runs on GPU when one
is available (`--pytorch-device cuda`, the default), the 1-bit generator
never uses one (`OneBitSoundMapGenerator` has no GPU path at all). Pass
`--pytorch-device cpu` to instead compare both on CPU only.

Head boxes + VAD are shared, so any label difference is the beamforming
algorithm alone. Same structure as acoular-vs-pytorch/compare_video.py.
The generators are imported from their own folders (onebit from
../../generator-1bit, the FFT/pytorch one from ../../generator-pytorch) and the
bag-reader / labeling helpers from ../utils.py -- nothing is duplicated here.

    python3 compare_video.py --bag G11_game4_DoA

Deliberately does NOT cap OPENBLAS_NUM_THREADS/OMP_NUM_THREADS the way
acoular-vs-pytorch/compare_video.py does -- that cap exists there to avoid
oversubscription when compare_generators.py runs many bags in parallel
worker PROCESSES. This script is single-process, and capping OMP threads
also throttles the FFT beamformer's torch CPU einsum path down to 1 thread
(measured: ~60ms/map capped vs ~18-21ms/map uncapped on this machine's 22
cores, when --pytorch-device is cpu) -- pure CPU threading, unrelated to the
GPU.
"""
from __future__ import annotations

import os

import argparse
import subprocess
import sys
import time

import cv2
import numpy as np
from scipy.io import wavfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
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
from utils import (get_speaking_box, label_current_sm, mask_speaking_box,
                      plot_annotations, sm_to_color, transform_sm, vad_active_at)
from new_soundmap_api import NewSoundMapAPI as SoundMapAPI
from onebit_soundmap import OneBitSoundMapAPI

# ==== defaults ============================================================
DEFAULT_ROOT = "/media/chen/Extreme SSD/PSSPData/WordWolfExp"   # post-2026-07 PSSPData reorg
BAG_NAME = "G11_game4_DoA"
START_S = 40.0        # skip the first START_S seconds (buffer fill + settling)
DURATION_S = 40.0     # length of the clip
TICK = 0.25           # 4 Hz
PANEL = 620           # each sound-map (camera) panel is PANEL x PANEL
HEADER_H = 118        # top info bar
TITLE_H = 40          # per-column generator-name strip (above the camera, not overlaid)
TIME_H = 48           # per-column timing strip (below the camera, not overlaid)
GAP = 8               # gap between the two panels
FS = 44100
CHANNELS = 16
AUDIO_WIN = 160
# ==========================================================================

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GREEN = (80, 230, 80)
RED = (60, 60, 235)
YELLOW = (40, 210, 235)
GREY = (150, 150, 150)


def _text(img, s, pos, scale, color, thick=2):
    cv2.putText(img, s, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, BLACK, thick + 2, cv2.LINE_AA)
    cv2.putText(img, s, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def render_column(frame, sm, hb, vad, speaking_box, title, inst_ms, mean_ms, tint):
    """One column: [title strip] / [camera + sound-map overlay + annotations] / [timing strip]."""
    sm_masked = sm if vad else mask_speaking_box(sm)
    sm_color = sm_to_color(transform_sm(sm_masked), plot_size=1080)
    label, metrics, marks = label_current_sm(sm, hb, vad, speaking_box=speaking_box)
    cam = cv2.resize(frame, (1080, 1080))
    blend = cv2.addWeighted(sm_color, 0.6, cam, 0.8, 0)
    plot_annotations(blend, label, metrics, hb, speaking_box=speaking_box, marker_points=marks)
    panel = cv2.resize(blend, (PANEL, PANEL))

    col = np.zeros((TITLE_H + PANEL + TIME_H, PANEL, 3), np.uint8)
    cv2.rectangle(col, (0, 0), (PANEL, TITLE_H), tint, -1)          # title strip (above)
    _text(col, title, (12, 28), 0.82, WHITE, 2)
    col[TITLE_H:TITLE_H + PANEL] = panel                            # untouched camera panel
    ty = TITLE_H + PANEL
    cv2.rectangle(col, (0, ty), (PANEL, ty + TIME_H), (25, 25, 25), -1)  # timing strip (below)
    _text(col, f"{mean_ms:6.1f} ms/map", (12, ty + 34), 1.0, YELLOW, 2)
    _text(col, f"(this tick {inst_ms:5.1f} ms)", (272, ty + 32), 0.6, GREY, 1)
    return col, label


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rosbag-root", default=DEFAULT_ROOT)
    ap.add_argument("--bag", default=BAG_NAME)
    ap.add_argument("--start", type=float, default=START_S)
    ap.add_argument("--dur", type=float, default=DURATION_S)
    ap.add_argument("--pytorch-device", default="cuda",
                    help="device for the FFT beamformer (default cuda, its normal deployment "
                         "target when available). The 1-bit generator is always CPU -- that's "
                         "the whole point of the architecture, not a benchmarking choice.")
    ap.add_argument("--out", default=os.path.join(_HERE, "compare-generator.mp4"))
    args = ap.parse_args()

    bag = os.path.join(args.rosbag_root, args.bag)
    if not os.path.isdir(bag):
        raise SystemExit(f"bag not found: {bag}")
    con = B.open_bag(bag)

    print("reading topics...")
    audio = B.read_series(con, B.AUDIO_TOPIC)
    a_ts = np.array([t for t, _ in audio]); a_d = [d for _, d in audio]
    cam_tid = B.topic_id(con, B.CAMERA_TOPIC)
    c_ts = np.array([r[0] for r in con.execute(
        "SELECT timestamp FROM messages WHERE topic_id=? ORDER BY timestamp", (cam_tid,))])
    head = [(t, v) for t, v in B.read_series(con, B.HEAD_TOPIC) if v and len(v) >= 8]
    h_ts = np.array([t for t, _ in head])
    vad = B.read_series(con, B.VAD_TOPIC)
    vts = [t / 1e9 for t, _ in vad]; vval = [bool(v) for _, v in vad]
    print(f"  audio={len(a_ts)} cam={len(c_ts)} head={len(h_ts)} vad={len(vts)}")

    def frame_at(ts_ns):
        row = con.execute("SELECT data FROM messages WHERE topic_id=? AND timestamp=? LIMIT 1",
                          (cam_tid, int(ts_ns))).fetchone()
        return B.decode_compressed_image(row[0]) if row else None

    t0 = int(a_ts[0] + args.start * 1e9)
    t_end = int(min(a_ts[-1], c_ts[-1], t0 + args.dur * 1e9))
    ticks = np.arange(t0, t_end, int(TICK * 1e9))
    print(f"{len(ticks)} ticks over {args.dur:.0f}s")

    pt_api = SoundMapAPI(device=args.pytorch_device)
    ob_api = OneBitSoundMapAPI(device="cpu")
    print(f"PYTORCH generator device: {pt_api.device}   1-BIT generator device: {ob_api.device}")
    speaking_box = get_speaking_box()

    # warm up both generators so timings are honest
    j0 = int(np.searchsorted(a_ts, ticks[0], side="right"))
    warm = a_d[j0 - AUDIO_WIN:j0]
    for _ in range(2):
        pt_api.generate(warm); ob_api.generate(warm)

    panel_w = PANEL * 2 + GAP
    frame_h = HEADER_H + TITLE_H + PANEL + TIME_H
    frames = []
    last = None
    pt_tot = ob_tot = 0.0
    n_used = 0
    n_agree = 0
    for i, t in enumerate(ticks):
        t_s = (t - t0) / 1e9
        ja = int(np.searchsorted(a_ts, t, side="right"))
        jc = int(np.searchsorted(c_ts, t, side="right")) - 1
        frame = frame_at(c_ts[jc]) if jc >= 0 else None
        if ja < AUDIO_WIN or frame is None:
            if last is not None:
                frames.append(last)
            continue
        window = a_d[ja - AUDIO_WIN:ja]
        jh = int(np.searchsorted(h_ts, t, side="right")) - 1
        hb = ([list(head[jh][1][0:4]), list(head[jh][1][4:8])] if jh >= 0
              else [[-99] * 4, [-99] * 4])
        va = vad_active_at(vts, vval, t / 1e9)

        ta = time.perf_counter(); sm_pt = pt_api.generate(window); pt_ms = 1e3 * (time.perf_counter() - ta)
        tb = time.perf_counter(); sm_ob = ob_api.generate(window); ob_ms = 1e3 * (time.perf_counter() - tb)
        pt_tot += pt_ms; ob_tot += ob_ms; n_used += 1
        pt_mean = pt_tot / n_used; ob_mean = ob_tot / n_used

        c_pt, lab_pt = render_column(frame, sm_pt, hb, va, speaking_box,
                                     f"PYTORCH  (FFT beamformer, {pt_api.device.upper()})",
                                     pt_ms, pt_mean, (120, 70, 20))
        c_ob, lab_ob = render_column(frame, sm_ob, hb, va, speaking_box,
                                     f"1-BIT  (bit-shift + XOR, {ob_api.device.upper()})",
                                     ob_ms, ob_mean, (20, 90, 30))
        agree = lab_pt == lab_ob
        n_agree += int(agree)

        canvas = np.zeros((frame_h, panel_w, 3), np.uint8)
        canvas[HEADER_H:, 0:PANEL] = c_pt
        canvas[HEADER_H:, PANEL + GAP:] = c_ob

        # header bar
        _text(canvas, f"{args.bag}   t={t_s:5.2f}s   VAD {'SPEAK' if va else 'silent'}",
              (14, 40), 0.95, WHITE if va else GREY, 2)
        speedup = pt_mean / ob_mean if ob_mean > 0 else 0.0
        speed_txt = f"speed:  PYTORCH {pt_mean:.0f} ms   vs   1-BIT {ob_mean:.0f} ms"
        _text(canvas, speed_txt, (14, 82), 0.85, YELLOW, 2)
        (speed_w, _), _ = cv2.getTextSize(speed_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.85, 2)
        _text(canvas, f"= {speedup:.1f}x", (14 + speed_w + 24, 82), 0.85, GREEN, 2)
        agr_txt = f"label agree: {n_agree}/{n_used} ({100*n_agree/n_used:.1f}%)"
        _text(canvas, agr_txt, (14, 110), 0.7, GREEN if agree else RED, 2)
        _text(canvas, f"PYTORCH->{lab_pt}   1-BIT->{lab_ob}", (600, 110), 0.7,
              GREEN if agree else RED, 2)
        cv2.line(canvas, (PANEL + GAP // 2, HEADER_H), (PANEL + GAP // 2, frame_h), (60, 60, 60), 1)

        last = canvas
        frames.append(canvas)
        if i % 40 == 0:
            print(f"  tick {i}/{len(ticks)}  pt={pt_ms:.0f}ms 1bit={ob_ms:.0f}ms "
                  f"{lab_pt}/{lab_ob}", flush=True)

    print(f"agreement over clip: {n_agree}/{n_used} "
          f"({100*n_agree/max(n_used,1):.2f}%);  "
          f"PYTORCH {pt_tot/max(n_used,1):.1f} ms vs 1-BIT {ob_tot/max(n_used,1):.1f} ms "
          f"= {(pt_tot/max(ob_tot,1e-9)):.1f}x")
    con.close()

    # write video @ 4 fps (real time), then mux mic audio
    tmp_v = os.path.join(_HERE, f"_tmp_{args.bag}.mp4")
    vw = cv2.VideoWriter(tmp_v, cv2.VideoWriter_fourcc(*"mp4v"), 1.0 / TICK, (panel_w, frame_h))
    for f in frames:
        vw.write(f)
    vw.release()

    seg = [a_d[k] for k in range(len(a_ts)) if t0 <= a_ts[k] <= t_end]
    audio_np = np.concatenate([np.frombuffer(b, np.int16).reshape(-1, CHANNELS) for b in seg])
    mono = audio_np.astype(np.float32).mean(axis=1) * 10 ** (30 / 20)
    mono = np.clip(mono, -32768, 32767).astype(np.int16)
    tmp_a = os.path.join(_HERE, f"_tmp_{args.bag}.wav")
    wavfile.write(tmp_a, FS, mono)

    r = subprocess.run(["ffmpeg", "-y", "-i", tmp_v, "-i", tmp_a, "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", args.out],
                       capture_output=True)
    if r.returncode == 0:
        os.remove(tmp_v); os.remove(tmp_a)
        print(f"wrote {args.out}")
    else:
        print("ffmpeg failed; kept", tmp_v, tmp_a)
        print(r.stderr.decode()[-800:])


if __name__ == "__main__":
    main()
