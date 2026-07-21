"""Data augmentation for sixth-run's ablation #3 (see CONTEXT.md fifth-run
section: exp4's real training applies this on every batch, third/fourth/
fifth-run never did). Two augmentation schemes:

  - flip_and_crop (augment_batch): ported directly from access-model-train/
    utils_all_load.py's FlipAndRandomCrop_step1/step2 -- exp4's real
    augmentation. Random horizontal flip (50%) + random-scale/aspect crop
    (area 95-100% of original, aspect ratio 0.9-1.11x) resized back to the
    original size, applied with the SAME flip/crop parameters to both input
    and target so their spatial correspondence (a peak's position) isn't
    broken by the augmentation.

  - noise_and_ratio_jitter (augment_batch_noise_ratio): a new scheme, tried
    as an alternative to flip_and_crop (owner's request, 2026-07-14) since
    it regularizes via a completely different mechanism -- signal/channel
    perturbation instead of spatial geometry. Deliberately does NOT use
    rotation: the soundmap<->camera pixel mapping is calibrated to the
    mic array's fixed real-world geometry (see soundmap.py's
    _prepare_interpolator), so rotating the frame would desync that mapping
    and manufacture physically inconsistent training pairs -- flip is safe
    (mirror symmetry preserves the geometry), rotation is not.
    Two pieces, both applied to the INPUT only (never the target -- the
    target must stay the clean ground truth to predict):
      1. sm_ratio jitter: PSSPWindowDataset bakes a FIXED sm_ratio=0.5 blend
         into input channel 0 at dataset-build time. This re-derives the
         pure (unblended) soundmap from that known ratio, then re-blends
         with a fresh ratio drawn per batch from ratio_range -- teaches the
         model to not overfit to exactly-0.5 channel weighting.
      2. Gaussian noise on both channels post-reblend, modeling sensor
         noise (mic array / camera).
"""
from __future__ import annotations

import random

import torch
import torch.nn.functional as F


def flip_and_crop_params(shape: tuple[int, ...], scale=(0.95, 1.0), ratio=(9. / 10., 10. / 9.), try_n=20):
    """shape: (..., H, W). Returns (flip_flag, (x0, x1, y0, y1)) -- same
    random draw shared between input and target so they stay aligned."""
    H, W = shape[-2], shape[-1]
    flip_flag = random.random() > 0.5

    area = H * W
    for _ in range(try_n):
        target_area = random.uniform(*scale) * area
        aspect_ratio = random.uniform(*ratio)
        new_w = int(round((target_area * aspect_ratio) ** 0.5))
        new_h = int(round((target_area / aspect_ratio) ** 0.5))
        if new_w <= W and new_h <= H:
            x0 = random.randint(0, W - new_w)
            y0 = random.randint(0, H - new_h)
            return flip_flag, (x0, x0 + new_w, y0, y0 + new_h)
    # fallback: no crop (shouldn't normally trigger given scale close to 1.0)
    return flip_flag, (0, W, 0, H)


def apply_flip_and_crop(batch: torch.Tensor, flip_flag: bool, coords: tuple[int, int, int, int]) -> torch.Tensor:
    """batch: (B, T, C, H, W) or (B, C, H, W)."""
    reshape_back = batch.ndim == 5
    if reshape_back:
        B, T, C, H, W = batch.shape
        batch = batch.reshape(B * T, C, H, W)
    else:
        B, C, H, W = batch.shape

    if flip_flag:
        batch = batch.flip(dims=[-1])

    x0, x1, y0, y1 = coords
    cropped = batch[:, :, y0:y1, x0:x1]
    resized = F.interpolate(cropped, size=(H, W), mode="bilinear", align_corners=False)

    if reshape_back:
        resized = resized.reshape(B, T, C, H, W)
    return resized


def augment_batch(x: torch.Tensor, y: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """x: (B, clip_len, 2, H, W), y: (B, pred_len, 1, H, W) -- same flip/crop
    draw applied to both, matching exp4's training loop exactly."""
    flip_flag, coords = flip_and_crop_params(x.shape)
    return apply_flip_and_crop(x, flip_flag, coords), apply_flip_and_crop(y, flip_flag, coords)


def augment_batch_noise_ratio(x: torch.Tensor, y: torch.Tensor, base_ratio: float = 0.5,
                               ratio_range: tuple[float, float] = (0.3, 0.7),
                               noise_std: float = 0.03) -> tuple[torch.Tensor, torch.Tensor]:
    """x: (B, clip_len, 2, H, W) -- ch0 = base_ratio-blended exp(sm), ch1 =
    gray/255 (see dataset.py). y is returned unchanged -- both pieces of
    this augmentation only touch the input, never the (clean) target.
    See module docstring for the design reasoning."""
    blend, gray = x[:, :, 0], x[:, :, 1]           # each (B, clip_len, H, W)
    pure_sm = (blend - (1.0 - base_ratio) * gray) / base_ratio

    new_ratio = random.uniform(*ratio_range)
    new_blend = new_ratio * pure_sm + (1.0 - new_ratio) * gray
    x_aug = torch.stack([new_blend, gray], dim=2)  # (B, clip_len, 2, H, W)

    x_aug = x_aug + torch.randn_like(x_aug) * noise_std
    return x_aug, y
