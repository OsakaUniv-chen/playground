"""WP2 step 0 — sound-map QC video.

Replay one bag (hardcoded below), regenerate the sound map at each 4 Hz tick,
overlay it on the room1 camera with the 4-label / head boxes / speaking box /
VAD state, and mux with the mic audio into an mp4. Watch it to confirm the
regenerated sound map actually tracks the speaker BEFORE trusting the numbers.

Reference: train_orig_sm_target_sm_gray/11/bag2Video.py (audio reconstruction +
ffmpeg muxing). Here we additionally overlay the regenerated SM.

    OPENBLAS_NUM_THREADS=1 python bag2video.py
"""
from __future__ import annotations

import os
import subprocess
import sys

import cv2
import numpy as np
from scipy.io import wavfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bag_io as B
import room1_vad as R1
import vad_overlay as VO
from labeling import (label_current_sm, mask_speaking_box, plot_annotations,
                      sm_to_color, transform_sm, vad_active_at, get_speaking_box)
from soundmap_api import SoundMapAPI

# ==== configure here (defaults; optional argv: BAG_NAME START_S DURATION_S) ===
BAG = os.path.join(B.resolve_bag_root(), "G11_game4_DoA")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "results", "qc_video")
START_S = 30.0     # skip the first START_S seconds (buffer fill + settling)
DURATION_S = 60.0  # length of the QC clip
TICK = 0.25        # 4 Hz
PLOT_SIZE = 720    # output frame size
PANEL_H = 260      # room1-VAD + 4-label strip height (added below the sound-map frame)
PPF = 12           # strip pixels per tick (window shown = PLOT_SIZE/PPF ticks)
FS = 44100
CHANNELS = 16
if len(sys.argv) > 1:
    BAG = os.path.join(B.resolve_bag_root(), sys.argv[1])
if len(sys.argv) > 2:
    START_S = float(sys.argv[2])
if len(sys.argv) > 3:
    DURATION_S = float(sys.argv[3])
# ==========================================================================


def main():
    os.makedirs(OUT, exist_ok=True)
    name = os.path.basename(BAG)
    con = B.open_bag(BAG)

    print("reading topics...")
    audio = B.read_series(con, B.AUDIO_TOPIC)
    a_ts = np.array([t for t, _ in audio]); a_d = [d for _, d in audio]
    # camera: only timestamps up front; decode the nearest frame per tick lazily (avoids OOM)
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

    t0 = int(a_ts[0] + START_S * 1e9)
    t_end = int(min(a_ts[-1], c_ts[-1], t0 + DURATION_S * 1e9))
    ticks = np.arange(t0, t_end, int(TICK * 1e9))
    print(f"{len(ticks)} ticks over {DURATION_S:.0f}s")

    # room1 VAD strip (silero, chosen gate) — RMS envelope + speaking band per tick
    lo = int(np.searchsorted(a_ts, t0 - int(1e9)))
    hi = int(np.searchsorted(a_ts, t_end, side="right"))
    mono_s = np.concatenate([np.frombuffer(b, np.int16).reshape(-1, CHANNELS)
                             for b in a_d[lo:hi]]).astype(np.float32).mean(axis=1)
    rms_db = VO.per_tick_rms(mono_s, FS, int(a_ts[lo]), ticks, 0.46)
    spk = R1.speaking_at_ticks(mono_s, FS, int(a_ts[lo]), ticks)
    strip, geom = VO.build_strip(rms_db, spk, PPF, PANEL_H, VO.LABEL_BARS)
    print(f"room1 speaking: {100 * spk.mean():.0f}% of ticks")

    api = SoundMapAPI()
    speaking_box = get_speaking_box()
    frames = []
    last_frame = None                 # reused if a tick has no decodable camera frame, so
    for i, t in enumerate(ticks):     # len(frames) == len(ticks) and the 4 fps stays real-time
        ja = int(np.searchsorted(a_ts, t, side="right"))
        jc = int(np.searchsorted(c_ts, t, side="right")) - 1
        frame = frame_at(c_ts[jc]) if jc >= 0 else None
        if ja < 160 or frame is None:                 # no window / no frame this tick:
            if last_frame is not None:                # repeat the previous frame instead of
                frames.append(last_frame)             # dropping it (which would shift audio sync)
            continue
        window = a_d[ja - 160:ja]                     # GT view: window ends at tick t
        jh = int(np.searchsorted(h_ts, t, side="right")) - 1
        hb = ([list(head[jh][1][0:4]), list(head[jh][1][4:8])] if jh >= 0
              else [[-99] * 4, [-99] * 4])
        va = vad_active_at(vts, vval, t / 1e9)

        sm = api.generate(window)
        sm_masked = sm if va else mask_speaking_box(sm)
        sm_color = sm_to_color(transform_sm(sm_masked), plot_size=1080)
        label, metrics, marks = label_current_sm(sm, hb, va, speaking_box=speaking_box)
        VO.paint_label(strip, geom, i, label, PPF)     # 4-label detection bars

        cam_resized = cv2.resize(frame, (1080, 1080))
        blend = cv2.addWeighted(sm_color, 0.6, cam_resized, 0.8, 0)
        plot_annotations(blend, label, metrics, hb, speaking_box=speaking_box, marker_points=marks)
        cv2.putText(blend, f"room1 VAD {'SPEAK' if spk[i] else 'silent'}  t={(t-t0)/1e9:5.2f}s",
                    (10, 1050), cv2.FONT_HERSHEY_SIMPLEX, 1.4,
                    (0, 255, 0) if spk[i] else (0, 0, 255), 3)
        scene = cv2.resize(blend, (PLOT_SIZE, PLOT_SIZE))
        last_frame = np.vstack([scene, VO.crop_panel(strip, geom, i, PPF, PLOT_SIZE)])
        frames.append(last_frame)
        if i % 40 == 0:
            print(f"  tick {i}/{len(ticks)}", flush=True)
    con.close()

    # video @ 4 fps = real time (one tick per frame)
    tmp_v = os.path.join(OUT, f"_tmp_{name}.mp4")
    vw = cv2.VideoWriter(tmp_v, cv2.VideoWriter_fourcc(*"mp4v"), 1.0 / TICK,
                         (PLOT_SIZE, PLOT_SIZE + PANEL_H))
    for f in frames:
        vw.write(f)
    vw.release()

    # audio over [t0, t_end], downmix 16ch -> mono, +30 dB
    seg = [a_d[k] for k in range(len(a_ts)) if t0 <= a_ts[k] <= t_end]
    audio_np = np.concatenate([np.frombuffer(b, np.int16).reshape(-1, CHANNELS) for b in seg])
    mono = audio_np.astype(np.float32).mean(axis=1)
    mono *= 10 ** (30 / 20)
    mono = np.clip(mono, -32768, 32767).astype(np.int16)
    tmp_a = os.path.join(OUT, f"_tmp_{name}.wav")
    wavfile.write(tmp_a, FS, mono)

    out_mp4 = os.path.join(OUT, f"{name}_sm_qc.mp4")
    r = subprocess.run(["ffmpeg", "-y", "-i", tmp_v, "-i", tmp_a, "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", out_mp4],
                       capture_output=True)
    if r.returncode == 0:
        os.remove(tmp_v); os.remove(tmp_a)
        print(f"wrote {out_mp4}")
    else:
        print("ffmpeg failed; kept", tmp_v, tmp_a)
        print(r.stderr.decode()[-500:])


if __name__ == "__main__":
    main()
