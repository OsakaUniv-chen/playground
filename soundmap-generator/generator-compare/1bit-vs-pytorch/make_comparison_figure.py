"""Render the two illustrative cases from validate_synthetic.py as PNGs:
single dominant source (should match) and unequal two-source (should diverge).
Not a dataviz deliverable -- a quick diagnostic plot for this folder's README.

Each case is shown under BOTH display conventions, for both generators, so the
"which of this is the algorithm and which is the colour map" question can be
answered by looking rather than argued about:

  col 1  FFT beamformer   min-max          col 3  1-bit XOR   min-max
  col 2  FFT beamformer   exp(sm-sm.max()) col 4  1-bit XOR   exp(sm-sm.max())

min-max is per-panel `(sm-min)/(max-min)`; exp is `utils.transform_sm`, the
transform the LIVE pipeline actually applies (DoADetector.transform_sound_map)
before both the overlay and the percentile-based 4-label decision. The exp
columns are what production sees; the min-max columns are the underlying
spatial shape with the contrast stretch taken out of the way.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
# Reused generators + shared utils now live outside this folder:
#   ../../generator-pytorch/new_soundmap_api.py     (FFT/pytorch reference generator)
#   ../../generator-1bit/onebit_soundmap.py          (the 1-bit generator)
#   ../utils.py                                       (shared comparison helpers)
for _p in (os.path.join(_HERE, "..", "..", "generator-pytorch"),
           os.path.join(_HERE, "..", "..", "generator-1bit"),
           os.path.join(_HERE, "..")):
    _p = os.path.normpath(_p)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import butter, sosfiltfilt

from onebit_soundmap import OneBitSoundMapAPI, MIC_POSITIONS
from new_soundmap_api import NewSoundMapAPI as SoundMapAPI
from utils import transform_sm
import validate_synthetic as vs

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
os.makedirs(OUT, exist_ok=True)


def single_source_case():
    pytorch_api = SoundMapAPI(device="cpu")
    onebit_api = OneBitSoundMapAPI(device="cpu")
    gpos = onebit_api._gen.gpos
    gi = int(np.argmin(np.hypot(gpos[0] - 0.5, gpos[1] + 0.5)))
    rng = np.random.default_rng(42)
    chunk = vs.synthesize(tuple(gpos[:, gi]), 0, rng)
    return pytorch_api.generate([chunk]), onebit_api.generate([chunk]), "single source\n0dB SNR"


def two_source_case(rel_db=-6):
    pytorch_api = SoundMapAPI(device="cpu")
    onebit_api = OneBitSoundMapAPI(device="cpu")
    gpos = onebit_api._gen.gpos
    p_gi = int(np.argmin(np.hypot(gpos[0] - 0.5, gpos[1] + 0.5)))
    s_gi = int(np.argmin(np.hypot(gpos[0] + 0.5, gpos[1] - 0.3)))
    p_xyz, s_xyz = tuple(gpos[:, p_gi]), tuple(gpos[:, s_gi])

    rng = np.random.default_rng(vs.SEED + 999)
    total = vs.N + 2 * vs.GUARD
    sos = butter(4, vs.BAND, btype="bandpass", fs=vs.FS, output="sos")
    src_p = sosfiltfilt(sos, rng.standard_normal(total))
    src_s = sosfiltfilt(sos, rng.standard_normal(total)) * (10 ** (rel_db / 20))

    mic_sig = np.zeros((total, vs.CHANNELS))
    for m in range(vs.CHANNELS):
        dp = np.linalg.norm(np.asarray(p_xyz) - MIC_POSITIONS[:, m])
        ds = np.linalg.norm(np.asarray(s_xyz) - MIC_POSITIONS[:, m])
        mic_sig[:, m] += vs._fft_delay(src_p, dp / vs.SOUND_SPEED * vs.FS)
        mic_sig[:, m] += vs._fft_delay(src_s, ds / vs.SOUND_SPEED * vs.FS)
    mic_sig += rng.standard_normal(mic_sig.shape) * 0.02 * np.std(mic_sig)

    crop = mic_sig[vs.GUARD:vs.GUARD + vs.N]
    scale = 100.0 / (np.max(np.abs(crop)) + 1e-9)
    audio_i16 = np.clip(crop * scale, -32768, 32767).astype(np.int16)
    chunk = audio_i16.tobytes()
    return (pytorch_api.generate([chunk]), onebit_api.generate([chunk]),
            f"two sources\nsecondary {rel_db}dB")


def _normalize(sm):
    """Per-map min-max normalization, purely so each panel's own spatial shape
    is visible. The two generators' [0,160] scales are NOT physically
    comparable magnitudes (see README): the FFT beamformer emits true dB
    (10*log10(power/4e-10)) while the 1-bit generator emits a linear match
    rate times GAIN, so a min-max panel of the former always looks broader
    than one of the latter for reasons that are partly scale convention and
    not only beam width. The exp() columns beside them are the fair
    comparison in the sense that matters operationally: they are the identical
    transform production applies to both.
    """
    lo, hi = sm.min(), sm.max()
    return (sm - lo) / (hi - lo) if hi > lo else sm


# Fraction of pixels still "visible" after a transform -- the same >0.05
# criterion `onebit_soundmap.GAIN` was calibrated against (see its docstring).
def _visible_frac(disp):
    return float(np.mean(disp > 0.05))


COLUMNS = [
    ("FFT beamformer\nmin-max", "pt", _normalize),
    ("FFT beamformer\nexp(sm - sm.max())", "pt", transform_sm),
    ("1-bit XOR\nmin-max", "ob", _normalize),
    ("1-bit XOR\nexp(sm - sm.max())", "ob", transform_sm),
]


def main():
    cases = [single_source_case(), two_source_case(-6)]

    fig, axes = plt.subplots(len(cases), len(COLUMNS), figsize=(13.2, 3.5 * len(cases)))
    for row, (sm_pt, sm_ob, row_label) in enumerate(cases):
        raw = {"pt": sm_pt, "ob": sm_ob}
        for col, (col_title, which, transform) in enumerate(COLUMNS):
            ax = axes[row, col]
            sm = raw[which]
            disp = transform(sm)
            im = ax.imshow(disp, vmin=0, vmax=1, cmap="inferno", origin="lower")
            if row == 0:
                ax.set_title(col_title, fontsize=9)
            if col == 0:
                ax.set_ylabel(row_label, fontsize=9)
            ax.set_xticks([]); ax.set_yticks([])
            ax.text(0.03, 0.03,
                    f"raw max {sm.max():.1f}\n>0.05: {_visible_frac(disp) * 100:.1f}%",
                    transform=ax.transAxes, fontsize=7, color="white",
                    va="bottom", ha="left")
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    fig.suptitle("Display convention vs. generator "
                 "(1.0 = each panel's OWN max; absolute values are not comparable across panels)",
                 fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    out_path = os.path.join(OUT, "comparison.png")
    fig.savefig(out_path, dpi=140)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
