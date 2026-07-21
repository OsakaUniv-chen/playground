"""Sample balanced ticks across bags, regenerate sound map + frame, render.

For each sampled tick we render "gray fisheye + jet sound-map overlay" (the map
being the exact masked+transformed one the GT labeler saw) and store gt_label.

Output: manifest2.csv (id, bag, tick_ts, gt_label, vad_active) + images/<id>.png
Also a QC contact sheet (with head/tele boxes drawn) for human verification --
the boxes are NOT in the images shown to the VLM.
"""
from __future__ import annotations
import argparse
import csv
import random
from collections import defaultdict, Counter
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from common2 import (TICKS, LABELS, bag_dir, load_audio, gen_sm, frame_at,
                     label_input_sm, render_overlay, SPEAKING_BOX)
import bag_io as B
from soundmap_api import SoundMapAPI

HERE = Path(__file__).parent
IMG_DIR = HERE / "images"
MANIFEST = HERE / "manifest2.csv"

N_PER_LABEL = 40
MAX_PER_BAG = 16
N_BAGS = 16
SEED = 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-label", type=int, default=N_PER_LABEL)
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args()

    rng = random.Random(SEED)
    parquets = sorted(TICKS.glob("*.parquet"))
    rng.shuffle(parquets)
    parquets = parquets[:N_BAGS]

    # 1) pool candidate ticks (cheap: parquet only), balance per label with per-bag cap
    pool = defaultdict(list)       # label -> [(bag, tick_ts, vad)]
    for pq_path in parquets:
        bag = pq_path.stem
        df = pd.read_parquet(pq_path)
        per_bag = defaultdict(int)
        idx = list(df.index)
        rng.shuffle(idx)
        for i in idx:
            r = df.loc[i]
            lab = r["gt_label"]
            if lab not in LABELS or per_bag[lab] >= MAX_PER_BAG:
                continue
            pool[lab].append((bag, int(r["tick_ts"]), bool(r["vad_active"])))
            per_bag[lab] += 1

    chosen = []
    for lab in LABELS:
        cand = pool[lab]
        rng.shuffle(cand)
        take = cand[:args.n_per_label]
        for bag, ts, vad in take:
            chosen.append(dict(bag=bag, tick_ts=ts, gt_label=lab, vad_active=vad))
    rng.shuffle(chosen)
    print("target per label:", {l: min(len(pool[l]), args.n_per_label) for l in LABELS})

    # 2) regenerate per bag (open each bag once)
    IMG_DIR.mkdir(exist_ok=True)
    by_bag = defaultdict(list)
    for c in chosen:
        by_bag[c["bag"]].append(c)

    sm_api = SoundMapAPI(device=args.device)
    rows, qc = [], []
    for bag, items in by_bag.items():
        con = B.open_bag(bag_dir(bag))
        a_ts, a_d = load_audio(con)
        cam_tid = B.topic_id(con, B.CAMERA_TOPIC)
        for c in items:
            sm = gen_sm(sm_api, a_ts, a_d, c["tick_ts"])
            fr = frame_at(con, cam_tid, c["tick_ts"])
            if sm is None or fr is None:
                continue
            smt = label_input_sm(sm, c["vad_active"])
            img = render_overlay(fr, smt)
            rid = f"{len(rows):03d}"
            plt.imsave(IMG_DIR / f"{rid}.png", img)
            rows.append(dict(id=rid, bag=bag, tick_ts=c["tick_ts"],
                             gt_label=c["gt_label"], vad_active=int(c["vad_active"])))
            qc.append((rid, fr, smt, c["gt_label"]))
        con.close()

    rows.sort(key=lambda r: r["id"])
    with MANIFEST.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    # QC sheet with boxes (NOT shown to VLM)
    n = min(12, len(qc))
    fig, ax = plt.subplots(2, n // 2, figsize=(2.4 * (n // 2), 5))
    ax = ax.ravel()
    for i in range(n):
        rid, fr, smt, lab = qc[i]
        import cv2, matplotlib.cm as cm
        gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        heat = (cm.jet(cv2.resize(smt / (smt.max() + 1e-9), gray.shape[::-1]))[..., :3] * 255).astype(np.uint8)
        blend = (0.55 * np.stack([gray] * 3, -1) + 0.45 * heat).astype(np.uint8)
        sx, sy, sw, sh = SPEAKING_BOX
        cv2.rectangle(blend, (sx, sy), (sx + sw, sy + sh), (255, 0, 255), 4)
        ax[i].imshow(blend); ax[i].set_title(f"{rid} GT={lab}", fontsize=8); ax[i].axis("off")
    fig.suptitle("QC (magenta=tele box; boxes NOT shown to VLM)")
    fig.tight_layout(); fig.savefig(HERE / "qc_sheet.png", dpi=85)

    print(f"wrote {len(rows)} samples -> {MANIFEST}")
    print("label dist:", dict(Counter(r["gt_label"] for r in rows)))


if __name__ == "__main__":
    main()
