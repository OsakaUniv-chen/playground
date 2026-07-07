"""Compare the OLD vs NEW sound-map generator on every experiment bag.

Both generators are fed the IDENTICAL audio window per 4 Hz tick and their (64,64)
[0,160] maps are pushed through the IDENTICAL labeling path (mask -> transform ->
color -> extract7), so the only thing that differs is the beamforming algorithm:

  OLD  = live/acoular  : BeamformerBase.synthetic(f=2000, num=3)   (soundmap_api)
  NEW  = offline/torch : direct FFT-power sum over 2000-8000 Hz    (new_soundmap_api)

Everything is recreated from the RAW audio + RAW room1 camera:
  - sound maps        : from /audio/audio_raw (160-msg window ending at each tick)
  - head boxes (L/R)  : RE-DETECTED with MediaPipe from /camera/image_raw/compressed
                        (HeadBoxAPI, head_node logic) -- the recorded /head/head_box
                        is NOT used (Video bags don't even have it).
  - VAD gate          : from /room2_audio/vad
The head box + VAD are shared between the two generators, so they cancel out and
the label disagreement is attributable to the generator alone.

Time axis = bag record timestamp (ns). First 10 s of each bag are discarded
(audio-window fill). Interview bags are excluded (they don't match the game
pattern). One parquet per bag under --out; resumable (skips done bags).

Usage:
    python compare_generators.py --bags G1_game5_DoA          # single bag (smoke)
    python compare_generators.py --bags all --workers 8       # full run
Resumable: bags already in --out are skipped unless --force.
"""
from __future__ import annotations

# Thread caps BEFORE numpy/torch import (acoular+numba+8 workers would oversubscribe).
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import argparse
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

_HERE = os.path.dirname(os.path.abspath(__file__))
_CODE = os.path.abspath(os.path.join(_HERE, os.pardir, "code"))
for _p in (_CODE, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bag_io as B

# --- constants (mirror the live pipeline / SPEC §5) ------------------------
TICK = 0.25            # 4 Hz output grid
AUDIO_WIN = 160        # msgs per sound map (~0.46 s)
SKIP_S = 10.0          # discard first 10 s (audio-window fill)
VAD_WIN = 0.25
DEFAULT_ROOT = Path(B.resolve_bag_root())    # /media/chen/... (Linux) else /Volumes/... (Mac)
# 5 experiment conditions; interviews don't match DIR_RE so are excluded anyway.
MODES = ("DoA", "PSSP", "Random", "Tele", "Video")


def _latest_idx(ts_arr, t):
    """Index of the latest element with ts <= t (or -1)."""
    return int(np.searchsorted(ts_arr, t, side="right")) - 1


def redetect_headboxes(con, frame_stride):
    """Chronological MediaPipe head-box re-detection from RAW room1 camera.
    Returns (ts[np int64], boxes[list[[left,right]]], carried[list[bool]])."""
    from head_box import HeadBoxAPI
    api = HeadBoxAPI()
    tid = B.topic_id(con, B.CAMERA_TOPIC)
    if tid is None:
        return np.asarray([], dtype=np.int64), [], []
    ts, boxes, carried = [], [], []
    i = 0
    for rec_ts, data in con.execute(
            "SELECT timestamp, data FROM messages WHERE topic_id=? ORDER BY timestamp", (tid,)):
        if i % frame_stride == 0:
            frame = B.decode_compressed_image(data)
            if frame is not None:
                bx, c = api.detect_with_flag(frame)
                ts.append(rec_ts); boxes.append(bx); carried.append(c)
        i += 1
    return np.asarray(ts, dtype=np.int64), boxes, carried


def _box_valid(box):
    return box is not None and not all(int(c) == -99 for c in box)


def _pearson(a, b):
    a = a.ravel().astype(np.float64); b = b.ravel().astype(np.float64)
    sa, sb = a.std(), b.std()
    if sa == 0.0 or sb == 0.0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _peak_dist(a, b):
    ra, ca = np.unravel_index(int(np.argmax(a)), a.shape)
    rb, cb = np.unravel_index(int(np.argmax(b)), b.shape)
    return float(np.hypot(ra - rb, ca - cb))


def process_bag(bag_dir, out_dir, device, frame_stride, save_sm):
    from soundmap_api import SoundMapAPI
    from new_soundmap_api import NewSoundMapAPI
    from labeling import label_current_sm, vad_active_at

    m = B.DIR_RE.match(bag_dir.name)
    con = B.open_bag(bag_dir)

    audio = B.read_series(con, B.AUDIO_TOPIC)
    if len(audio) < AUDIO_WIN + 1:
        con.close()
        raise RuntimeError(f"too few audio msgs ({len(audio)})")
    a_ts = np.asarray([t for t, _ in audio], dtype=np.int64)
    a_d = [d for _, d in audio]
    vad = B.read_series(con, B.VAD_TOPIC)
    v_ts = [t / 1e9 for t, _ in vad]; v_val = [bool(v) for _, v in vad]

    h_ts, h_box, h_carried = redetect_headboxes(con, frame_stride)

    cam_tid = B.topic_id(con, B.CAMERA_TOPIC)
    c_max = con.execute("SELECT max(timestamp) FROM messages WHERE topic_id=?",
                        (cam_tid,)).fetchone()[0] if cam_tid is not None else a_ts[-1]
    con.close()

    old_api = SoundMapAPI()
    new_api = NewSoundMapAPI(device=device)

    t0 = int(a_ts[0] + SKIP_S * 1e9)
    t_end = int(min(a_ts[-1], c_max))
    ticks = range(t0, t_end + 1, int(TICK * 1e9))

    def window(end_ns):
        j = int(np.searchsorted(a_ts, end_ns, side="right"))
        return a_d[j - AUDIO_WIN:j] if j >= AUDIO_WIN else None

    cols = {k: [] for k in (
        "tick_ts", "tick_idx", "old_label", "new_label", "agree",
        "old_L", "old_R", "old_T", "old_O", "new_L", "new_R", "new_T", "new_O",
        "sm_pearson", "sm_peak_dist", "old_peak", "new_peak",
        "old_max", "new_max", "old_mean", "new_mean",
        "vad_active", "hb_l_valid", "hb_r_valid", "hb_carried")}
    old_stack, new_stack = [], []
    t_old = t_new = 0.0
    n_used = 0

    def region(metrics):
        return [metrics.get(k) for k in ("Left", "Right", "Teleoperator", "Others")]

    for k, t in enumerate(ticks):
        w = window(t)
        if w is None:
            continue

        hi = _latest_idx(h_ts, t) if len(h_ts) else -1
        hb = h_box[hi] if hi >= 0 else [[-99] * 4, [-99] * 4]
        carried = bool(h_carried[hi]) if hi >= 0 else False
        vad_t = vad_active_at(v_ts, v_val, t / 1e9) if v_ts else False

        ta = time.perf_counter(); sm_o = old_api.generate(w); t_old += time.perf_counter() - ta
        tb = time.perf_counter(); sm_n = new_api.generate(w); t_new += time.perf_counter() - tb

        old_lab, old_m, _ = label_current_sm(sm_o, hb, vad_t)
        new_lab, new_m, _ = label_current_sm(sm_n, hb, vad_t)

        cols["tick_ts"].append(int(t)); cols["tick_idx"].append(k)
        cols["old_label"].append(old_lab); cols["new_label"].append(new_lab)
        cols["agree"].append(old_lab == new_lab)
        for name, v in zip(("old_L", "old_R", "old_T", "old_O"), region(old_m)):
            cols[name].append(None if v is None else float(v))
        for name, v in zip(("new_L", "new_R", "new_T", "new_O"), region(new_m)):
            cols[name].append(None if v is None else float(v))
        cols["sm_pearson"].append(_pearson(sm_o, sm_n))
        cols["sm_peak_dist"].append(_peak_dist(sm_o, sm_n))
        cols["old_peak"].append(float(sm_o.max())); cols["new_peak"].append(float(sm_n.max()))
        cols["old_max"].append(float(sm_o.max())); cols["new_max"].append(float(sm_n.max()))
        cols["old_mean"].append(float(sm_o.mean())); cols["new_mean"].append(float(sm_n.mean()))
        cols["vad_active"].append(bool(vad_t))
        cols["hb_l_valid"].append(_box_valid(hb[0])); cols["hb_r_valid"].append(_box_valid(hb[1]))
        cols["hb_carried"].append(carried)
        if save_sm:
            old_stack.append(sm_o.astype(np.float16)); new_stack.append(sm_n.astype(np.float16))
        n_used += 1

    out_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(cols), str(out_dir / f"{bag_dir.name}.parquet"))
    if save_sm:
        np.savez_compressed(out_dir / f"{bag_dir.name}_sm.npz",
                            old_sm=np.asarray(old_stack), new_sm=np.asarray(new_stack))
    return {"bag": bag_dir.name, "group": int(m["group"]), "game": int(m["game"]),
            "mode": m["mode"], "dur_s": round((t_end - t0) / 1e9, 1),
            "n_ticks": n_used, "n_heads": int(len(h_ts)),
            "old_ms": round(1000 * t_old / max(n_used, 1), 1),
            "new_ms": round(1000 * t_new / max(n_used, 1), 1),
            "new_device": new_api.device}


