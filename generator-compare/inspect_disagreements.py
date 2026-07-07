"""Recompute and visualize the ticks where OLD and NEW disagree on the 4-label.

The main run doesn't store maps, so for each disagreeing tick (found by scanning
results/ticks/*.parquet) this reopens the bag, rebuilds the identical inputs
(same 160-msg audio window, same MediaPipe head box <= t, same VAD), regenerates
BOTH sound maps, reproduces the two labels (asserts they match the parquet), and
writes a side-by-side figure: OLD annotated map | NEW annotated map | raw (NEW-OLD)
difference. Also dumps the raw 64x64 maps to results/disagreement_maps.npz.

Run:  python inspect_disagreements.py
"""
from __future__ import annotations

import os
import sys
import glob

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import pyarrow.parquet as pq

_HERE = os.path.dirname(os.path.abspath(__file__))
_CODE = os.path.abspath(os.path.join(_HERE, os.pardir, "code"))
for _p in (_CODE, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import bag_io as B
from labeling import (mask_speaking_box, transform_sm, sm_to_color,
                      run_extract_target, plot_annotations, vad_active_at,
                      get_speaking_box)

ROOT = __import__("pathlib").Path(B.resolve_bag_root())    # /media/chen/... (Linux) else /Volumes/... (Mac)
AUDIO_WIN = 160
FRAME_STRIDE = 2          # must match the run
OUT = os.path.join(_HERE, "results")


def find_disagreements():
    out = []
    for fp in sorted(glob.glob(os.path.join(OUT, "ticks", "*.parquet"))):
        d = pq.read_table(fp).to_pydict()
        bag = os.path.basename(fp)[:-8]
        for i in range(len(d["old_label"])):
            if d["old_label"][i] != d["new_label"][i]:
                out.append({"bag": bag, "tick_idx": d["tick_idx"][i],
                            "tick_ts": d["tick_ts"][i],
                            "old_label": d["old_label"][i], "new_label": d["new_label"][i],
                            "vad": d["vad_active"][i]})
    return out


def headbox_at(con, t_ns):
    """Replay MediaPipe (stride FRAME_STRIDE) up to t_ns, return latest processed box."""
    from head_box import HeadBoxAPI
    api = HeadBoxAPI()
    tid = B.topic_id(con, B.CAMERA_TOPIC)
    last = [[-99] * 4, [-99] * 4]
    i = 0
    for rec_ts, data in con.execute(
            "SELECT timestamp, data FROM messages WHERE topic_id=? ORDER BY timestamp", (tid,)):
        if rec_ts > t_ns:
            break
        if i % FRAME_STRIDE == 0:
            frame = B.decode_compressed_image(data)
            if frame is not None:
                last, _ = api.detect_with_flag(frame)
        i += 1
    return last


def label_with_color(sm, hb, vad):
    """Reproduce label_current_sm but also return the annotated color image + parts."""
    sm2 = sm if vad else mask_speaking_box(sm)
    color = sm_to_color(transform_sm(sm2), plot_size=1080)
    lab, metrics, points = run_extract_target(7, color, hb, speaking_box=get_speaking_box())
    return lab, metrics, points, color


def main():
    from soundmap_api import SoundMapAPI
    from new_soundmap_api import NewSoundMapAPI
    dis = find_disagreements()
    print(f"{len(dis)} disagreeing tick(s)")
    if not dis:
        return
    old_api = SoundMapAPI(); new_api = NewSoundMapAPI(device="cpu")

    n = len(dis)
    fig, axes = plt.subplots(n, 3, figsize=(13.5, 4.6 * n))
    if n == 1:
        axes = axes[None, :]
    saved = {}

    for r_i, d in enumerate(dis):
        bag_dir = ROOT / d["bag"]
        con = B.open_bag(bag_dir)
        audio = B.read_series(con, B.AUDIO_TOPIC)
        a_ts = np.asarray([t for t, _ in audio], dtype=np.int64); a_d = [x for _, x in audio]
        vadser = B.read_series(con, B.VAD_TOPIC)
        v_ts = [t / 1e9 for t, _ in vadser]; v_val = [bool(v) for _, v in vadser]

        t = d["tick_ts"]
        j = int(np.searchsorted(a_ts, t, side="right"))
        w = a_d[j - AUDIO_WIN:j]
        hb = headbox_at(con, t)
        con.close()
        vad = vad_active_at(v_ts, v_val, t / 1e9) if v_ts else False

        sm_o = old_api.generate(w); sm_n = new_api.generate(w)
        old_lab, old_m, old_pts, col_o = label_with_color(sm_o, hb, vad)
        new_lab, new_m, new_pts, col_n = label_with_color(sm_n, hb, vad)
        # sanity: recomputation must match the recorded parquet decision
        assert old_lab == d["old_label"] and new_lab == d["new_label"], \
            f"repro mismatch {d['bag']} {old_lab}/{new_lab} vs {d['old_label']}/{d['new_label']}"

        plot_annotations(col_o, old_lab, old_m, hb, speaking_box=get_speaking_box(),
                         marker_points=old_pts)
        plot_annotations(col_n, new_lab, new_m, hb, speaking_box=get_speaking_box(),
                         marker_points=new_pts)

        diff = sm_n.astype(np.float64) - sm_o.astype(np.float64)
        vmax = max(1e-6, float(np.abs(diff).max()))

        axes[r_i, 0].imshow(col_o[:, :, ::-1])  # BGR->RGB
        axes[r_i, 0].set_title(f"{d['bag']}  tick {d['tick_idx']}  (VAD={'on' if vad else 'off'})\n"
                               f"OLD (acoular) -> {old_lab}", fontsize=10)
        axes[r_i, 1].imshow(col_n[:, :, ::-1])
        axes[r_i, 1].set_title(f"NEW (torch) -> {new_lab}", fontsize=10)
        im = axes[r_i, 2].imshow(diff, cmap="RdBu_r", vmin=-vmax, vmax=vmax)
        axes[r_i, 2].set_title(f"raw NEW-OLD  (max|Δ|={vmax:.1f} dB,\n"
                               f"r={np.corrcoef(sm_o.ravel(), sm_n.ravel())[0,1]:.5f})", fontsize=10)
        fig.colorbar(im, ax=axes[r_i, 2], fraction=0.046, pad=0.04)
        for c in range(2):
            axes[r_i, c].set_xticks([]); axes[r_i, c].set_yticks([])

        # region-metric annotation (why it flipped)
        def fmt(m):
            return " ".join(f"{k[0]}:{(m[k] if m[k] is not None else float('nan')):.1f}"
                            for k in ("Left", "Right", "Teleoperator", "Others"))
        axes[r_i, 0].set_xlabel("P-metric  " + fmt(old_m), fontsize=8)
        axes[r_i, 1].set_xlabel("P-metric  " + fmt(new_m), fontsize=8)

        saved[f"{d['bag']}_t{d['tick_idx']}_old"] = sm_o.astype(np.float16)
        saved[f"{d['bag']}_t{d['tick_idx']}_new"] = sm_n.astype(np.float16)
        print(f"  {d['bag']} tick {d['tick_idx']}: OLD={old_lab} NEW={new_lab} | "
              f"OLD[{fmt(old_m)}]  NEW[{fmt(new_m)}]  max|Δ|={vmax:.2f}dB")

    fig.suptitle("OLD vs NEW generator — the only ticks where the 4-label differs "
                 f"({n} of 49,786)", fontsize=13, y=0.997)
    fig.tight_layout(rect=[0, 0, 1, 0.985])
    out_png = os.path.join(OUT, "disagreements.png")
    fig.savefig(out_png, dpi=140); plt.close(fig)
    np.savez_compressed(os.path.join(OUT, "disagreement_maps.npz"), **saved)
    print(f"\nwrote {out_png} and disagreement_maps.npz")


if __name__ == "__main__":
    main()
