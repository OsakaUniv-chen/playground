"""1-bit ("bit-shift & XOR") acoustic-camera sound-map generator. CPU only.

    api = OneBitSoundMapAPI()
    sm = api.generate([raw_bytes_of_audio_msg, ...])   # (64,64) float in [0,160]

Implements the 4-step PoC handed down by the supervisor, literally:

  1. Prep (`__init__` / `_prepare_lut`): for every virtual grid point and every
     mic, the propagation-time difference relative to mic 0 is computed and
     rounded to an integer *sample* count -> `self._delay_samples`, the LUT.
  2. Preprocess (`_binarize`): band-pass the 16ch window to 2000-8000 Hz (same
     band as the FFT beamformer in video-generator/beamform_soundmap.py, for a
     fair comparison), zero-phase (sosfiltfilt) so relative timing between
     channels is not distorted, then keep only the sign -> a 1-bit/channel
     signal.
  3. Correlate (`_xor_correlate`): for each grid point and every one of the
     C(16,2)=120 mic pairs (i,j), shift mic j by that pair's LUT delay
     relative to mic i and XOR against it (SRP-PHAT-style full pairwise
     correlation, not just the 15 pairs relative to a single reference mic
     -- see "sharper but shakier" in README.md for why); the mean match rate
     (XOR==0) over all 120 pairs is the "agreement" score for that grid point.
  4. Map (`generate`): the per-grid-point scores are assembled into the same
     merged polar grid / Delaunay interpolation used by the FFT beamformer, so
     the two generators' 64x64 outputs are directly overlayable.

No FFT, no float multiply-accumulate, no torch: steps 2-3 are pure integer /
bitwise numpy ops (band-pass filtering is the only floating-point step, and it
is a one-off per call, not per grid point). This is deliberately how real
FPGA-class 1-bit acoustic cameras work: sign-bit signals packed 64/word,
integer sample shifts from a precomputed LUT, and word-level XOR+popcount
(`_popcount64`, `_pack_bits`) instead of a per-sample compare -- see
"putting the CPU speed back" in README.md for why that packing isn't
optional: element-per-byte XOR was measured 6x slower once this went from 15
to 120 mic pairs, defeating the entire point of a CPU-only architecture.

The mic array spans ~13cm, so the true delay range between any two mics is
only a few dozen samples at 44.1kHz. `_prepare_lut` exploits this: instead of
gathering a (n_grid, N) shifted array per pair, it gathers one shifted copy
per *unique* delay-difference value (tens, not hundreds) and broadcasts the
resulting match counts back out to every grid point that shares that value --
and since every sample-domain array here is now bit-packed 64/word
(`_pack_bits`), each of those gathers moves ~64x fewer elements than a
per-sample one would. This is also how the hardware actually does it -- a
small fixed bank of shift registers, reused across many steering directions.

See README.md in this folder for the precision-vs-speed discussion (how this
compares to the frequency-domain FFT beamformer in ../video-generator/).
"""
from __future__ import annotations

import warnings

import cv2
import numpy as np
from scipy.signal import butter, sosfiltfilt
from scipy.spatial import Delaunay

# Same UMA-16 geometry as ../video-generator/beamform_soundmap.py (fair comparison).
_POPCOUNT_M1 = np.uint64(0x5555555555555555)
_POPCOUNT_M2 = np.uint64(0x3333333333333333)
_POPCOUNT_M4 = np.uint64(0x0f0f0f0f0f0f0f0f)
_POPCOUNT_H01 = np.uint64(0x0101010101010101)


def _popcount64(x):
    """Vectorized bit-population-count of a uint64 array (SWAR trick): counts
    set bits per element in ~5 elementwise ops instead of a per-sample loop --
    the actual "why 1-bit is CPU-cheap" ingredient this PoC was missing (see
    README's "putting the CPU speed back" section)."""
    x = x - ((x >> np.uint64(1)) & _POPCOUNT_M1)
    x = (x & _POPCOUNT_M2) + ((x >> np.uint64(2)) & _POPCOUNT_M2)
    x = (x + (x >> np.uint64(4))) & _POPCOUNT_M4
    return (x * _POPCOUNT_H01) >> np.uint64(56)


MIC_POSITIONS = np.array([
    (0.021, -0.063, 0.0), (0.063, -0.063, 0.0), (0.021, -0.021, 0.0), (0.063, -0.021, 0.0),
    (0.021, 0.021, 0.0), (0.063, 0.021, 0.0), (0.021, 0.063, 0.0), (0.063, 0.063, 0.0),
    (-0.063, 0.063, 0.0), (-0.021, 0.063, 0.0), (-0.063, 0.021, 0.0), (-0.021, 0.021, 0.0),
    (-0.063, -0.021, 0.0), (-0.021, -0.021, 0.0), (-0.063, -0.063, 0.0), (-0.021, -0.063, 0.0),
], dtype=np.float64).T  # (3, 16)


