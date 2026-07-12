"""Per-bag tick extraction — GT/DoA/PSSP/Tele/Random @ 4Hz.

Replays each robot-condition bag on a 4 Hz output-time grid and writes one
tick table per bag (results/ticks/{bag}.parquet). Policy definitions and the
differences from analysis2/code/extract.py (no Tele staleness gate, no
--save-sm) are documented in ../report2/report.md.

Each mode is computed with its own delay window:
  GT(t)   : sound-map label of audio window ending at t          (delay 0, truth)
  DoA(t)  : sound-map label of audio window ending at t-0.2s     (0.2s SM gen)
  PSSP(t) : SimVP prediction (4 horizons) from the DoA clip      (0.2s + inference)
  Tele(t) : room2 head-yaw side, latest face <= t
  motors  : recorded /boxie/boxie_motors yaw sign (ffill)        (executed / RAND)
Head boxes are RE-DETECTED with MediaPipe (not read from the bag). First 10 s of
each bag are discarded (buffer fill), so no per-tick validity flag is needed.

Usage:
    python extract.py --bags all --device auto --workers 8
    python extract.py --bags G11_game6_PSSP            # single bag (smoke)
Resumable: bags already in --out are skipped unless --force.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

_HERE = os.path.dirname(os.path.abspath(__file__))
_UTILS_DIR = os.path.join(_HERE, "..", "utils")
_PSSP_DIR = os.path.join(_UTILS_DIR, "pssp")
for _p in (_UTILS_DIR, _PSSP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bag_io as B

# --- constants (mirror the live pipeline; see ../report2/report.md) --------
TICK = 0.25            # 4 Hz output grid
AUDIO_WIN = 160        # msgs per sound map (~0.46 s)
GEN_DELAY = 0.2        # DoA/PSSP sound-map generation delay (s)
SKIP_S = 10.0          # discard first 10 s (buffer fill)
CLIP_MAXLEN = 19       # tick-cadence clip deque; [::2] -> 10 frames @2 Hz
RAND_SWITCH = 0.065    # Random policy per-tick flip probability
HORIZONS = ("p05", "p10", "p15", "p20")  # SimVP +0.5/1.0/1.5/2.0 s
DEFAULT_ROOT = Path(B.resolve_bag_root())
MODES = ("Tele", "PSSP", "DoA", "Random")


def _latest_idx(ts_arr, t):
    """Index of the latest element with ts <= t (or -1)."""
    return int(np.searchsorted(ts_arr, t, side="right")) - 1


def preprocess_room1(con, frame_stride):
    """Chronological MediaPipe head-box re-detection + cached gray64 for the clip.
    Returns (ts[np], boxes[list], carried[list], gray64[list])."""
    from head_box import HeadBoxAPI
    from labeling import frame_to_gray64
    api = HeadBoxAPI()
    try:
        tid = B.topic_id(con, B.CAMERA_TOPIC)
        ts, boxes, carried, grays = [], [], [], []
        i = 0
        for rec_ts, data in con.execute(
            "SELECT timestamp, data FROM messages WHERE topic_id=? ORDER BY timestamp", (tid,)):
            if i % frame_stride == 0:
                frame = B.decode_compressed_image(data)
                if frame is not None:
                    bx, c = api.detect_with_flag(frame)
                    ts.append(rec_ts); boxes.append(bx); carried.append(c)
                    grays.append(frame_to_gray64(frame))
            i += 1
        return np.asarray(ts), boxes, carried, grays
    finally:
        # MediaPipe's GPU/EGL delegate isn't released on Python GC alone; a new
        # HeadBoxAPI (and its FaceDetection) is created per bag, so without an
        # explicit close() the GPU context leaks across bags within the same
        # long-lived worker process until it OOMs a few bags in.
        api.detector.face_detection.close()


def preprocess_room2(con, frame_stride):
    """Chronological MediaPipe FaceMesh head-yaw. Returns (ts[np], yaw[list|None])."""
    from head_orientation import HeadOrientationAPI
    api = HeadOrientationAPI()
    try:
        tid = B.topic_id(con, B.ROOM2_CAMERA_TOPIC)
        if tid is None:
            return np.asarray([]), []
        ts, yaw = [], []
        i = 0
        for rec_ts, data in con.execute(
            "SELECT timestamp, data FROM messages WHERE topic_id=? ORDER BY timestamp", (tid,)):
            if i % frame_stride == 0:
                frame = B.decode_compressed_image(data)
                if frame is not None:
                    res = api.detect(frame)
                    ts.append(rec_ts); yaw.append(None if res is None else res[1])
            i += 1
        return np.asarray(ts), yaw
    finally:
        # see preprocess_room1: FaceMesh's GPU/EGL delegate must be closed
        # explicitly or it leaks across bags within the same worker process.
        api.face_mesh.close()


def process_bag(bag_dir, out_dir, device, frame_stride):
    from soundmap_api import SoundMapAPI
    from pssp_api import PsspAPI
    from labeling import (label_current_sm, label_prediction_sm,
                          build_clip_frame_from_gray, vad_active_at)

    m = B.DIR_RE.match(bag_dir.name)
    con = B.open_bag(bag_dir)

    audio = B.read_series(con, B.AUDIO_TOPIC)
    a_ts = np.asarray([t for t, _ in audio]); a_d = [d for _, d in audio]
    vad = B.read_series(con, B.VAD_TOPIC)
    v_ts = [t / 1e9 for t, _ in vad]; v_val = [bool(v) for _, v in vad]
    motors = [(t, y) for t, y in B.read_series(con, B.MOTORS_TOPIC) if y is not None]
    m_ts = np.asarray([t for t, _ in motors]); m_yaw = [y for _, y in motors]

    h_ts, h_box, h_carried, h_gray = preprocess_room1(con, frame_stride)
    tel_ts, tel_yaw = preprocess_room2(con, frame_stride)

    cam_tid = B.topic_id(con, B.CAMERA_TOPIC)
    c_max = con.execute("SELECT max(timestamp) FROM messages WHERE topic_id=?", (cam_tid,)).fetchone()[0]
    con.close()

    sm_api = SoundMapAPI()
    pssp = PsspAPI(device=device)

    t0 = int(a_ts[0] + SKIP_S * 1e9)
    step = int(TICK * 1e9)
    # pre-fill the 19-frame PSSP clip during the last 18 ticks (4.5 s) of the skip
    # window, so PSSP is valid from the very first recorded tick (no None gap).
    # The clip's AUDIO content spans ~5 s (oldest window starts t-5.16, newest
    # ends t-0.2); describe it as "10 frames @2Hz, latest ~5 s", not 4.75 s.
    t_warm = t0 - (CLIP_MAXLEN - 1) * step
    t_end = int(min(a_ts[-1], c_max))
    ticks = range(t_warm, t_end + 1, step)

    def window(end_ns):
        j = int(np.searchsorted(a_ts, end_ns, side="right"))
        return a_d[j - AUDIO_WIN:j] if j >= AUDIO_WIN else None

    cols = {k: [] for k in (
        "tick_ts", "tick_idx",
        "gt_label", "gt_L", "gt_R", "gt_T", "gt_O",
        "doa_label", "doa_L", "doa_R", "doa_T", "doa_O",
        "pssp_p05", "pssp_p10", "pssp_p15", "pssp_p20",
        "tel_side", "rand_side", "vad_active", "motors_side", "hb_carried",
        "hb_lx", "hb_ly", "hb_lw", "hb_lh", "hb_rx", "hb_ry", "hb_rw", "hb_rh")}
    clip = []  # rolling clip frames (2,64,64), one per tick
    n_pssp = 0
    k_rec = 0
    # reproducible Random-policy baseline (observation-independent random walk,
    # 0.065 flip/tick; seed derived from bag name for determinism)
    rng = np.random.default_rng(zlib.crc32(bag_dir.name.encode()))
    rand_state = "left" if rng.random() < 0.5 else "right"

    def region(metrics):
        return [metrics.get(k) for k in ("Left", "Right", "Teleoperator", "Others")]

    for t in ticks:
        s_doa = t - int(GEN_DELAY * 1e9)
        w_doa = window(s_doa)
        if w_doa is None:
            continue

        # DoA sound map (needed every tick to feed the clip; reused for doa_label)
        vad_doa = vad_active_at(v_ts, v_val, s_doa / 1e9)
        sm_doa = sm_api.generate(w_doa)
        gi = _latest_idx(h_ts, s_doa)
        gray = h_gray[gi] if gi >= 0 else np.zeros((64, 64), np.float64)
        clip.append(build_clip_frame_from_gray(sm_doa, vad_doa, gray))
        if len(clip) > CLIP_MAXLEN:
            clip.pop(0)

        if t < t0:
            continue  # warmup ticks: only fill the clip, do not record

        w_gt = window(t)
        if w_gt is None:
            continue

        # head box (latest re-detection <= t) reused for all labels
        hi = _latest_idx(h_ts, t)
        hb = h_box[hi] if hi >= 0 else [[-99] * 4, [-99] * 4]
        carried = bool(h_carried[hi]) if hi >= 0 else False

        vad_t = vad_active_at(v_ts, v_val, t / 1e9)
        sm_gt = sm_api.generate(w_gt)
        gt_lab, gt_m, _ = label_current_sm(sm_gt, hb, vad_t)
        doa_lab, doa_m, _ = label_current_sm(sm_doa, hb, vad_doa)

        pssp_labels = [None, None, None, None]
        if len(clip) == CLIP_MAXLEN:
            preds = pssp.predict(np.asarray(clip[::2], dtype=np.float32))  # (4,64,64)
            pssp_labels = [label_prediction_sm(preds[i], hb)[0] for i in range(4)]
            n_pssp += 1

        # Random-policy baseline: flip with prob 0.065 each recorded tick
        if rng.random() < RAND_SWITCH:
            rand_state = "right" if rand_state == "left" else "left"

        # Tele side: latest face <= t, no staleness limit (see report2/report.md).
        tel_side = None
        if len(tel_ts):
            ti = _latest_idx(tel_ts, t)
            if ti >= 0 and tel_yaw[ti] is not None:
                tel_side = "left" if tel_yaw[ti] > 0 else "right"

        # motors executed side (ffill, no staleness limit)
        motors_side = None
        if len(m_ts):
            mi = _latest_idx(m_ts, t)
            if mi >= 0:
                y = m_yaw[mi]
                motors_side = "left" if y > 0 else ("right" if y < 0 else None)

        cols["tick_ts"].append(int(t)); cols["tick_idx"].append(k_rec)
        cols["gt_label"].append(gt_lab)
        for name, v in zip(("gt_L", "gt_R", "gt_T", "gt_O"), region(gt_m)):
            cols[name].append(v)
        cols["doa_label"].append(doa_lab)
        for name, v in zip(("doa_L", "doa_R", "doa_T", "doa_O"), region(doa_m)):
            cols[name].append(v)
        for name, v in zip(("pssp_p05", "pssp_p10", "pssp_p15", "pssp_p20"), pssp_labels):
            cols[name].append(v)
        cols["tel_side"].append(tel_side)
        cols["rand_side"].append(rand_state)
        cols["vad_active"].append(bool(vad_t))
        cols["motors_side"].append(motors_side)
        cols["hb_carried"].append(carried)
        for name, v in zip(("hb_lx", "hb_ly", "hb_lw", "hb_lh"), hb[0]):
            cols[name].append(int(v))
        for name, v in zip(("hb_rx", "hb_ry", "hb_rw", "hb_rh"), hb[1]):
            cols[name].append(int(v))
        k_rec += 1

    out_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(cols), str(out_dir / f"{bag_dir.name}.parquet"))

    # A fresh PsspAPI (SimVP model) is created per bag; release its GPU memory
    # before the worker moves on to the next bag (same leak-across-bags reason
    # as the MediaPipe .close() calls above).
    torch_mod = pssp.torch
    del pssp, sm_api
    if torch_mod.cuda.is_available():
        torch_mod.cuda.empty_cache()
    return {"bag": bag_dir.name, "group": int(m["group"]), "game": int(m["game"]),
            "mode": m["mode"], "dur_s": round((t_end - t0) / 1e9, 1),
            "n_ticks": len(cols["tick_ts"]), "n_pssp": n_pssp}


def _worker(args_tuple):
    bag_str, out_str, device, stride = args_tuple
    t0 = time.time()
    try:
        meta = process_bag(Path(bag_str), Path(out_str), device, stride)
        meta["sec"] = round(time.time() - t0, 0)
        print(f"  done {meta['bag']}: {meta['n_ticks']} ticks, {meta['n_pssp']} pssp "
              f"in {meta['sec']:.0f}s", flush=True)
        return meta
    except Exception as e:  # noqa: BLE001
        print(f"  FAILED {Path(bag_str).name}: {e}", flush=True)
        return {"bag": Path(bag_str).name, "error": str(e)}


def discover(root):
    out = []
    for d in sorted(Path(root).iterdir()):
        mm = B.DIR_RE.match(d.name)
        if d.is_dir() and mm and mm["mode"] in MODES:
            out.append(d)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rosbag-root", default=str(DEFAULT_ROOT))
    ap.add_argument("--bags", default="all", help="'all' or comma-separated bag names")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "results" / "ticks"))
    ap.add_argument("--device", default="auto", help="auto|cuda|mps|cpu (SimVP)")
    ap.add_argument("--workers", type=int, default=1, help="parallel bag workers")
    ap.add_argument("--frame-stride", type=int, default=1,
                    help="process every Nth camera frame for MediaPipe (1 = all 30 Hz)")
    ap.add_argument("--force", action="store_true", help="re-extract existing bags")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    root = Path(args.rosbag_root)
    if not root.exists():
        raise SystemExit(f"ROSbag root not found: {root}")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    bags = discover(root) if args.bags == "all" else \
        [root / n.strip() for n in args.bags.split(",")]
    todo = [b for b in bags if args.force or not (out_dir / f"{b.name}.parquet").exists()]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} bag(s) to extract (device={args.device}, workers={args.workers}, "
          f"frame_stride={args.frame_stride}); {len(bags) - len(todo)} skipped/done")
    if not todo:
        return

    jobs = [(str(b), str(out_dir), args.device, args.frame_stride) for b in todo]
    metas = []
    if args.workers > 1:
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx) as ex:
            for meta in ex.map(_worker, jobs):
                metas.append(meta)
    else:
        for j in jobs:
            metas.append(_worker(j))

    # append/update index.csv
    import csv
    idx_path = out_dir / "index.csv"
    existing = {}
    if idx_path.exists():
        with open(idx_path) as f:
            for row in csv.DictReader(f):
                existing[row["bag"]] = row
    for m in metas:
        if "error" not in m:
            existing[m["bag"]] = m
    fields = ["bag", "group", "game", "mode", "dur_s", "n_ticks", "n_pssp", "sec"]
    with open(idx_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in sorted(existing.values(), key=lambda r: r["bag"]):
            w.writerow(row)
    ok = [m for m in metas if "error" not in m]
    print(f"extracted {len(ok)}/{len(todo)} bags -> {out_dir}; index.csv updated")


if __name__ == "__main__":
    main()
