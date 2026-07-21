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

--- second-run additions: sharpness diagnostics ---

Added for the loss-function ablation (see CONTEXT.md): the owner observed the
old model's predictions are noticeably more "diffuse" than the sharp
single-peak input/target -- the classic regression-to-the-mean symptom of
training a peaky spatial target with plain MSE. `to_prob_dist`/`peak_mass`/
`entropy` quantify this directly, independent of peak *location* (peak_dist/
PSR already cover that). They're deliberately location-blind: a model could
have perfect peak_dist and still be diffuse (a wide, low blob that happens to
be centered on the right cell), which is exactly the failure mode these are
meant to catch.

Because the three loss conditions being compared (MSE, BCE: sigmoid output in
[0,1] per pixel, not normalized; KL: raw logits, unnormalized, can be
negative) have different native output scales, `to_prob_dist` normalizes ANY
of them into a proper probability distribution (sums to 1 over the map) before
`peak_mass`/`entropy` are computed, so the three conditions are comparable on
the same footing.
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


def to_prob_dist(x: np.ndarray, from_logits: bool = False) -> np.ndarray:
    """x: (...,H,W). Returns a proper probability distribution over the last
    two axes (sums to 1, same shape as x).
    from_logits=True: applies softmax (for raw/unbounded model output, e.g.
      the KL loss condition's no-activation output).
    from_logits=False: clips to non-negative then normalizes by sum (for
      already-bounded [0,1]-ish output, e.g. sigmoid/MSE/BCE conditions, and
      for the exp(x-max) target which is already >=0)."""
    H, W = x.shape[-2:]
    flat = x.reshape(*x.shape[:-2], H * W).astype(np.float64)
    if from_logits:
        flat = flat - flat.max(axis=-1, keepdims=True)
        flat = np.exp(flat)
    else:
        flat = np.clip(flat, 0, None)
    flat = flat / np.clip(flat.sum(axis=-1, keepdims=True), 1e-12, None)
    return flat.reshape(x.shape)


def peak_mass(p: np.ndarray) -> np.ndarray:
    """p: (...,H,W), already a probability distribution (see to_prob_dist).
    Returns (...) the probability mass at the argmax cell -- a simple
    sharpness/confidence proxy. Higher = more concentrated/confident;
    1/(H*W) would be a perfectly uniform (maximally diffuse) map."""
    H, W = p.shape[-2:]
    return p.reshape(*p.shape[:-2], H * W).max(axis=-1)


def entropy(p: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    """p: (...,H,W) probability distribution. Shannon entropy in nats over
    the last two axes. Lower = more concentrated/sharp; log(H*W) would be
    the uniform-distribution maximum."""
    H, W = p.shape[-2:]
    flat = p.reshape(*p.shape[:-2], H * W)
    return -(flat * np.log(flat + eps)).sum(axis=-1)
