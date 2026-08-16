"""acoular (live generator) vs 1-bit (XOR) -- side-by-side video, on an emulated N100.

Same structure as the other two comparisons' compare_video.py: replay one bag,
and at each 4 Hz tick feed the IDENTICAL 160-msg audio window to BOTH generators,
render their (64,64) maps over the room1 camera side by side, and burn in each
generator's per-map time plus the 4-label decision. Head boxes and VAD are
shared, so any label difference is the beamformer alone.

  LEFT  = generator-acoular/soundmap_api.SoundMapAPI   (BeamformerBase.synthetic,
                                                        what the robot runs today)
  RIGHT = generator-1bit/onebit_soundmap.OneBitSoundMapAPI  (bit-shift + XOR)

What is different here: the whole process runs inside the **emulated N100**
(`n100.py` -- 4 pinned E-cores at a locked 3.4 GHz), and each column carries a
budget bar showing how much of the 250 ms tick its generator ate on that
machine. The video is the artefact for "on the box we would actually deploy,
one of these two fits the tick with room to spare and the other does not".

    python3 compare_video.py                     # emulated N100 (default)
    python3 compare_video.py --host              # the same clip on the workstation

Only the two `generate()` calls are timed; bag reading, JPEG decode and the
compositing below are not, and run on the same 4 cores.
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
import subprocess  # noqa: E402
import time  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from scipy.io import wavfile  # noqa: E402

for _p in (os.path.join(_HERE, "..", "..", "generator-acoular"),
           os.path.join(_HERE, "..", "..", "generator-1bit"),
           os.path.join(_HERE, "..")):
    _p = os.path.normpath(_p)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import utils as B  # noqa: E402
from utils import (get_speaking_box, label_current_sm, mask_speaking_box,  # noqa: E402
                   plot_annotations, sm_to_color, transform_sm, vad_active_at)
from soundmap_api import SoundMapAPI  # noqa: E402
from onebit_soundmap import OneBitSoundMapAPI  # noqa: E402

# ==== defaults ============================================================
DEFAULT_ROOT = "/media/chen/Extreme SSD/PSSPData/WordWolfExp"
BAG_NAME = "G11_game4_DoA"      # same bag/window as the other two comparisons
START_S = 40.0
DURATION_S = 40.0
TICK = 0.25                     # 4 Hz -> BUDGET_MS
BUDGET_MS = TICK * 1e3
PANEL = 620
HEADER_H = 150                  # top info bar (one line more than the siblings: the N100 state)
TITLE_H = 40
TIME_H = 84                     # timing strip + budget bar (bar at +36, caption baseline at +70)
GAP = 8
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


def budget_bar(col, y, mean_ms):
    """A 250 ms-wide bar filled to `mean_ms`. Over budget = the bar overflows, in red."""
    x0, x1 = 12, PANEL - 12
    frac = mean_ms / BUDGET_MS
    cv2.rectangle(col, (x0, y), (x1, y + 14), (55, 55, 55), -1)
    fill = int(min(frac, 1.0) * (x1 - x0))
    cv2.rectangle(col, (x0, y), (x0 + fill, y + 14), GREEN if frac <= 1.0 else RED, -1)
    cv2.rectangle(col, (x0, y), (x1, y + 14), (110, 110, 110), 1)
    _text(col, f"{100 * frac:.0f}% of the {BUDGET_MS:.0f} ms tick",
          (x0 + 6, y + 34), 0.55, GREEN if frac <= 1.0 else RED, 1)


def render_column(frame, sm, hb, vad, speaking_box, title, inst_ms, mean_ms, tint):
    """One column: [title strip] / [camera + sound-map overlay] / [timing + budget bar]."""
    sm_masked = sm if vad else mask_speaking_box(sm)
    sm_color = sm_to_color(transform_sm(sm_masked), plot_size=1080)
    label, metrics, marks = label_current_sm(sm, hb, vad, speaking_box=speaking_box)
    cam = cv2.resize(frame, (1080, 1080))
    blend = cv2.addWeighted(sm_color, 0.6, cam, 0.8, 0)
    plot_annotations(blend, label, metrics, hb, speaking_box=speaking_box, marker_points=marks)
    panel = cv2.resize(blend, (PANEL, PANEL))

    col = np.zeros((TITLE_H + PANEL + TIME_H, PANEL, 3), np.uint8)
    cv2.rectangle(col, (0, 0), (PANEL, TITLE_H), tint, -1)
    _text(col, title, (12, 28), 0.72, WHITE, 2)
    col[TITLE_H:TITLE_H + PANEL] = panel
    ty = TITLE_H + PANEL
    cv2.rectangle(col, (0, ty), (PANEL, ty + TIME_H), (25, 25, 25), -1)
    _text(col, f"{mean_ms:6.1f} ms/map", (12, ty + 26), 0.9, YELLOW, 2)
    _text(col, f"(this tick {inst_ms:5.1f} ms)", (250, ty + 24), 0.55, GREY, 1)
    budget_bar(col, ty + 36, mean_ms)
    return col, label


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rosbag-root", default=DEFAULT_ROOT)
    ap.add_argument("--bag", default=BAG_NAME)
    ap.add_argument("--start", type=float, default=START_S)
    ap.add_argument("--dur", type=float, default=DURATION_S)
    ap.add_argument("--out", default=os.path.join(_HERE, "compare-generator-n100.mp4"))
    n100.add_arguments(ap)
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

    ac_api = SoundMapAPI()
    ob_api = OneBitSoundMapAPI(device="cpu")
    speaking_box = get_speaking_box()

    # warm up both (numba JIT, LUT/Delaunay caches) so the burned-in times are honest
    j0 = int(np.searchsorted(a_ts, ticks[0], side="right"))
    warm = a_d[j0 - AUDIO_WIN:j0]
    for _ in range(3):
        ac_api.generate(warm); ob_api.generate(warm)

    machine = n100.label()
    clock_note = ""
    if n100.STATE["active"] and not n100.STATE["freq_locked"]:
        clock_note = "  [clock NOT locked -- optimistic]"

    panel_w = PANEL * 2 + GAP
    frame_h = HEADER_H + TITLE_H + PANEL + TIME_H
    frames = []
    last = None
    ac_tot = ob_tot = 0.0
    n_used = n_agree = 0
    sampler = n100.FreqSampler(n100.STATE["cores"] or (0,)).start()
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

        ta = time.perf_counter(); sm_ac = ac_api.generate(window); ac_ms = 1e3 * (time.perf_counter() - ta)
        tb = time.perf_counter(); sm_ob = ob_api.generate(window); ob_ms = 1e3 * (time.perf_counter() - tb)
        ac_tot += ac_ms; ob_tot += ob_ms; n_used += 1
        ac_mean = ac_tot / n_used; ob_mean = ob_tot / n_used

        c_ac, lab_ac = render_column(frame, sm_ac, hb, va, speaking_box,
                                     "ACOULAR  (BeamformerBase, live robot)",
                                     ac_ms, ac_mean, (25, 55, 130))
        c_ob, lab_ob = render_column(frame, sm_ob, hb, va, speaking_box,
                                     "1-BIT  (bit-shift + XOR)",
                                     ob_ms, ob_mean, (20, 90, 30))
        agree = lab_ac == lab_ob
        n_agree += int(agree)

        canvas = np.zeros((frame_h, panel_w, 3), np.uint8)
        canvas[HEADER_H:, 0:PANEL] = c_ac
        canvas[HEADER_H:, PANEL + GAP:] = c_ob

        _text(canvas, f"{args.bag}   t={t_s:5.2f}s   VAD {'SPEAK' if va else 'silent'}",
              (14, 34), 0.9, WHITE if va else GREY, 2)
        _text(canvas, f"machine: {machine}{clock_note}", (14, 66), 0.62,
              YELLOW if n100.STATE["active"] else GREY, 1)
        speedup = ac_mean / ob_mean if ob_mean > 0 else 0.0
        speed_txt = f"speed:  ACOULAR {ac_mean:.0f} ms   vs   1-BIT {ob_mean:.0f} ms"
        _text(canvas, speed_txt, (14, 100), 0.82, YELLOW, 2)
        (speed_w, _), _ = cv2.getTextSize(speed_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.82, 2)
        _text(canvas, f"= {speedup:.1f}x", (14 + speed_w + 24, 100), 0.82, GREEN, 2)
        agr_txt = f"label agree: {n_agree}/{n_used} ({100 * n_agree / n_used:.1f}%)"
        _text(canvas, agr_txt, (14, 132), 0.68, GREEN if agree else RED, 2)
        _text(canvas, f"ACOULAR->{lab_ac}   1-BIT->{lab_ob}", (600, 132), 0.68,
              GREEN if agree else RED, 2)
        cv2.line(canvas, (PANEL + GAP // 2, HEADER_H), (PANEL + GAP // 2, frame_h), (60, 60, 60), 1)

        last = canvas
        frames.append(canvas)
        if i % 40 == 0:
            print(f"  tick {i}/{len(ticks)}  acoular={ac_ms:.0f}ms 1bit={ob_ms:.0f}ms "
                  f"{lab_ac}/{lab_ob}", flush=True)

    clock = sampler.stop()
    print(f"agreement over clip: {n_agree}/{n_used} ({100 * n_agree / max(n_used, 1):.2f}%);  "
          f"ACOULAR {ac_tot / max(n_used, 1):.1f} ms vs 1-BIT {ob_tot / max(n_used, 1):.1f} ms "
          f"= {(ac_tot / max(ob_tot, 1e-9)):.1f}x   [{machine}]")
    if clock.get("mean_mhz"):
        print(f"clock under load: {clock['mean_mhz']:.0f} MHz mean "
              f"({clock['min_mhz']:.0f}-{clock['max_mhz']:.0f})")
    con.close()

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
