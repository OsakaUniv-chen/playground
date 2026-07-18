"""PSSP evaluation metrics, incl. PSR_k@n from the owner's own paper:

Chen et al., "A Feasibility Study With In-the-Wild Data in Human Interaction
Settings: Acoustic-Visual Fusion for Predictive Sound Source Positioning,"
IEEE Access, vol. 13, 2025 (paper5/publications/...).

Eq (7): a k x k local mean filter over a sound map, edge-aware (near a
border, divides by the count of in-bounds neighbors, not always k^2 -- this
is NOT the same as zero-padded convolution, which would under-weight edges).
Applied before peak detection to make the argmax robust to single-pixel
noise.

Eq (8): PSR_k@n = the fraction of samples whose (k-filtered) peak distance
D_peak is below threshold n -- a "success rate", not a mean distance. The
paper's headline setting is PSR_k5@5 (k=5 ~ a face's pixel scale on their
64x64 maps, n=5 grid cells). "Aggregate PSR" = mean PSR_k5@5 across all
prediction steps t+1..t+pred_len.

k=1 (no filtering) recovers the plain/raw peak distance used elsewhere in
this project (e.g. train.py's peak_dist_batch, access-model/smoke_test.py).
"""
from __future__ import annotations

import numpy as np


def local_mean_filter(sm: np.ndarray, k: int) -> np.ndarray:
    """k x k local mean filter over the last two axes, edge-aware. sm: (...,H,W).
    No-op for k=1 (returns sm unchanged)."""
    if k == 1:
        return sm
    if k % 2 == 0:
        raise ValueError(f"k must be odd, got {k}")

    pad = k // 2
    pad_width = [(0, 0)] * (sm.ndim - 2) + [(pad, pad), (pad, pad)]
    padded = np.pad(sm, pad_width, mode="constant")
    ones = np.pad(np.ones_like(sm), pad_width, mode="constant")
    H, W = sm.shape[-2:]

    def box_sum(a):
        c = np.cumsum(np.cumsum(a, axis=-1), axis=-2)
        c = np.pad(c, [(0, 0)] * (a.ndim - 2) + [(1, 0), (1, 0)], mode="constant")
        return c[..., k:, k:] - c[..., :H, k:] - c[..., k:, :W] + c[..., :H, :W]

    return box_sum(padded) / box_sum(ones)


def peak_coords(sm: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """sm: (...,H,W). Returns (row, col) of argmax, each shape (...)."""
    H, W = sm.shape[-2:]
    flat = sm.reshape(*sm.shape[:-2], H * W)
    idx = np.argmax(flat, axis=-1)
    return idx // W, idx % W


def peak_dist(pred: np.ndarray, target: np.ndarray, k: int = 1) -> np.ndarray:
    """pred/target: (...,H,W). Returns (...) Euclidean argmax displacement in
    grid cells, after a k x k local mean filter (k=1 = raw/unfiltered)."""
    pr, pc = peak_coords(local_mean_filter(pred, k))
    tr, tc = peak_coords(local_mean_filter(target, k))
    return np.hypot(pr - tr, pc - tc)


def psr_at(pred: np.ndarray, target: np.ndarray, k: int, n: float, sample_axis: int = 0) -> np.ndarray:
    """PSR_k@n. pred/target: (N,...,H,W) with N = sample_axis. Returns the
    fraction of samples with peak_dist < n, reduced over sample_axis (so for
    pred shape (N,T,H,W) this returns shape (T,) -- one PSR per prediction
    step)."""
    d = peak_dist(pred, target, k=k)
    return (d < n).mean(axis=sample_axis)
