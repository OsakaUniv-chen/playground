"""Torch-based frequency-domain beamforming sound-map generator.

    api = SoundMapAPI(device="cpu")
    sm = api.generate([raw_bytes_of_audio_msg, ...])   # (64,64) float in [0,160]

`audio_chunks` = the raw uint8 payloads of consecutive /audio/audio_raw messages
(int16, 16ch interleaved), i.e. exactly what bag_io.decode_audio returns.

Conventional (delay-and-sum) frequency-domain beamforming: a direct linear sum
of FFT power over a fixed 2000-8000 Hz band, steered across a merged polar grid
in front of the 16-mic UMA-16 array, then interpolated onto a 64x64 map. This
was validated in generator-compare/ as a harmless, ~7-8x faster reimplementation
of the older acoular-based generator (BeamformerBase.synthetic(f=2000, num=3)) —
raw maps correlate at r~0.99999 and downstream 4-label decisions agree almost
perfectly.

No acoular / xml file needed: the UMA-16 mic geometry (from
acoular's minidsp_uma-16.xml, corrected in x after acoular 24.03 per GitHub
issue #63) is embedded below as MIC_POSITIONS.
"""
from __future__ import annotations

import warnings

import cv2
import numpy as np
import torch
from scipy.signal.windows import blackmanharris
from scipy.spatial import Delaunay

# UMA-16 mic positions (x, y, z) in meters, mic order matches /audio/audio_raw channels.
MIC_POSITIONS = np.array([
    (0.021, -0.063, 0.0), (0.063, -0.063, 0.0), (0.021, -0.021, 0.0), (0.063, -0.021, 0.0),
    (0.021, 0.021, 0.0), (0.063, 0.021, 0.0), (0.021, 0.063, 0.0), (0.063, 0.063, 0.0),
    (-0.063, 0.063, 0.0), (-0.021, 0.063, 0.0), (-0.063, 0.021, 0.0), (-0.021, 0.021, 0.0),
    (-0.063, -0.021, 0.0), (-0.021, -0.021, 0.0), (-0.063, -0.063, 0.0), (-0.021, -0.063, 0.0),
], dtype=np.float64).T  # (3, 16)


