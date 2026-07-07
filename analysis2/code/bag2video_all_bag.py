"""WP2 step 0 (batch) — sound-map QC videos for every robot bag.

Same per-tick pipeline as bag2video.py (regenerate the 4 Hz sound map, overlay it
on the room1 camera with the 4-label / head boxes / speaking box / VAD state, mux
with mic audio), but run over ALL robot bags under the ROSbag root instead of one
hardcoded bag. Watch the clips to confirm the regenerated sound map tracks the
speaker BEFORE trusting the numbers.

Differences vs bag2video.py:
  * discovers every G*_game{3-6}_{Tele,PSSP,DoA,Random} bag and loops over them;
  * resamples the room1 camera onto a UNIFORM real-time grid at its healthy cadence
    (~29.9 fps) and HOLDS the last frame across dropped-frame gaps, so video-time
    stays locked to real-time (= audio-time): a camera dropout shows as a freeze
    instead of drifting the audio (groups 12-13 dropped tens of seconds of frames).
    The sound map still refreshes only on the 4 Hz tick grid (per-frame beamforming
    would be ~7x the cost and the live system updates the SM at 4 Hz too); each output
    frame is overlaid with the most-recent tick's SM;
  * defaults to the FULL bag (start-skip only), so frames are STREAMED straight to
    the VideoWriter instead of held in a list (a full game is GBs of frames);
  * one SoundMapAPI is built once and reused across bags;
  * overwrites by default (re-renders every bag); pass --skip-existing to resume a
    run and skip bags that already have a {name}_sm_qc.mp4.

    OPENBLAS_NUM_THREADS=1 python bag2video_all_bag.py                  # all 52, full bag
    OPENBLAS_NUM_THREADS=1 python bag2video_all_bag.py --modes DoA      # DoA only
    OPENBLAS_NUM_THREADS=1 python bag2video_all_bag.py --duration 60    # 60s samples
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import traceback

import cv2
import numpy as np
from scipy.io import wavfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bag_io as B
import room1_vad as R1
import vad_overlay as VO
from bag_io import DIR_RE
from labeling import (get_speaking_box, label_current_sm, mask_speaking_box,
                      plot_annotations, sm_to_color, transform_sm, vad_active_at)
from soundmap_api import SoundMapAPI

# ==== defaults (override on the CLI) ======================================
BAG_ROOT = B.resolve_bag_root()    # /media/chen/... (Linux) else /Volumes/... (Mac)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "qc_video")
ROBOT_MODES = ("Tele", "PSSP", "DoA", "Random")   # game3-6, 4 conditions -> 52 bags
START_S = 30.0     # skip the first START_S seconds (buffer fill + settling)
DURATION_S = None  # None = to end of bag; otherwise clip length in seconds
TICK = 0.25        # 4 Hz
PLOT_SIZE = 720    # output frame size
PANEL_H = 260      # room1-VAD + 4-label strip height (added below the sound-map frame)
PPF = 12           # strip pixels per tick (window shown = PLOT_SIZE/PPF ticks)
FS = 44100
CHANNELS = 16
# ==========================================================================


def discover_bags(root: str, modes) -> list[str]:
    """Return robot-bag paths under `root` whose mode is in `modes`, sorted by (group, game)."""
    modes = set(modes)
    hits = []
    for name in os.listdir(root):
        m = DIR_RE.match(name)
        if not m or m["mode"] not in modes:
            continue
        if not os.path.isdir(os.path.join(root, name)):
            continue
        hits.append((int(m["group"]), int(m["game"]), name))
    hits.sort()
    return [os.path.join(root, n) for _, _, n in hits]


def process_bag(bag_path, api, speaking_box, out_dir,
                start_s, duration_s, tick, plot_size, overwrite, fps=None) -> str:
    """Render + mux one bag. Returns 'ok' | 'skip' | 'empty'. Raises on hard error."""
    name = os.path.basename(bag_path)
    out_mp4 = os.path.join(out_dir, f"{name}_sm_qc.mp4")
    if os.path.exists(out_mp4) and not overwrite:
        return "skip"

    con = B.open_bag(bag_path)
    try:
        audio = B.read_series(con, B.AUDIO_TOPIC)
        a_ts = np.array([t for t, _ in audio]); a_d = [d for _, d in audio]
        cam_tid = B.topic_id(con, B.CAMERA_TOPIC)
        c_ts = np.array([r[0] for r in con.execute(
            "SELECT timestamp FROM messages WHERE topic_id=? ORDER BY timestamp", (cam_tid,))]
        ) if cam_tid is not None else np.array([])
        if len(a_ts) == 0 or len(c_ts) == 0:
            return "empty"
        head = [(t, v) for t, v in B.read_series(con, B.HEAD_TOPIC) if v and len(v) >= 8]
        h_ts = np.array([t for t, _ in head])
        vad = B.read_series(con, B.VAD_TOPIC)
        vts = [t / 1e9 for t, _ in vad]; vval = [bool(v) for _, v in vad]

        t0 = int(a_ts[0] + start_s * 1e9)
        cap = min(a_ts[-1], c_ts[-1])
        t_end = cap if duration_s is None else int(min(cap, t0 + duration_s * 1e9))
        ticks = np.arange(t0, t_end, int(tick * 1e9))     # 4 Hz SM refresh grid
        cwin = c_ts[(c_ts >= t0) & (c_ts <= t_end)]        # room1 frames in the window
        if len(ticks) == 0 or len(cwin) == 0:
            return "empty"
        # Frames are placed on a UNIFORM real-time grid (see loop below), so video-time
        # == real-time == audio-time exactly regardless of camera jitter or dropouts.
        # The grid rate only sets smoothness (sync comes from the time mapping, not the
        # fps), so use the camera's HEALTHY cadence = 1/median(interval); dropped-frame
        # gaps then HOLD the last frame instead of being collapsed/sped up. (The earlier
        # batch used this median as a per-frame CFR rate, which overstated the true mean
        # rate and desynced every clip; here it is only the resample grid, which is fine.)
        out_fps = fps if fps else (1e9 / float(np.median(np.diff(cwin))) if len(cwin) > 1 else 30.0)
        span = (cwin[-1] - cwin[0]) / 1e9
        mean_fps = (len(cwin) - 1) / span if span > 0 else out_fps
        print(f"    {len(cwin)} cam frames, mean {mean_fps:.2f} Hz -> grid @ {out_fps:.2f} fps "
              f"over {span:.0f}s ({len(ticks)} SM ticks @ {1/tick:.0f} Hz)"
              + ("  [camera dropouts -> hold]" if mean_fps < 0.97 * out_fps else ""), flush=True)

        # room1 VAD strip (silero, chosen gate) — RMS envelope + speaking band per tick
        lo = int(np.searchsorted(a_ts, t0 - int(1e9)))
        hi = int(np.searchsorted(a_ts, t_end, side="right"))
        mono_s = np.concatenate([np.frombuffer(b, np.int16).reshape(-1, CHANNELS)
                                 for b in a_d[lo:hi]]).astype(np.float32).mean(axis=1)
        rms_db = VO.per_tick_rms(mono_s, FS, int(a_ts[lo]), ticks, 0.46)
        spk = R1.speaking_at_ticks(mono_s, FS, int(a_ts[lo]), ticks)
        strip, geom = VO.build_strip(rms_db, spk, PPF, PANEL_H, VO.LABEL_BARS)
        print(f"    room1 speaking: {100 * spk.mean():.0f}% of ticks", flush=True)

        # stream frames straight to disk (full bags are too big to hold in RAM)
        tmp_v = os.path.join(out_dir, f"_tmp_{name}.mp4")
        vw = cv2.VideoWriter(tmp_v, cv2.VideoWriter_fourcc(*"mp4v"), out_fps,
                             (plot_size, plot_size + PANEL_H))

        # SM regenerates only on the 4 Hz tick grid; each output frame reuses the latest
        # tick's overlay. cache = (sm_color, label, metrics, hb, marks), keyed to grid time.
        cache = None
        cur_ti = -1
        n_written = 0
        grid_dt = max(1, int(round(1e9 / out_fps)))

        def emit(g, img):
            """Write one output frame for real-time instant g (ns) using camera image img."""
            nonlocal cache, cur_ti, n_written
            gi = int(np.searchsorted(ticks, g, side="right")) - 1
            if gi < 0 or img is None:
                return
            if gi != cur_ti:                              # crossed into a new SM tick: refresh
                tt = int(ticks[gi])
                ja = int(np.searchsorted(a_ts, tt, side="right"))
                if ja >= 160:
                    win = a_d[ja - 160:ja]                # GT view: window ends at tick tt
                    jh = int(np.searchsorted(h_ts, tt, side="right")) - 1
                    hb = ([list(head[jh][1][0:4]), list(head[jh][1][4:8])] if jh >= 0
                          else [[-99] * 4, [-99] * 4])
                    va_t = vad_active_at(vts, vval, tt / 1e9)
                    sm = api.generate(win)
                    sm_masked = sm if va_t else mask_speaking_box(sm)
                    sm_color = sm_to_color(transform_sm(sm_masked), plot_size=1080)
                    label, metrics, marks = label_current_sm(sm, hb, va_t, speaking_box=speaking_box)
                    VO.paint_label(strip, geom, gi, label, PPF)   # 4-label detection bars
                    cache = (sm_color, label, metrics, hb, marks)
                    cur_ti = gi
            if cache is None:
                return                                     # pre-roll: no SM tick yet
            sm_color, label, metrics, hb, marks = cache
            cam_resized = cv2.resize(img, (1080, 1080))
            blend = cv2.addWeighted(sm_color, 0.6, cam_resized, 0.8, 0)
            plot_annotations(blend, label, metrics, hb, speaking_box=speaking_box, marker_points=marks)
            cv2.putText(blend, f"room1 VAD {'SPEAK' if spk[gi] else 'silent'}  t={(g-t0)/1e9:6.2f}s",
                        (10, 1050), cv2.FONT_HERSHEY_SIMPLEX, 1.4,
                        (0, 255, 0) if spk[gi] else (0, 0, 255), 3)
            scene = cv2.resize(blend, (plot_size, plot_size))
            vw.write(np.vstack([scene, VO.crop_panel(strip, geom, gi, PPF, plot_size)]))
            n_written += 1

        # Stream camera frames; between consecutive frames fill the grid by HOLDING the
        # last decoded frame, so a dropped-frame gap freezes the picture (audio + SM keep
        # advancing) instead of collapsing the gap and drifting the two apart.
        g = int(cwin[0])                                   # first grid instant = first frame = audio start
        g_end = int(cwin[-1])
        cur_img = None
        processed = 0
        rows = con.execute(
            "SELECT timestamp, data FROM messages WHERE topic_id=? AND timestamp BETWEEN ? AND ? "
            "ORDER BY timestamp", (cam_tid, int(t0), int(t_end)))
        for cts, cdata in rows:
            while g < cts and g <= g_end:                  # grid instants before this frame: hold prev
                emit(g, cur_img); g += grid_dt
            img = B.decode_compressed_image(cdata)
            if img is not None:
                cur_img = img
            processed += 1
            if processed % 500 == 0:
                print(f"      cam {processed}/{len(cwin)} (emitted {n_written})", flush=True)
        while g <= g_end:                                  # tail: hold the last frame to the end
            emit(g, cur_img); g += grid_dt
        vw.release()
        if n_written == 0:
            if os.path.exists(tmp_v):
                os.remove(tmp_v)
            return "empty"

        # audio over the SAME real span the video frames cover ([first, last] camera
        # timestamp), downmix 16ch -> mono, +30 dB. Anchoring to the camera span (not
        # t0/t_end) makes the audio start on the first shown frame, so start and end align.
        seg = [a_d[k] for k in range(len(a_ts)) if cwin[0] <= a_ts[k] <= cwin[-1]]
        audio_np = np.concatenate([np.frombuffer(b, np.int16).reshape(-1, CHANNELS) for b in seg])
        mono = audio_np.astype(np.float32).mean(axis=1)
        mono *= 10 ** (30 / 20)
        mono = np.clip(mono, -32768, 32767).astype(np.int16)
        tmp_a = os.path.join(out_dir, f"_tmp_{name}.wav")
        wavfile.write(tmp_a, FS, mono)

        r = subprocess.run(["ffmpeg", "-y", "-i", tmp_v, "-i", tmp_a, "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", out_mp4],
                           capture_output=True)
        if r.returncode == 0:
            os.remove(tmp_v); os.remove(tmp_a)
            return "ok"
        print("    ffmpeg failed; kept", tmp_v, tmp_a)
        print(r.stderr.decode()[-500:])
        raise RuntimeError("ffmpeg mux failed")
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description="Batch sound-map QC videos over all robot bags.")
    ap.add_argument("--root", default=BAG_ROOT, help="ROSbag root directory")
    ap.add_argument("--out", default=OUT, help="output directory for the QC mp4s")
    ap.add_argument("--modes", nargs="+", default=list(ROBOT_MODES),
                    help=f"bag modes to include (default: {' '.join(ROBOT_MODES)})")
    ap.add_argument("--start", type=float, default=START_S, help="start-skip seconds")
    ap.add_argument("--duration", type=float, default=DURATION_S,
                    help="clip length seconds (omit = full bag)")
    ap.add_argument("--tick", type=float, default=TICK,
                    help="SM refresh period seconds (4 Hz grid; not the video fps)")
    ap.add_argument("--fps", type=float, default=None,
                    help="output video fps (default: camera's native rate ~29.5)")
    ap.add_argument("--plot-size", type=int, default=PLOT_SIZE, help="output frame size")
    ap.add_argument("--skip-existing", action="store_true",
                    help="skip bags that already have output (default: overwrite / re-render all)")
    ap.add_argument("--limit", type=int, default=None, help="process only the first N bags (sampling)")
    args = ap.parse_args()

    if not os.path.isdir(args.root):
        sys.exit(f"ROSbag root not found (SSD mounted?): {args.root}")
    os.makedirs(args.out, exist_ok=True)

    bags = discover_bags(args.root, args.modes)
    if not bags:
        sys.exit(f"no bags matching modes {args.modes} under {args.root}")
    if args.limit:
        bags = bags[:args.limit]
    print(f"{len(bags)} bags to process ({', '.join(args.modes)})")

    api = SoundMapAPI()                # build once, reuse across bags
    speaking_box = get_speaking_box()
    counts = {"ok": 0, "skip": 0, "empty": 0, "fail": 0}
    t_start = time.time()
    for n, bag in enumerate(bags, 1):
        name = os.path.basename(bag)
        print(f"[{n}/{len(bags)}] {name}", flush=True)
        try:
            status = process_bag(bag, api, speaking_box, args.out,
                                 args.start, args.duration, args.tick, args.plot_size,
                                 overwrite=not args.skip_existing, fps=args.fps)
        except Exception:                # one bad bag must not kill the batch
            counts["fail"] += 1
            traceback.print_exc()
            continue
        counts[status] += 1
        print(f"    -> {status}", flush=True)

    dt = time.time() - t_start
    print(f"\ndone in {dt/60:.1f} min | "
          f"ok={counts['ok']} skip={counts['skip']} empty={counts['empty']} fail={counts['fail']}")
    print(f"videos in {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
