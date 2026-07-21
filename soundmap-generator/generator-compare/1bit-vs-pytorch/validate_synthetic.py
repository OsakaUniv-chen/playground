"""Synthetic point-source validation: 1-bit generator vs the FFT beamformer.

The ROSbag SSD (real 16ch recordings) wasn't mounted when this was written, so
correctness is validated with a controlled simulation instead of real audio:

  1. Pick a grid point as ground truth, synthesize band-limited (2-8kHz) noise
     "speech", propagate it to all 16 mics with the exact (fractional-sample,
     FFT-phase-shift) delay implied by true geometry, add mic self-noise at a
     chosen SNR, quantize to int16 -- i.e. build a byte-identical audio_chunk
     to what bag_io.decode_audio would hand either generator.
  2. Feed the SAME synthetic chunk to both:
       - new_soundmap_api.NewSoundMapAPI    (../../generator-pytorch, FFT power sum)
       - onebit_soundmap.OneBitSoundMapAPI  (../../generator-1bit, bit-shift + XOR)
  3. Compare peak-pixel localization error against the known ground truth, and
     the Pearson correlation between the two normalized maps, across a range
     of SNRs. This is exactly the question "does 1-bit degrade precision?"
     turned into a number instead of a guess.

Run inside the wolf venv (needs torch, numpy, scipy, cv2):
    python validate_synthetic.py
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
from scipy.signal import butter, sosfiltfilt

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

from onebit_soundmap import OneBitSoundMapAPI, MIC_POSITIONS  # noqa: E402
from new_soundmap_api import NewSoundMapAPI as SoundMapAPI  # noqa: E402

FS = 44100
CHANNELS = 16
N = 20000            # ~0.45s window, matches the real ~160-msg pipeline window
GUARD = 1200          # > max possible inter-mic delay for any grid point (~900 samples)
SOUND_SPEED = 345
BAND = (2000, 8000)
SEED = 0


def _fft_delay(x: np.ndarray, delay_samples: float) -> np.ndarray:
    """Shift x later in time by (fractional) delay_samples, via FFT phase ramp."""
    n = len(x)
    freqs = np.fft.rfftfreq(n)
    spec = np.fft.rfft(x)
    spec *= np.exp(-2j * np.pi * freqs * delay_samples)
    return np.fft.irfft(spec, n)


def synthesize(source_xyz, snr_db, rng):
    """Return raw int16/16ch-interleaved bytes for one audio chunk."""
    total = N + 2 * GUARD
    sos = butter(4, BAND, btype="bandpass", fs=FS, output="sos")
    source = sosfiltfilt(sos, rng.standard_normal(total))

    mic_sig = np.empty((total, CHANNELS))
    for m in range(CHANNELS):
        dist = np.linalg.norm(np.asarray(source_xyz) - MIC_POSITIONS[:, m])
        delay = dist / SOUND_SPEED * FS
        mic_sig[:, m] = _fft_delay(source, delay)

    sig_power = float(np.mean(mic_sig ** 2))
    noise = rng.standard_normal(mic_sig.shape)
    noise_power = float(np.mean(noise ** 2))
    target_noise_power = sig_power / (10 ** (snr_db / 10))
    noise *= np.sqrt(target_noise_power / max(noise_power, 1e-30))
    mic_sig += noise

    crop = mic_sig[GUARD:GUARD + N]
    # Raw mic counts stay well below int16 full-scale (the FFT generator applies
    # a +30dB/x31.6 gain internally before re-quantizing to int16, and pushing this
    # target much above ~100 saturates its [0,160]dB output almost everywhere --
    # a scaling artifact of this synthetic harness, not a beamforming difference).
    scale = 100.0 / (np.max(np.abs(crop)) + 1e-9)
    audio_i16 = np.clip(crop * scale, -32768, 32767).astype(np.int16)
    return audio_i16.tobytes()


def expected_peak_pixel(onebit_api, grid_idx):
    """Where a perfect one-hot score at this grid index lands after interpolation."""
    gen = onebit_api._gen
    one_hot = np.zeros(gen.n_grid)
    one_hot[grid_idx] = 160.0
    sm = gen._interpolate_to_soundmap(one_hot)
    return np.unravel_index(int(np.argmax(sm)), sm.shape)


def pearson(a, b):
    a = a.ravel().astype(np.float64)
    b = b.ravel().astype(np.float64)
    if a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def main():
    pytorch_api = SoundMapAPI(device="cpu")
    onebit_api = OneBitSoundMapAPI(device="cpu")

    rng_pick = np.random.default_rng(SEED)
    # a handful of interior grid points (skip the very edge of the merged grid)
    gpos = onebit_api._gen.gpos
    interior = np.where((np.abs(gpos[0]) < 2.0) & (np.abs(gpos[1]) < 2.0))[0]
    test_idx = rng_pick.choice(interior, size=5, replace=False)

    snr_levels = [20, 10, 5, 0, -5, -10, -15, -20, -25, -30]
    print(f"{'SNR(dB)':>8} {'pt_err_px':>10} {'1bit_err_px':>12} {'pearson(pt,1bit)':>18} "
          f"{'pt_ms':>8} {'1bit_ms':>9}")

    for snr in snr_levels:
        pt_errs, ob_errs, corrs, pt_times, ob_times = [], [], [], [], []
        for gi in test_idx:
            source_xyz = (float(gpos[0, gi]), float(gpos[1, gi]), float(gpos[2, gi]))
            rng = np.random.default_rng(SEED + int(gi) * 10_000 + int(round((snr + 20) * 100)))
            chunk = synthesize(source_xyz, snr, rng)

            t0 = time.perf_counter()
            sm_pt = pytorch_api.generate([chunk])
            pt_times.append(time.perf_counter() - t0)

            t0 = time.perf_counter()
            sm_ob = onebit_api.generate([chunk])
            ob_times.append(time.perf_counter() - t0)

            exp_px = np.array(expected_peak_pixel(onebit_api, gi))
            pt_px = np.array(np.unravel_index(int(np.argmax(sm_pt)), sm_pt.shape))
            ob_px = np.array(np.unravel_index(int(np.argmax(sm_ob)), sm_ob.shape))

            pt_errs.append(np.linalg.norm(pt_px - exp_px))
            ob_errs.append(np.linalg.norm(ob_px - exp_px))
            corrs.append(pearson(sm_pt, sm_ob))

        print(f"{snr:>8} {np.mean(pt_errs):>10.2f} {np.mean(ob_errs):>12.2f} "
              f"{np.nanmean(corrs):>18.3f} {1000*np.mean(pt_times):>8.1f} "
              f"{1000*np.mean(ob_times):>9.1f}")


def two_source_test():
    """Weak second source near a strong one: does either method 'capture' (mask)
    the weak source, and does it show up as a separate local peak at all?
    1-bit correlators are a hard nonlinearity (sign of the sum, not the sum of
    signs), so theory predicts a stronger capture effect than a linear
    delay-and-sum beamformer -- this checks whether that shows up in practice."""
    pytorch_api = SoundMapAPI(device="cpu")
    onebit_api = OneBitSoundMapAPI(device="cpu")
    gpos = onebit_api._gen.gpos

    # two interior points a few grid cells apart, one held fixed as "primary"
    primary_gi = int(np.argmin(np.hypot(gpos[0] - 0.5, gpos[1] + 0.5)))
    secondary_gi = int(np.argmin(np.hypot(gpos[0] + 0.5, gpos[1] - 0.3)))
    p_xyz = tuple(gpos[:, primary_gi])
    s_xyz = tuple(gpos[:, secondary_gi])
    exp_p = np.array(expected_peak_pixel(onebit_api, primary_gi))
    exp_s = np.array(expected_peak_pixel(onebit_api, secondary_gi))
    print(f"\nprimary grid xy={p_xyz[:2]} px={tuple(exp_p)}   "
          f"secondary grid xy={s_xyz[:2]} px={tuple(exp_s)}")

    print(f"{'sec_rel_dB':>10} {'pt_sec/pt_max':>14} {'1bit_sec/1bit_max':>18} "
          f"{'pt_2nd_peak_ok':>15} {'1bit_2nd_peak_ok':>17}")

    for rel_db in [0, -6, -12, -18]:
        rng = np.random.default_rng(SEED + 999)
        total = N + 2 * GUARD
        from scipy.signal import butter, sosfiltfilt
        sos = butter(4, BAND, btype="bandpass", fs=FS, output="sos")
        src_p = sosfiltfilt(sos, rng.standard_normal(total))
        src_s = sosfiltfilt(sos, rng.standard_normal(total)) * (10 ** (rel_db / 20))

        mic_sig = np.zeros((total, CHANNELS))
        for m in range(CHANNELS):
            dp = np.linalg.norm(np.asarray(p_xyz) - MIC_POSITIONS[:, m])
            ds = np.linalg.norm(np.asarray(s_xyz) - MIC_POSITIONS[:, m])
            mic_sig[:, m] += _fft_delay(src_p, dp / SOUND_SPEED * FS)
            mic_sig[:, m] += _fft_delay(src_s, ds / SOUND_SPEED * FS)
        mic_sig += rng.standard_normal(mic_sig.shape) * 0.02 * np.std(mic_sig)

        crop = mic_sig[GUARD:GUARD + N]
        scale = 100.0 / (np.max(np.abs(crop)) + 1e-9)
        audio_i16 = np.clip(crop * scale, -32768, 32767).astype(np.int16)
        chunk = audio_i16.tobytes()

        sm_pt = pytorch_api.generate([chunk])
        sm_ob = onebit_api.generate([chunk])

        pt_ratio = sm_pt[exp_s[0], exp_s[1]] / max(sm_pt.max(), 1e-9)
        ob_ratio = sm_ob[exp_s[0], exp_s[1]] / max(sm_ob.max(), 1e-9)

        # "found as its own local max within 2px" -- a weak proxy for resolvability
        def local_peak_ok(sm, exp_px, radius=2):
            r0, r1 = max(0, exp_px[0]-radius), exp_px[0]+radius+1
            c0, c1 = max(0, exp_px[1]-radius), exp_px[1]+radius+1
            patch = sm[r0:r1, c0:c1]
            local_max = patch.max() == sm[exp_px[0], exp_px[1]]
            return bool(local_max and sm[exp_px[0], exp_px[1]] > 0)

        print(f"{rel_db:>10} {pt_ratio:>14.3f} {ob_ratio:>18.3f} "
              f"{str(local_peak_ok(sm_pt, exp_s)):>15} {str(local_peak_ok(sm_ob, exp_s)):>17}")


if __name__ == "__main__":
    main()
    two_source_test()
