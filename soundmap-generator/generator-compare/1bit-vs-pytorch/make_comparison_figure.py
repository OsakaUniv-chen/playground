"""Render the two illustrative cases from validate_synthetic.py as PNGs:
single dominant source (should match) and unequal two-source (should diverge).
Not a dataviz deliverable -- a quick diagnostic plot for this folder's README.
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
    return pytorch_api.generate([chunk]), onebit_api.generate([chunk]), "single source, 0dB SNR"


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
            f"two sources, secondary {rel_db}dB relative")


def _normalize(sm):
    """Per-map min-max normalization, purely so each panel's own spatial shape
    is visible. The two generators' [0,160] scales are NOT physically
    comparable magnitudes (see README) -- production's shared exp(sm-sm.max())
    contrast stretch is tuned to the FFT beamformer's much gentler dB gradient
    and crushes the 1-bit map's much steeper one to a single pixel, which is a
    display artifact, not evidence the 1-bit response is a single-pixel spike
    (see the raw neighbor-score printout in README's precision analysis)."""
    lo, hi = sm.min(), sm.max()
    return (sm - lo) / (hi - lo) if hi > lo else sm


def main():
    cases = [single_source_case(), two_source_case(-6)]
    fig, axes = plt.subplots(len(cases), 2, figsize=(7, 3.4 * len(cases)))
    for row, (sm_pt, sm_ob, title) in enumerate(cases):
        for col, (sm, label) in enumerate([(sm_pt, "FFT beamformer (pytorch)"), (sm_ob, "1-bit XOR")]):
            ax = axes[row, col]
            im = ax.imshow(_normalize(sm), vmin=0, vmax=1, cmap="inferno", origin="lower")
            ax.set_title(f"{label}\n{title}", fontsize=9)
            ax.set_xticks([]); ax.set_yticks([])
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    out_path = os.path.join(OUT, "comparison.png")
    fig.savefig(out_path, dpi=140)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
