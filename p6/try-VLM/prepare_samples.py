"""Prepare default inputs for playground.py.

Populates playground_samples/ with:
  overlay_<Label>.png   one trial-2 tick per 4-label (gray fisheye + jet soundmap),
                        filename carries the ground-truth label for sanity checks
  scene_gray.png        the same tick as overlay_Right, camera only (no soundmap)
  soundmap_only.png     that tick's pure jet soundmap (no camera)  -- so you can
                        try feeding "scene + soundmap" as TWO separate images

Reuses trial-2's regeneration machinery. Needs the WordWolfExp bag SSD mounted.
"""
from __future__ import annotations
import csv
import shutil
import sys
from pathlib import Path
import numpy as np
import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.cm as cm

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "trial-2"))
from common2 import (bag_dir, load_audio, gen_sm, frame_at, label_input_sm,  # noqa: E402
                     render_overlay)
import bag_io as B                                                            # noqa: E402
from soundmap_api import SoundMapAPI                                          # noqa: E402

T2 = HERE / "trial-2"
OUT = HERE / "playground_samples"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    man = {r["gt_label"]: r for r in csv.DictReader((T2 / "manifest2.csv").open())}
    # one overlay per label (already rendered in trial-2/images)
    picked = {}
    for r in csv.DictReader((T2 / "manifest2.csv").open()):
        lab = r["gt_label"]
        if lab not in picked:
            picked[lab] = r
            src = T2 / "images" / f"{r['id']}.png"
            if src.exists():
                shutil.copy(src, OUT / f"overlay_{lab}.png")

    # regenerate scene-only gray + pure soundmap for the Right sample
    r = picked.get("Right") or next(iter(picked.values()))
    con = B.open_bag(bag_dir(r["bag"]))
    a_ts, a_d = load_audio(con)
    cam_tid = B.topic_id(con, B.CAMERA_TOPIC)
    sm = gen_sm(SoundMapAPI(device="cpu"), a_ts, a_d, int(r["tick_ts"]))
    fr = frame_at(con, cam_tid, int(r["tick_ts"]))
    con.close()

    gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
    plt.imsave(OUT / "scene_gray.png", cv2.resize(gray, (768, 768)), cmap="gray")
    smt = label_input_sm(sm, bool(int(r["vad_active"])))
    heat = cm.jet(cv2.resize(smt / (smt.max() + 1e-9), (768, 768)))[..., :3]
    plt.imsave(OUT / "soundmap_only.png", (heat * 255).astype(np.uint8))

    print("prepared:", sorted(p.name for p in OUT.iterdir()))
    print(f"(scene_gray/soundmap_only are the '{r['gt_label']}' tick: bag={r['bag']})")


if __name__ == "__main__":
    main()
