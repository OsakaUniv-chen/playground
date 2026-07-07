"""Room1 VAD overlay on the room1 scene (QC video).

Same scene as the vad_check videos (room1 fisheye + scrolling strip + muxed room1 mic
audio), showing the chosen room1 speech gate:
  room1 VAD = silero speech gate on the room1 mic array (room1_vad.py). Evaluated on the
  SAME GT window [t-0.46, t] per 4 Hz tick; silero itself runs streaming over the clip
  (RNN state), then intersected with each window.

Strip: gray = room1 window-RMS (reference), blue = room1 VAD speaking band, white = now.

    OPENBLAS_NUM_THREADS=1 python vad_compare.py [BAG_NAME] [START_S] [DURATION_S]

Defaults: BAG_NAME=G11_game4_DoA, START_S=60, DURATION_S=60. Output mp4 in ./out/.
"""
from __future__ import annotations

import os
import subprocess
import sys

import cv2
import numpy as np
from scipy.io import wavfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "code"))
import bag_io as B          # noqa: E402
import room1_vad as R1      # noqa: E402
import vad_overlay as VO    # noqa: E402

# ==== configure here ======================================================
BAG_NAME = "G11_game4_DoA"
START_S = 60.0
DURATION_S = 60.0

FS = 44100
CHANNELS = 16
TICK = 0.25                 # 4 Hz output-time grid
WIN_S = 0.46                # GT audio window (room1 RMS reference; VAD uses room1_vad)

FPS = 15
CAM = 720
PANEL_H = 260
PPF = 15                    # pixels per tick in the strip (window shown = CAM/PPF ticks)
# ==========================================================================

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
BLUE, GRAY, WHITE = VO.BLUE, VO.GRAY, VO.WHITE


def main():
    bag_name = sys.argv[1] if len(sys.argv) > 1 else BAG_NAME
    start_s = float(sys.argv[2]) if len(sys.argv) > 2 else START_S
    dur_s = float(sys.argv[3]) if len(sys.argv) > 3 else DURATION_S

    os.makedirs(OUT_DIR, exist_ok=True)
    con = B.open_bag(os.path.join(B.resolve_bag_root(), bag_name))
    aud_tid = B.topic_id(con, B.AUDIO_TOPIC)
    cam_tid = B.topic_id(con, B.CAMERA_TOPIC)
    if aud_tid is None or cam_tid is None:
        raise SystemExit(f"{bag_name}: missing audio or camera topic")
    a_min, a_max = con.execute(
        "SELECT MIN(timestamp), MAX(timestamp) FROM messages WHERE topic_id=?",
        (aud_tid,)).fetchone()
    t0 = int(a_min + start_s * 1e9)
    t_end = int(min(a_max, t0 + dur_s * 1e9))
    if t0 >= a_max:
        raise SystemExit(f"START_S={start_s}s past bag end ({(a_max - a_min) / 1e9:.0f}s)")

    print(f"reading audio [{start_s:.0f}s..] of {bag_name} ...")
    rows = con.execute(
        "SELECT timestamp, data FROM messages WHERE topic_id=? AND timestamp BETWEEN ? AND ? "
        "ORDER BY timestamp", (aud_tid, t0, t_end)).fetchall()
    clip_t0 = rows[0][0]
    mono = np.concatenate(
        [np.frombuffer(B.decode_audio(d), np.int16).reshape(-1, CHANNELS) for _, d in rows]
    ).astype(np.float32).mean(axis=1)               # room1 downmix
    actual_dur = len(mono) / FS
    print(f"  {len(rows)} msgs -> {actual_dur:.1f}s")

    c_ts = np.array([r[0] for r in con.execute(
        "SELECT timestamp FROM messages WHERE topic_id=? AND timestamp BETWEEN ? AND ? "
        "ORDER BY timestamp", (cam_tid, clip_t0 - int(1e9), t_end))])

    def frame_at(ts_ns):
        row = con.execute(
            "SELECT data FROM messages WHERE topic_id=? AND timestamp=? LIMIT 1",
            (cam_tid, int(ts_ns))).fetchone()
        return B.decode_compressed_image(row[0]) if row else None

    # --- tick grid: window end te = WIN_S + k*TICK (clip-relative seconds) ---
    n = int((actual_dur - WIN_S) / TICK)
    tick_ts = np.array([clip_t0 + int((WIN_S + k * TICK) * 1e9) for k in range(n)], np.int64)
    rms_db = VO.per_tick_rms(mono, FS, clip_t0, tick_ts, WIN_S)
    spk = R1.speaking_at_ticks(mono, FS, clip_t0, tick_ts)     # room1 VAD (silero, chosen)
    print(f"  room1 VAD (silero thr={R1.THRESHOLD} ratio={R1.RATIO}): "
          f"speaking {100 * spk.mean():.0f}% of ticks")

    strip, geom = VO.build_strip(rms_db, spk, PPF, PANEL_H)

    font = cv2.FONT_HERSHEY_SIMPLEX
    n_vframes = int(actual_dur * FPS)
    tmp_v = os.path.join(OUT_DIR, f"_tmp_{bag_name}.mp4")
    vw = cv2.VideoWriter(tmp_v, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (CAM, CAM + PANEL_H))
    for i in range(n_vframes):
        tau = i / FPS
        k = int(np.clip(round((tau - WIN_S) / TICK), 0, n - 1))
        ts = clip_t0 + int(tau * 1e9)
        jc = int(np.searchsorted(c_ts, ts, side="right")) - 1
        cam_img = frame_at(c_ts[jc]) if jc >= 0 else None
        cam = (cv2.resize(cam_img, (CAM, CAM)) if cam_img is not None
               else np.zeros((CAM, CAM, 3), np.uint8))

        on = bool(spk[k])
        cv2.rectangle(cam, (0, CAM - 74), (CAM, CAM), (0, 0, 0), -1)
        cv2.putText(cam, f"room1 VAD (silero): {'SPEAK' if on else 'silent'}", (12, CAM - 44),
                    font, 0.8, BLUE if on else GRAY, 2)
        cv2.putText(cam, f"room1 {rms_db[k]:5.1f} dBFS   t={tau:5.2f}s   {bag_name}",
                    (12, CAM - 14), font, 0.6, WHITE, 1)

        panel = VO.crop_panel(strip, geom, k, PPF, CAM)
        vw.write(np.vstack([cam, panel]))
        if i % (FPS * 10) == 0:
            print(f"  frame {i}/{n_vframes}", flush=True)
    vw.release()
    con.close()

    # mux room1 mic audio (peak-normalized so the quiet mic is audible)
    mono_out = (mono / (np.abs(mono).max() + 1e-9) * 0.9 * 32767).astype(np.int16)
    tmp_a = os.path.join(OUT_DIR, f"_tmp_{bag_name}.wav")
    wavfile.write(tmp_a, FS, mono_out)
    out_mp4 = os.path.join(OUT_DIR, f"{bag_name}_vad_compare.mp4")
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", tmp_v, "-i", tmp_a, "-c:v", "libx264", "-pix_fmt",
         "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", out_mp4],
        capture_output=True)
    if r.returncode == 0:
        os.remove(tmp_v)
        os.remove(tmp_a)
        print(f"wrote {out_mp4}")
    else:
        print("ffmpeg failed; kept", tmp_v, tmp_a)
        print(r.stderr.decode()[-500:])


if __name__ == "__main__":
    main()