def _worker(args_tuple):
    bag_str, out_str, device, stride, save_sm = args_tuple
    t0 = time.time()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            meta = process_bag(Path(bag_str), Path(out_str), device, stride, save_sm)
        meta["sec"] = round(time.time() - t0, 0)
        print(f"  done {meta['bag']}: {meta['n_ticks']} ticks "
              f"(old {meta['old_ms']}ms/new {meta['new_ms']}ms per map) in {meta['sec']:.0f}s",
              flush=True)
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
    ap.add_argument("--out", default=str(Path(_HERE) / "results" / "ticks"))
    ap.add_argument("--device", default="cpu", help="new generator torch device (cpu|mps|cuda)")
    ap.add_argument("--workers", type=int, default=1, help="parallel bag workers")
    ap.add_argument("--frame-stride", type=int, default=2,
                    help="run MediaPipe on every Nth room1 frame (1=all 30Hz)")
    ap.add_argument("--force", action="store_true", help="re-extract existing bags")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--save-sm", action="store_true", help="also dump old/new SM stacks (large)")
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
    print(f"{len(todo)} bag(s) to compare (device={args.device}, workers={args.workers}, "
          f"frame_stride={args.frame_stride}); {len(bags) - len(todo)} skipped/done")
    if not todo:
        return

    jobs = [(str(b), str(out_dir), args.device, args.frame_stride, args.save_sm) for b in todo]
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
    for mm in metas:
        if "error" not in mm:
            existing[mm["bag"]] = mm
    fields = ["bag", "group", "game", "mode", "dur_s", "n_ticks", "n_heads",
              "old_ms", "new_ms", "new_device", "sec"]
    with open(idx_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in sorted(existing.values(),
                          key=lambda r: (int(r["group"]), int(r["game"]))):
            w.writerow(row)
    ok = [mm for mm in metas if "error" not in mm]
    print(f"compared {len(ok)}/{len(todo)} bags -> {out_dir}; index.csv updated")
    for mm in metas:
        if "error" in mm:
            print(f"  ERROR {mm['bag']}: {mm['error']}")


if __name__ == "__main__":
    main()