class SoundMapGenerator:
    def __init__(
        self,
        fs,
        channels,
        blocksize,
        sm_size,
        plot_size=1080,
        device="cuda",
        precision="float32",
        beamform_algorithm="auto",
    ):
        self.fs = fs
        self.channels = channels
        self.blocksize = blocksize
        self.sm_size = sm_size
        self.plot_size = plot_size

        self.band_low = 2000
        self.band_high = 8000
        self.r_diag = True
        self.distance = 1.5
        self.sound_speed = 345
        self.overlap = 2.95
        self.gain_factor = 10 ** (30 / 20)

        self.device = self._select_device(device)
        self.precision = precision
        self.dtype, self.complex_dtype, self.np_dtype = self._select_precision(precision)
        self.beamform_algorithm = self._select_beamform_algorithm(beamform_algorithm)

        self.mpos = self._mic_positions()
        self.gpos = self._create_merged_grid()
        self.r0, self.rm = self._compute_distances(self.gpos, self.mpos)
        self.freqs = np.abs(np.fft.fftfreq(self.blocksize, 1.0 / self.fs)[: self.blocksize // 2 + 1])
        self.freq_indices = np.arange(
            np.searchsorted(self.freqs, self.band_low),
            np.searchsorted(self.freqs, self.band_high),
            dtype=np.int64,
        )
        self.window = blackmanharris(self.blocksize).astype(np.float64)
        self.window_weight = float(np.dot(self.window, self.window))

        self._prepare_torch_constants()
        self._prepare_interpolator()

    def _select_device(self, requested):
        if requested == "cuda" and not torch.cuda.is_available():
            warnings.warn("CUDA requested for SoundMapGenerator, but it is not available. Falling back to CPU.", RuntimeWarning, stacklevel=2)
            return torch.device("cpu")
        return torch.device(requested)

    @staticmethod
    def _select_precision(precision):
        if precision == "float64":
            return torch.float64, torch.complex128, np.float64
        if precision == "float32":
            return torch.float32, torch.complex64, np.float32
        raise ValueError(f"Unsupported precision: {precision}. Use 'float64' or 'float32'.")

    def _select_beamform_algorithm(self, beamform_algorithm):
        if beamform_algorithm == "auto":
            return "direct" if self.device.type == "cuda" else "csm"
        if beamform_algorithm in {"direct", "csm"}:
            return beamform_algorithm
        raise ValueError("beamform_algorithm must be 'auto', 'direct', or 'csm'.")

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

    def _prepare_torch_constants(self):
        rm = torch.as_tensor(self.rm, dtype=self.dtype, device=self.device)
        r0 = torch.as_tensor(self.r0, dtype=self.dtype, device=self.device)
        freqs = torch.as_tensor(self.freqs[self.freq_indices], dtype=self.dtype, device=self.device)
        wave_numbers = 2 * torch.pi * freqs / self.sound_speed

        phase = wave_numbers[:, None, None] * rm[None, :, :]
        steer = torch.exp(-1j * phase).to(self.complex_dtype) / rm[None, :, :].to(self.complex_dtype)
        help_normalize = torch.sum(1.0 / (rm * rm), dim=1)
        self._steer = steer
        self._steer_conj = steer.conj()
        self._steer_abs2 = steer.real * steer.real + steer.imag * steer.imag
        self._normalizer = ((r0 * help_normalize) ** 2)[None, :]
        self._signal_loss_norm = self.channels / (self.channels - 1)
        self._window = torch.as_tensor(self.window[:, None], dtype=self.dtype, device=self.device)
        self._freq_indices = torch.as_tensor(self.freq_indices, dtype=torch.long, device=self.device)

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

    def _audio_queue_to_array(self, audio_queue):
        chunks = [np.frombuffer(a, dtype=np.int16).reshape(-1, self.channels) for a in audio_queue]
        if not chunks:
            return np.zeros((0, self.channels), dtype=np.int16)
        audio = np.vstack(chunks)
        return (audio * self.gain_factor).astype(np.int16)

    def _overlap_blocks(self, audio):
        bs = self.blocksize
        if len(audio) < bs:
            return np.zeros((0, bs, self.channels), dtype=self.np_dtype)

        temp = np.empty((2 * bs, self.channels), dtype=self.np_dtype)
        pos = float(bs)
        posinc = bs / self.overlap
        blocks = []
        for start in range(0, len(audio), bs):
            data_block = audio[start : start + bs].astype(self.np_dtype, copy=False)
            ns = data_block.shape[0]
            temp[bs : bs + ns] = data_block
            while pos + bs <= bs + ns:
                blocks.append(temp[int(pos) : int(pos + bs)].copy())
                pos += posinc
            temp[0:bs] = temp[bs:]
            pos -= bs
        if not blocks:
            return np.zeros((0, bs, self.channels), dtype=self.np_dtype)
        return np.stack(blocks, axis=0)

    def _fft_blocks(self, audio_np_array):
        blocks = self._overlap_blocks(audio_np_array)
        if blocks.shape[0] == 0 or self.freq_indices.size == 0:
            return None

        blocks_t = torch.as_tensor(blocks, dtype=self.dtype, device=self.device)
        ft = torch.fft.rfft(blocks_t * self._window[None, :, :], dim=1).to(self.complex_dtype)
        return ft.index_select(1, self._freq_indices)

    def _csm_scale(self, audio_np_array):
        num_blocks = self.overlap * audio_np_array.shape[0] / self.blocksize - self.overlap + 1
        return 2.0 / self.blocksize / self.window_weight / num_blocks

    def _beamform_power_csm(self, audio_np_array):
        ft = self._fft_blocks(audio_np_array)
        if ft is None:
            return np.zeros(self.gpos.shape[1], dtype=np.float64)

        csm = torch.einsum("bfi,bfj->fij", ft, ft.conj())
        csm = csm * self._csm_scale(audio_np_array)

        if self.r_diag:
            csm = csm.clone()
            idx = torch.arange(self.channels, device=self.device)
            csm[:, idx, idx] = 0

        power = torch.einsum("fgm,fmn,fgn->fg", self._steer_conj, csm, self._steer).real
        power = power / self._normalizer
        if self.r_diag:
            power = power * self._signal_loss_norm
            power = torch.clamp(power, min=0)

        summed = torch.sum(power, dim=0)
        return summed.detach().cpu().numpy()

    def _beamform_power_direct(self, audio_np_array):
        ft = self._fft_blocks(audio_np_array)
        if ft is None:
            return np.zeros(self.gpos.shape[1], dtype=np.float64)

        with torch.no_grad():
            steered = torch.einsum("bfm,fgm->bfg", ft, self._steer_conj)
            power = torch.sum(steered.real * steered.real + steered.imag * steered.imag, dim=0)

            if self.r_diag:
                ft_abs2 = ft.real * ft.real + ft.imag * ft.imag
                diag_power = torch.einsum("bfm,fgm->bfg", ft_abs2, self._steer_abs2)
                power = power - torch.sum(diag_power, dim=0)

            power = power * self._csm_scale(audio_np_array)
            power = power / self._normalizer
            if self.r_diag:
                power = power * self._signal_loss_norm
                power = torch.clamp(power, min=0)

            summed = torch.sum(power, dim=0)
        return summed.detach().cpu().numpy()

    def _beamform_power(self, audio_np_array):
        if self.beamform_algorithm == "direct":
            return self._beamform_power_direct(audio_np_array)
        return self._beamform_power_csm(audio_np_array)

    def _lp(self, power):
        return 10.0 * np.log10(np.clip(power / 4e-10, 1e-35, None))

    def _interpolate_to_soundmap(self, values):
        transformed_values = np.clip(values, 0, None)
        sampled = np.sum(transformed_values[self._interp_vertices] * self._interp_weights, axis=1)
        final_lm = sampled.reshape(self.sm_size, self.sm_size)
        return np.clip(final_lm, 0, 160)

    def generate(self, audio_queue):
        audio_np_array = self._audio_queue_to_array(audio_queue)
        power = self._beamform_power(audio_np_array)
        lm = self._lp(power)
        return self._interpolate_to_soundmap(lm)

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


class SoundMapAPI:
    def __init__(self, fs: int = 44100, channels: int = 16,
                 blocksize: int = 4096, sm_size: int = 64, plot_size: int = 1080,
                 device: str = "cpu", precision: str = "float32"):
        self._gen = SoundMapGenerator(
            fs=fs, channels=channels, blocksize=blocksize,
            sm_size=sm_size, plot_size=plot_size,
            device=device, precision=precision,
        )
        self.sm_size = sm_size
        self.channels = channels
        self.device = str(self._gen.device)

    def generate(self, audio_chunks) -> np.ndarray:
        """audio_chunks: iterable of raw int16/16ch byte payloads.
        Returns (sm_size, sm_size) float in [0,160]."""
        return self._gen.generate(list(audio_chunks))
