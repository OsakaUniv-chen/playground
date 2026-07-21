"""Synthetic control: textbook-clean single-Gaussian heatmaps.

Same rendering pipeline (common.jet_rgb) as the real probe, so the ONLY thing
that changes is cleanliness. Purpose: separate two failure modes --
  * fails synthetic too  -> the model simply can't read blob direction off a
    jet heatmap (a capability ceiling; representation/model must change)
  * passes synthetic, fails real -> the sound map's noise / diffuseness is the
    blocker (preprocessing / denoising could help)

Outputs manifest_synth.csv + images_synth/ in the same format as the real set,
so 03_probe_localize.py runs on it unchanged via --manifest/--imgdir.
"""
from __future__ import annotations
import csv
import math
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from common import (H, W, CX, CY, DISC, jet_rgb, azimuth_to_clock,
                    azimuth_to_quadrant)

HERE = Path(__file__).parent
IMG_DIR = HERE / "images_synth"
MANIFEST = HERE / "manifest_synth.csv"

PER_CLOCK = 5
BLOB_R = 20.0        # blob centre radius from image centre
SIGMA = 5.5          # blob spread
BASELINE = 0.15      # low uniform floor so contrast-stretch behaves like real
SEED = 0


def synth(azimuth_deg: float) -> np.ndarray:
    cx = CX + BLOB_R * math.sin(math.radians(azimuth_deg))
    cy = CY - BLOB_R * math.cos(math.radians(azimuth_deg))
    yy, xx = np.mgrid[0:H, 0:W]
    g = np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * SIGMA ** 2))
    f = (BASELINE + g).astype(np.float32)
    f[~DISC] = 0.0
    return f


def main() -> None:
    rng = np.random.default_rng(SEED)
    IMG_DIR.mkdir(exist_ok=True)
    rows = []
    for clock in range(1, 13):
        for _ in range(PER_CLOCK):
            # jitter within the clock's 30-deg bin so it's not always dead-centre
            az = ((clock % 12) * 30.0 + rng.uniform(-10, 10)) % 360.0
            f = synth(az)
            i = len(rows)
            plt.imsave(IMG_DIR / f"{i:03d}.png", jet_rgb(f))
            rows.append(dict(id=f"{i:03d}", gt_clock=azimuth_to_clock(az),
                             gt_quadrant=azimuth_to_quadrant(az),
                             gt_azimuth=round(az, 1)))
    with MANIFEST.open("w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"wrote {len(rows)} synthetic frames -> {IMG_DIR} + {MANIFEST}")


if __name__ == "__main__":
    main()
