"""OLD vs NEW sound-map generator — side-by-side comparison video.

Replay one bag, and at each 4 Hz tick feed the IDENTICAL 160-msg audio window to
BOTH generators, render their (64,64) maps overlaid on the room1 camera side by
side, and burn in each generator's per-map processing time (this-tick ms + running
mean) plus the 4-label decision. A header bar shows the live speedup and whether
the two labels agree. Mux with the mic audio into an mp4.

  OLD = live/acoular : soundmap_api.SoundMapAPI      (BeamformerBase.synthetic)
  NEW = offline/torch: new_soundmap_api.NewSoundMapAPI (FFT-power 2000-8000 Hz)

Head boxes + VAD are shared, so any label difference is the beamformer alone.
Self-contained: only depends on the sibling modules in generator-compare/.

    OPENBLAS_NUM_THREADS=1 python3 compare_video.py --bag G2_game3_PSSP
"""
from __future__ import annotations

import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

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

import bag_io as B
from labeling import (get_speaking_box, label_current_sm, mask_speaking_box,
                      plot_annotations, sm_to_color, transform_sm, vad_active_at)
from soundmap_api import SoundMapAPI
from new_soundmap_api import NewSoundMapAPI

# ==== defaults ============================================================
DEFAULT_ROOT = "/media/chen/Extreme SSD/PSSPData/WordWolfExp"
BAG_NAME = "G2_game3_PSSP"
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
    """One column: [title strip] / [camera + sound-map overlay + annotations] / [timing strip].

    The title and timing live in dedicated strips ABOVE and BELOW the camera panel
    so they never cover the video content or the labeling annotations."""
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
    ap.add_argument("--device", default="cpu", help="NEW generator torch device")
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

    old_api = SoundMapAPI()
    new_api = NewSoundMapAPI(device=args.device)
    speaking_box = get_speaking_box()

    # warm up both generators (acoular numba JIT + torch graph) so timings are honest
    j0 = int(np.searchsorted(a_ts, ticks[0], side="right"))
    warm = a_d[j0 - AUDIO_WIN:j0]
    for _ in range(2):
        old_api.generate(warm); new_api.generate(warm)

    panel_w = PANEL * 2 + GAP
    frame_h = HEADER_H + TITLE_H + PANEL + TIME_H
    frames = []
    last = None
    old_tot = new_tot = 0.0
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

        ta = time.perf_counter(); sm_o = old_api.generate(window); old_ms = 1e3 * (time.perf_counter() - ta)
        tb = time.perf_counter(); sm_n = new_api.generate(window); new_ms = 1e3 * (time.perf_counter() - tb)
        old_tot += old_ms; new_tot += new_ms; n_used += 1
        old_mean = old_tot / n_used; new_mean = new_tot / n_used

        c_old, lab_o = render_column(frame, sm_o, hb, va, speaking_box,
                                     "OLD  (acoular BeamformerBase)", old_ms, old_mean, (120, 70, 20))
        c_new, lab_n = render_column(frame, sm_n, hb, va, speaking_box,
                                     "NEW  (torch FFT-power)", new_ms, new_mean, (20, 90, 30))
        agree = lab_o == lab_n
        n_agree += int(agree)

        canvas = np.zeros((frame_h, panel_w, 3), np.uint8)
        canvas[HEADER_H:, 0:PANEL] = c_old
        canvas[HEADER_H:, PANEL + GAP:] = c_new

        # header bar
        _text(canvas, f"{args.bag}   t={t_s:5.2f}s   VAD {'SPEAK' if va else 'silent'}",
              (14, 40), 0.95, WHITE if va else GREY, 2)
        speedup = old_mean / new_mean if new_mean > 0 else 0.0
        _text(canvas, f"speed:  OLD {old_mean:.0f} ms   vs   NEW {new_mean:.0f} ms",
              (14, 82), 0.85, YELLOW, 2)
        _text(canvas, f"= {speedup:.1f}x faster", (600, 82), 0.85, GREEN, 2)
        agr_txt = f"label agree: {n_agree}/{n_used} ({100*n_agree/n_used:.1f}%)"
        _text(canvas, agr_txt, (14, 110), 0.7, GREEN if agree else RED, 2)
        _text(canvas, f"OLD->{lab_o}   NEW->{lab_n}", (600, 110), 0.7,
              GREEN if agree else RED, 2)
        cv2.line(canvas, (PANEL + GAP // 2, HEADER_H), (PANEL + GAP // 2, frame_h), (60, 60, 60), 1)

        last = canvas
        frames.append(canvas)
        if i % 40 == 0:
            print(f"  tick {i}/{len(ticks)}  old={old_ms:.0f}ms new={new_ms:.0f}ms "
                  f"{lab_o}/{lab_n}", flush=True)

    print(f"agreement over clip: {n_agree}/{n_used} "
          f"({100*n_agree/max(n_used,1):.2f}%);  "
          f"OLD {old_tot/max(n_used,1):.1f} ms vs NEW {new_tot/max(n_used,1):.1f} ms "
          f"= {(old_tot/new_tot):.1f}x")
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
