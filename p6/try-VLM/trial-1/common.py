"""Shared helpers for the try-VLM probe.

Sound map data comes from train-pssp/train-data/*.npz:
  soundmap    (T, 64, 64) float32  -- beamforming energy on the fisheye image plane
  gray_camimg (T, 64, 64) uint8    -- grayscale fisheye camera frame (spatially aligned)
  tick_ts     (T,)        int64

The camera is omnidirectional (fisheye): the image centre is directly under the
sensor, directions radiate outward. We treat the frame as a clock face:
top edge = 12 o'clock, right = 3, bottom = 6, left = 9 (clockwise).
"""
from __future__ import annotations
import math
from pathlib import Path
import numpy as np
from PIL import Image

# train-pssp data pool (447 npz as of 2026-07)
DATA_DIR = Path("/home/chen/Documents/Playground/train-pssp/train-data")

H = W = 64
CX = CY = (H - 1) / 2.0  # 31.5
DISC_R = 31.0            # fisheye disc radius (data is 0 outside)

# precomputed per-pixel geometry
_yy, _xx = np.mgrid[0:H, 0:W]
_dx = _xx - CX
_dy = _yy - CY
RAD = np.hypot(_dx, _dy)
AZ = (np.degrees(np.arctan2(_dx, -_dy)) % 360.0)   # top=0, cw
DISC = RAD <= DISC_R


def azimuth_deg(y: float, x: float) -> float:
    """Clockwise azimuth in [0,360) with top edge (12 o'clock) = 0 deg."""
    dx = x - CX
    dy = y - CY
    ang = math.degrees(math.atan2(dx, -dy))  # top=0, right=90, bottom=180, left=270
    return ang % 360.0


def radius(y: float, x: float) -> float:
    return math.hypot(x - CX, y - CY)


def azimuth_to_clock(ang: float) -> int:
    """Map azimuth deg -> clock number 1..12 (12 at the top)."""
    c = int(round(ang / 30.0)) % 12
    return 12 if c == 0 else c


def clock_circ_dist(a: int, b: int) -> int:
    """Circular distance between two clock numbers 1..12 (0..6)."""
    d = abs((a % 12) - (b % 12))
    return min(d, 12 - d)


QUAD_NAMES = ["up", "right", "down", "left"]


def azimuth_to_quadrant(ang: float) -> str:
    q = int(round(ang / 90.0)) % 4  # 0=up,1=right,2=down,3=left
    return QUAD_NAMES[q]


def jet_rgb(sm_frame: np.ndarray, lo_p: float = 55.0, hi_p: float = 99.5,
            size: int = 512) -> np.ndarray:
    """Colorize a sound map for the VLM: in-disc contrast-stretch + jet.

    Naive min-max fails because ~25% of pixels are the out-of-disc corners (=0),
    which drag the min to 0 and wash the whole disc red. We stretch using
    percentiles of the in-disc values so the source lobe pops as red and quieter
    disc regions read as blue/green. Out-of-disc stays dark blue (jet min).
    Nearest upscale (no smoothing => no invented structure).
    """
    import matplotlib.cm as cm
    f = sm_frame.astype(np.float64)
    m = DISC & (f > 0)
    lo = np.percentile(f[m], lo_p)
    hi = np.percentile(f[m], hi_p)
    n = np.clip((f - lo) / (hi - lo + 1e-9), 0, 1)
    n[~DISC] = 0.0
    rgb = (cm.jet(n)[..., :3] * 255).astype(np.uint8)
    rep = size // sm_frame.shape[0]
    return np.repeat(np.repeat(rgb, rep, axis=0), rep, axis=1)


def rotate_scalar(sm_frame: np.ndarray, deg: float) -> np.ndarray:
    """Rotate the scalar sound map by `deg` (clockwise, about the image centre).

    Used to place the energy peak at controlled, balanced azimuths so the probe
    measures reading ability, not the seating prior. Rotation is done on the raw
    float field (PIL 'F' mode, bilinear) so GT is re-derived from the exact map
    shown to the VLM. deg>0 rotates the content clockwise (azimuth += deg).
    """
    if deg % 360 == 0:
        return sm_frame.astype(np.float32)
    img = Image.fromarray(sm_frame.astype(np.float32), mode="F")
    # PIL rotates counter-clockwise for positive angle -> negate for clockwise
    out = img.rotate(-deg, resample=Image.BILINEAR, fillcolor=0.0)
    return np.asarray(out, dtype=np.float32)


def _excess(f: np.ndarray) -> np.ndarray:
    """In-disc energy above the disc median (the lobe(s) over baseline)."""
    base = float(np.median(f[DISC]))
    ex = np.clip(f - base, 0.0, None)
    ex[~DISC] = 0.0
    return ex


def angular_profile(f: np.ndarray, nbins: int = 36) -> np.ndarray:
    """Excess energy summed into `nbins` azimuth bins (circularly smoothed)."""
    ex = _excess(f.astype(np.float64))
    bins = (AZ / (360.0 / nbins)).astype(int) % nbins
    prof = np.bincount(bins[DISC], weights=ex[DISC], minlength=nbins)
    # circular 3-bin smoothing
    return (prof + np.roll(prof, 1) + np.roll(prof, -1)) / 3.0


def source_info(f: np.ndarray) -> dict:
    """Direction of the dominant sound lobe + how clean/unimodal the frame is.

    - azimuth: excess-energy-weighted circular mean around the peak sector
    - clock / quadrant: discretised direction
    - concentration: fraction of excess energy within +-45 deg of the peak
      (1.0 = all energy in one direction; low = diffuse)
    - unimodality: 1 - (2nd lobe / 1st lobe); high = single clean lobe
    - radius: mean radius of the peak-sector energy (near-centre = ambiguous dir)
    """
    f = f.astype(np.float64)
    nb = 36
    prof = angular_profile(f, nb)
    total = prof.sum() + 1e-9
    peak_bin = int(np.argmax(prof))
    peak_az = peak_bin * (360.0 / nb)

    # concentration within +-45 deg of peak
    d = np.minimum(np.abs(np.arange(nb) - peak_bin), nb - np.abs(np.arange(nb) - peak_bin))
    near = d <= (45 / (360.0 / nb))
    concentration = float(prof[near].sum() / total)

    # 2nd lobe: strongest bin at least 60 deg away from peak
    far = d >= (60 / (360.0 / nb))
    second = float(prof[far].max()) if far.any() else 0.0
    unimodality = float(1.0 - second / (prof[peak_bin] + 1e-9))

    # refined azimuth: circular mean of pixel azimuths weighted by excess,
    # restricted to the peak sector (+-45 deg), + mean radius there
    ex = _excess(f)
    sector = near[(AZ / (360.0 / nb)).astype(int) % nb] & DISC
    w = ex[sector]
    if w.sum() > 0:
        a = np.radians(AZ[sector])
        az = float(np.degrees(np.arctan2(np.sum(w * np.sin(a)), np.sum(w * np.cos(a)))) % 360.0)
        rad = float(np.average(RAD[sector], weights=w))
    else:
        az, rad = peak_az, DISC_R / 2

    return dict(
        azimuth=az, clock=azimuth_to_clock(az), quadrant=azimuth_to_quadrant(az),
        concentration=concentration, unimodality=unimodality, radius=rad,
        total_excess=float(total),
    )
