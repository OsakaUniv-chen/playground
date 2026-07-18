"""Extracts chat's 3 bags at NATIVE camera rate (~30Hz, confirmed empirically:
all 3 bags measure ~29.95fps) instead of the usual 2Hz TICK, to replicate
the old model's (exp4) actual training-data density for sixth-run's ablation
#1 (sparse vs dense sliding windows -- see CONTEXT.md's fifth-run section
for why this mechanism, not "30Hz vs 2Hz" per se, is the real candidate
explanation: access-model-train/create_dataset.py computes a fresh soundmap
at EVERY camera frame via sm_generator.generate(audio_queue), one call per
image_topic message, not gated by any tick interval).

One soundmap per camera frame, same AUDIO_WIN=160 (~0.46s) trailing audio
window as the normal 2Hz pipeline (preprocessing/build_dataset.py) -- the
per-window audio content construction is IDENTICAL, only the tick spacing
differs. Output: sixth-run/data-dense/{bag}_dense.npz with soundmap/
gray_camimg/tick_ts, same schema as the normal train-data/ npz (just ~15x
more frames). No QC video -- this is training data only.

~5.4ms/generate() call measured on this GPU; ~32700 camera frames per chat
bag -> ~3 minutes/bag, ~9 minutes for all 3.
"""
from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

import cv2
import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "preprocessing"))

import bag_io as B
from soundmap import SoundMapGenerator

PSSPDATA_ROOT = Path("/media/chen/Extreme SSD/PSSPData")
OUT_DIR = _HERE / "data-dense"
BAGS = ["debate_exp1_topic1", "debate_exp1_topic2", "debate_exp1_topic3"]

AUDIO_WIN = 160
SM_SIZE = 64
FS = 44100
CHANNELS = 16
BLOCKSIZE = 4096


def extract_dense(bag_dir: Path, generator: SoundMapGenerator) -> dict:
    con = B.open_bag(bag_dir)
    audio = B.read_series(con, B.AUDIO_TOPIC, decode=B.audio_decoder_for(con))
    cam = B.read_series(con, B.CAMERA_TOPIC, decode=lambda d: d)
    con.close()

    a_ts = np.asarray([t for t, _ in audio], dtype=np.int64)
    a_d = [d for _, d in audio]
    c_ts = np.asarray([t for t, _ in cam], dtype=np.int64)
    c_d = [d for _, d in cam]

    def audio_window(end_ns):
        j = int(np.searchsorted(a_ts, end_ns, side="right"))
        return a_d[j - AUDIO_WIN:j] if j >= AUDIO_WIN else None

    sm_list, gray_list, ts_list = [], [], []
    for i, t in enumerate(c_ts):
        w = audio_window(int(t))
        if w is None:
            continue
        frame = B.decode_compressed_image(c_d[i])
        if frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (SM_SIZE, SM_SIZE), interpolation=cv2.INTER_AREA)
        sm = generator.generate(w)

        sm_list.append(sm.astype(np.float32))
        gray_list.append(gray)
        ts_list.append(int(t))

    return {
        "soundmap": np.stack(sm_list),
        "gray_camimg": np.stack(gray_list),
        "tick_ts": np.asarray(ts_list, dtype=np.int64),
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.simplefilter("once")
        generator = SoundMapGenerator(fs=FS, channels=CHANNELS, blocksize=BLOCKSIZE,
                                       sm_size=SM_SIZE, device="cuda")
    print(f"generator ready on {generator.device}")

    for name in BAGS:
        out_path = OUT_DIR / f"{name}_dense.npz"
        if out_path.exists():
            print(f"skip {name} (already exists)")
            continue
        bag_dir = PSSPDATA_ROOT / "chat" / name
        t0 = time.time()
        data = extract_dense(bag_dir, generator)
        np.savez_compressed(out_path, **data)
        n = data["soundmap"].shape[0]
        dt = time.time() - t0
        print(f"{name}: {n} dense frames in {dt:.1f}s -> {out_path}")


if __name__ == "__main__":
    main()
