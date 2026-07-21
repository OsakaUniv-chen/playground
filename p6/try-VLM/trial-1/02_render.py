"""Render each selected sound map as a PURE jet heatmap PNG (what the VLM sees).

Representation under test = "纯 Jet 热力图": the beamforming map only, in-disc
contrast-stretched (see common.jet_rgb), jet colormap, 512x512, nearest upscale,
NO axes / labels / compass / camera. The clock convention lives in the prompt.

Also writes a human-only QC contact sheet (heatmap + GT direction arrow +
camera). The camera row is for us to verify GT; it is NOT shown to the VLM.
"""
from __future__ import annotations
import csv
import math
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from common import DATA_DIR, rotate_scalar, jet_rgb, CX, CY

HERE = Path(__file__).parent
IMG_DIR = HERE / "images"
MANIFEST = HERE / "manifest.csv"


def main() -> None:
    IMG_DIR.mkdir(exist_ok=True)
    rows = list(csv.DictReader(MANIFEST.open()))
    cache: dict[str, tuple] = {}
    qc = []
    for r in rows:
        bag, fr, rot = r["bag"], int(r["frame"]), float(r["rot_deg"])
        if bag not in cache:
            d = np.load(DATA_DIR / f"{bag}.npz")
            cache[bag] = (d["soundmap"], d["gray_camimg"])
        sm, cam = cache[bag]
        smf = rotate_scalar(sm[fr], rot)
        plt.imsave(IMG_DIR / f"{r['id']}.png", jet_rgb(smf))
        qc.append((r, smf, cam[fr]))

    n = min(12, len(qc))
    fig, ax = plt.subplots(2, n, figsize=(2.2 * n, 4.6))
    for i in range(n):
        r, smf, camf = qc[i]
        ax[0, i].imshow(jet_rgb(smf, size=64)); ax[0, i].axis("off")
        ax[0, i].set_title(f"{r['id']} clk{r['gt_clock']}", fontsize=8)
        # GT direction arrow from centre
        az = math.radians(float(r["gt_azimuth"]))
        ax[0, i].annotate("", xy=(CX + 26 * math.sin(az), CY - 26 * math.cos(az)),
                          xytext=(CX, CY),
                          arrowprops=dict(color="white", width=1.2, headwidth=6))
        ax[1, i].imshow(camf, cmap="gray"); ax[1, i].axis("off")
    fig.suptitle("QC: row1 heatmap+GT arrow (VLM sees heatmap only) | row2 camera (NOT shown)")
    fig.tight_layout()
    fig.savefig(HERE / "qc_contact_sheet.png", dpi=90)
    print(f"rendered {len(rows)} heatmaps -> {IMG_DIR}")
    print(f"QC sheet -> {HERE / 'qc_contact_sheet.png'}")


if __name__ == "__main__":
    main()