class OneBitSoundMapGenerator:
    def __init__(
        self,
        fs=44100,
        channels=16,
        sm_size=64,
        plot_size=1080,
        device="cpu",
        filter_order=4,
        min_samples=256,
    ):
        if device != "cpu":
            warnings.warn(
                "OneBitSoundMapGenerator is CPU-only by design (bit ops, no GPU path); "
                f"ignoring device={device!r}.", RuntimeWarning, stacklevel=2,
            )
        self.device = "cpu"

        self.fs = fs
        self.channels = channels
        self.sm_size = sm_size
        self.plot_size = plot_size

        self.band_low = 2000
        self.band_high = 8000
        self.distance = 1.5
        self.sound_speed = 345
        self.filter_order = filter_order
        self.min_samples = min_samples

        self.mpos = self._mic_positions()
        self.gpos = self._create_merged_grid()
        self.n_grid = self.gpos.shape[1]
        self.r0, self.rm = self._compute_distances(self.gpos, self.mpos)

        self._sos = butter(
            self.filter_order, [self.band_low, self.band_high],
            btype="bandpass", fs=self.fs, output="sos",
        )

        self._prepare_lut()
        self._prepare_interpolator()

    # -- geometry (identical to beamform_soundmap.py, for a directly comparable grid) --
    def _mic_positions(self):
        if MIC_POSITIONS.shape != (3, self.channels):
            raise ValueError(f"Expected microphone geometry with shape (3, {self.channels}), got {MIC_POSITIONS.shape}")
        return MIC_POSITIONS

    @staticmethod
    def _rect_grid(x_min, x_max, y_min, y_max, increment, z):
        i = abs(increment)
        nxsteps = int(round((abs(x_max - x_min) + i) / i)) if i != 0 else 1
        nysteps = int(round((abs(y_max - y_min) + i) / i)) if i != 0 else 1
        bpos = np.mgrid[x_min:x_max : nxsteps * 1j, y_min:y_max : nysteps * 1j, z : z + 0.1]
        bpos.resize((3, nxsteps * nysteps))
        return bpos

    def _create_merged_grid(self):
        grids = [
            self._rect_grid(-5.0, 5.0, -5.0, 5.0, 1.0, 1.5),
            self._rect_grid(-2.5, 2.5, -2.5, 2.5, 0.5, 1.5),
            self._rect_grid(-1.25, 1.25, -1.25, 1.25, 0.1, 1.5),
        ]
        return np.unique(np.append(np.append(grids[0], grids[1], axis=1), grids[2], axis=1), axis=1)

    @staticmethod
    def _compute_distances(gpos, mpos):
        r0 = np.sqrt(np.sum(gpos * gpos, axis=0))
        diff = gpos.T[:, None, :] - mpos.T[None, :, :]
        rm = np.sqrt(np.sum(diff * diff, axis=2))
        return r0.astype(np.float64), rm.astype(np.float64)

    def _create_uv(self):
        pixel_size = 1080
        r_max = np.pi / 2
        x = self.gpos[0, :]
        y = self.gpos[1, :]

        r = np.arctan(np.sqrt(x**2 + y**2) / self.distance)
        r_normalized = (r / r_max) * (pixel_size / 2)
        theta = np.arctan2(y, x)

        u = (pixel_size / 2 + r_normalized * np.cos(theta)).astype(int)
        v = (pixel_size / 2 + r_normalized * np.sin(theta)).astype(int)
        return u, v

    def _prepare_interpolator(self):
        v, u = self._create_uv()
        v = 1080 - v
        cx, cy = 540, 540
        u = 2 * cx - u
        v = 2 * cy - v
        points = np.array([u, v]).T.astype(np.float64)

        coords = (np.arange(self.sm_size, dtype=np.float64) + 0.5) * (1080.0 / self.sm_size) - 0.5
        grid_x, grid_y = np.meshgrid(coords, coords, indexing="ij")
        targets = np.column_stack((grid_x.ravel(), grid_y.ravel()))

        tri = Delaunay(points)
        simplex = tri.find_simplex(targets)
        valid = simplex >= 0
        safe_simplex = np.where(valid, simplex, 0)
        transform = tri.transform[safe_simplex]
        delta = targets - transform[:, 2]
        bary = np.einsum("nij,nj->ni", transform[:, :2], delta)
        weights = np.column_stack((bary, 1.0 - bary.sum(axis=1)))
        vertices = tri.simplices[safe_simplex]
        weights[~valid] = 0.0
        vertices[~valid] = 0

        self._interp_vertices = vertices.astype(np.int64)
        self._interp_weights = weights.astype(np.float64)

    def _interpolate_to_soundmap(self, values):
        transformed_values = np.clip(values, 0, None)
        sampled = np.sum(transformed_values[self._interp_vertices] * self._interp_weights, axis=1)
        final_lm = sampled.reshape(self.sm_size, self.sm_size)
        return np.clip(final_lm, 0, 160)

    # -- step 1: LUT (arrival-time-difference-in-samples per grid point x mic) --
    def _prepare_lut(self):
        delay_time = (self.rm - self.rm[:, :1]) / self.sound_speed          # (n_grid, channels), s
        delay_samples = np.round(delay_time * self.fs).astype(np.int64)    # LUT, relative to mic0
        self._delay_samples = delay_samples

        # All C(channels,2) mic pairs (SRP-PHAT-style), not just the `channels-1`
        # pairs relative to mic0: using every pair means every grid point's score
        # averages over ~8x more independent bit-agreement observations, which
        # lowers its variance and reduces the boundary-jitter this was built to
        # fix (see README's "sharper but shakier" section) -- still pure bitwise
        # ops, no bit ever needs more than an integer sample shift + XOR.
        #
        # match(i,j) for grid g only depends on the RELATIVE delay between mic i
        # and mic j at g (delay_samples[g,j] - delay_samples[g,i]), so the same
        # "dedupe to unique shifts, reduce over time first, broadcast to grid
        # points last" trick from the mic0-relative version still applies --
        # just once per pair instead of once per mic.
        self._pairs = []   # [(i, j, unique_diffs, grid->unique_diffs index), ...]
        for i in range(self.channels):
            for j in range(i + 1, self.channels):
                diff = delay_samples[:, j] - delay_samples[:, i]
                u, inv = np.unique(diff, return_inverse=True)
                self._pairs.append((i, j, u, inv.ravel()))

    # -- step 2: BPF + 1-bit (sign) quantization --
    def _audio_queue_to_array(self, audio_queue):
        chunks = [np.frombuffer(a, dtype=np.int16).reshape(-1, self.channels) for a in audio_queue]
        if not chunks:
            return np.zeros((0, self.channels), dtype=np.int16)
        return np.vstack(chunks)

    def _binarize(self, audio):
        filtered = sosfiltfilt(self._sos, audio.astype(np.float64), axis=0)
        return (filtered >= 0).astype(np.uint8)   # (N, channels), 1 bit/sample

    # -- bit-packing: 64 samples/word, so XOR+popcount does 64 samples per op --
    # WORD_PAD covers the word-level neighbor a shift may reach into; the ~13cm
    # array only produces delays up to ~25 samples (well under 64), so a shift's
    # word-offset q = shift//64 is always 0 in practice -- WORD_PAD=2 is a
    # deliberately generous margin, not a tuned minimum.
    WORD_PAD = 2

    def _pack_channel(self, bits_1d, n_words):
        """(N,) uint8 0/1 -> (n_words,) uint64, bit i of word w = sample 64w+i.
        Zero-pads the tail past N; see docstring note on that below."""
        padded = np.zeros(n_words * 64, dtype=np.uint8)
        padded[:bits_1d.shape[0]] = bits_1d
        weights = np.uint64(1) << np.arange(64, dtype=np.uint64)
        return (padded.reshape(n_words, 64).astype(np.uint64) * weights[None, :]).sum(
            axis=1, dtype=np.uint64)

    def _pack_bits(self, bits):
        """(N, channels) uint8 -> (channels, n_words + 2*WORD_PAD) uint64,
        zero-padded on both sides so any in-range shift is a plain slice (no
        bounds branching needed per pair)."""
        n = bits.shape[0]
        n_words = -(-n // 64)   # ceil(n / 64)
        pad = self.WORD_PAD
        packed = np.zeros((self.channels, n_words + 2 * pad), dtype=np.uint64)
        for m in range(self.channels):
            packed[m, pad:pad + n_words] = self._pack_channel(bits[:, m], n_words)
        return packed, n_words

    # -- step 3: bit-shift (LUT) & XOR+popcount correlation, over every mic pair --
    def _xor_correlate(self, bits):
        n = bits.shape[0]
        packed, n_words = self._pack_bits(bits)   # (channels, n_words + 2*pad)
        pad = self.WORD_PAD
        word_idx = np.arange(n_words)

        match_total = np.zeros(self.n_grid, dtype=np.int64)
        for i, j, u, inv in self._pairs:
            ref_words = packed[i, pad:pad + n_words]        # (n_words,) unshifted reference

            q, r = np.divmod(u, 64)                          # (k,) word offset, bit offset in [0,64)
            start = pad + q                                  # (k,)
            idx = start[:, None] + word_idx[None, :]          # (k, n_words): word containing bit r..
            hi = packed[j][idx] >> r[:, None].astype(np.uint64)
            shift_lo = np.where(r == 0, np.uint64(1), (64 - r).astype(np.uint64))
            lo = packed[j][idx + 1] << shift_lo[:, None]      # unused when r==0, but must not shift by 64
            shifted = np.where(r[:, None] == 0, packed[j][idx], hi | lo)   # (k, n_words)

            match_words = ~(ref_words[None, :] ^ shifted)     # (k, n_words): set bits = agreeing samples
            match_per_shift = _popcount64(match_words).sum(axis=1, dtype=np.int64)   # (k,)

            match_total += match_per_shift[inv]

        # denominator = n_words*64 (not n) bit-positions compared per pair: the
        # last word's <=63 bits of zero-tail padding (from packing N up to a
        # multiple of 64) are a fixed, uniform-across-grid-points dilution of
        # well under 0.2% of a 20000-sample window -- negligible relative to
        # everything else here, and documented rather than specially masked.
        valid_per_pair = n_words * 64
        return match_total / (valid_per_pair * len(self._pairs))   # (n_grid,) in [0, 1], 0.5 = chance level

    # -- step 4: score -> 2D map --
    # GAIN calibrated against real 16ch recordings (G11_game4_DoA), not synthetic
    # data: with real reverberation/noise the achievable peak match rate is only
    # ~0.6-0.7, nowhere near the idealized 1.0 ceiling, so a naive "0.5->0,
    # 1.0->160" mapping wastes almost all its range on scores that never occur
    # and crushes the whole map to a near-invisible single pixel once the shared
    # exp(sm-sm.max()) display/label transform (labeling.transform_sm) is
    # applied. GAIN=50 was picked so this generator's fraction of "visible"
    # (>0.05 after that transform) pixels lands in the same ballpark as the FFT
    # beamformer's on real ticks (~0.03-0.04 both, re-checked after switching to
    # all-pairs correlation, which changed the raw score distribution slightly
    # from the 15-pair scheme's -- see compare_video.py / README.md).
    GAIN = 50.0

    def _score_to_db(self, score):
        # Purely a display/label-pipeline convention -- unlike the FFT
        # beamformer's figure this is NOT a physical sound-pressure-level
        # estimate, just something exp(sm-sm.max())-shaped comparably.
        return np.clip(score - 0.5, 0.0, None) * self.GAIN

    def generate(self, audio_queue):
        audio = self._audio_queue_to_array(audio_queue)
        if audio.shape[0] < self.min_samples:
            return np.zeros((self.sm_size, self.sm_size))
        bits = self._binarize(audio)
        score = self._xor_correlate(bits)
        db_like = self._score_to_db(score)
        return self._interpolate_to_soundmap(db_like)

    def visualize_sm(self, final_Lm, method="B"):
        plot_sm = cv2.resize(final_Lm, (self.plot_size, self.plot_size), interpolation=cv2.INTER_LINEAR)

        x = plot_sm.astype(np.float64)
        if method == "A":
            filled_sm = x / 160
        elif method == "B":
            filled_sm = np.exp(x - x.max())
        elif method == "C":
            filled_sm = np.zeros_like(x) if x.max() == x.min() else np.exp((x - x.max()) / (x.max() - x.min()))
        elif method == "D":
            x = np.exp(x)
            filled_sm = (x - x.min()) / (x.max() - x.min())
        elif method == "E":
            x = np.exp(x)
            filled_sm = (x - 1) / (np.exp(160) - 1)
        else:
            raise ValueError(f"Unknown visualization method: {method}")

        filled_sm = (filled_sm * 255).astype(np.uint8)
        return np.stack([np.zeros_like(filled_sm), filled_sm, filled_sm], axis=-1)


class OneBitSoundMapAPI:
    def __init__(self, fs: int = 44100, channels: int = 16,
                 blocksize: int = 4096, sm_size: int = 64, plot_size: int = 1080,
                 device: str = "cpu", precision: str = "float32"):
        # blocksize/precision accepted-but-unused: kept only so this class is a
        # drop-in for beamform_soundmap.SoundMapAPI's constructor signature.
        self._gen = OneBitSoundMapGenerator(
            fs=fs, channels=channels, sm_size=sm_size, plot_size=plot_size, device=device,
        )
        self.sm_size = sm_size
        self.channels = channels
        self.device = self._gen.device

    def generate(self, audio_chunks) -> np.ndarray:
        """audio_chunks: iterable of raw int16/16ch byte payloads.
        Returns (sm_size, sm_size) float in [0,160]."""
        return self._gen.generate(list(audio_chunks))
