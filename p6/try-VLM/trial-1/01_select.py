"""Stage-1 sample selection (clean single-lobe, rotation-balanced).

Pick sound-map frames with ONE clear, well-localised source lobe -- the easy
regime. If a VLM can't read even a clean single-lobe heatmap, then overlaying
the sound map on the camera image is a dead end and we stop there.

Two data realities force the design:
  (a) Many frames are diffuse or bimodal (two people face-to-face -> hot top AND
      bottom). We reject those via an angular-energy profile (unimodality /
      concentration filters), keeping only single-direction frames.
  (b) Real sources pile at 12 o'clock (seating prior). To measure *reading*
      ability rather than the prior, each kept frame is rotated by a chosen
      angle so all 12 clock directions are covered evenly. GT is re-derived from
      the ROTATED map -- the exact image the VLM sees.

Output: manifest.csv (bag, frame, rot_deg + GT labels + cleanliness scores).
"""
from __future__ import annotations
import csv
import random
from collections import Counter
from pathlib import Path
import numpy as np

from common import DATA_DIR, source_info, rotate_scalar

OUT = Path(__file__).parent / "manifest.csv"

PER_CLOCK = 5            # 12 * 5 = 60 frames
MIN_CONC = 0.55         # >=55% of excess energy within +-45 deg of the lobe
MIN_UNIMOD = 0.55       # 2nd lobe < 45% of main lobe (single clear source)
MIN_RADIUS = 12.0       # lobe off-centre enough that direction is meaningful
MIN_EXCESS = 8.0        # lobe must actually stand above baseline
MAX_PER_BAG = 2
SEED = 0


def clean(info: dict) -> bool:
    return (info["concentration"] >= MIN_CONC and info["unimodality"] >= MIN_UNIMOD
            and info["radius"] >= MIN_RADIUS and info["total_excess"] >= MIN_EXCESS)


def main() -> None:
    rng = random.Random(SEED)
    bags = sorted(DATA_DIR.glob("*.npz"))
    rng.shuffle(bags)

    # 1) pool of clean single-lobe frames (raw azimuth)
    pool = []
    n_seen = n_clean = 0
    for bag in bags:
        sm = np.load(bag)["soundmap"]
        picked = 0
        order = list(range(len(sm)))
        rng.shuffle(order)
        for fr in order:
            n_seen += 1
            if picked >= MAX_PER_BAG:
                break
            info = source_info(sm[fr])
            if not clean(info):
                continue
            n_clean += 1
            pool.append((bag.stem, fr, info["azimuth"]))
            picked += 1
        if len(pool) >= 12 * PER_CLOCK * 6:   # enough headroom for balancing
            break
    rng.shuffle(pool)
    print(f"scanned {n_seen} frames; clean single-lobe hit-rate "
          f"~{n_clean/max(n_seen,1):.1%}; pool={len(pool)}")

    # 2) rotate each toward a needed clock direction; keep if still clean there
    need = {c: PER_CLOCK for c in range(1, 13)}
    rows = []
    cache: dict[str, np.ndarray] = {}
    for bag, fr, nat_az in pool:
        remaining = [c for c, k in need.items() if k > 0]
        if not remaining:
            break
        target_clock = rng.choice(remaining)
        target_az = (target_clock % 12) * 30.0
        rot = (target_az - nat_az) % 360.0
        if bag not in cache:
            cache[bag] = np.load(DATA_DIR / f"{bag}.npz")["soundmap"]
        info = source_info(rotate_scalar(cache[bag][fr], rot))   # GT from rotated map
        if not clean(info) or need[info["clock"]] <= 0:
            continue
        need[info["clock"]] -= 1
        rows.append(dict(
            id=f"{len(rows):03d}", bag=bag, frame=fr, rot_deg=round(rot, 1),
            gt_clock=info["clock"], gt_quadrant=info["quadrant"],
            gt_azimuth=round(info["azimuth"], 1),
            concentration=round(info["concentration"], 2),
            unimodality=round(info["unimodality"], 2),
            radius=round(info["radius"], 1),
        ))

    rows.sort(key=lambda r: r["id"])
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    cl = Counter(r["gt_clock"] for r in rows)
    print(f"selected {len(rows)} frames -> {OUT}")
    print("clock dist:", {c: cl.get(c, 0) for c in range(1, 13)})


if __name__ == "__main__":
    main()
