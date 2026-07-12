"""DMVFN (Dynamic Multi-scale Voxel Flow Network), adapted from Hu et al.,
"A Dynamic Multi-Scale Voxel Flow Network for Video Prediction" (CVPR 2023
highlight, arXiv:2303.09875). Read the actual paper before implementing
this (module docstring below records what it says, since the owner's
recollection of the input-frame count didn't match it and needed checking).

Faithful to the paper's core design in the ways that matter for this
comparison (owner confirmed 2026-07-11, after being shown the paper's
actual wording -- "The inputs of our video prediction model are only the
two consecutive frames"):
  - Only the last 2 frames are used as input, NOT a longer clip_len history
    -- multi-step prediction is autoregressive: the model's own last
    output is fed back in as the new "current frame" for the next step
    (see rollout() in train.py). This matches the paper exactly (Section
    3.1: "We concentrate on predicting Ĩ_{t+1} and iteratively predict
    future frames {Ĩ_{t+2}, Ĩ_{t+3}, ...} in a similar manner").
  - 9 cascaded MVFB (Multi-scale Voxel Flow Block) stages with decreasing
    scale factors [4,4,4,2,2,2,1,1,1] (paper Section 3.2 / Figure 2b),
    each refining a running (flow-to-cur, flow-to-prev, fusion-mask)
    estimate; the frame is synthesized at every stage by backward-warping
    the two input frames with the current flow estimate and blending with
    the fusion mask (paper Eq 1-3).
  - Trained with deep supervision across all 9 stages, later/finer stages
    weighted more (gamma=0.8, paper Eq 13) -- this file uses a simplified
    3-level L1 Laplacian-pyramid reconstruction loss per stage rather than
    the paper's exact pyramid formulation.

Deliberately simplified vs. the paper (owner confirmed, since this
comparison is about "does flow-warping beat direct regression for THIS
task", not a paper reproduction):
  - No Routing Module / dynamic per-sample block-skipping. That machinery
    exists purely for INFERENCE-time compute savings (skip blocks whose
    contribution isn't needed for a given sample); the paper's own ablation
    ("DMVFN w/o r", Table 1) shows near-identical accuracy without it,
    since routing only removes blocks, never adds capability. All 9 MVFB
    blocks always run here.
  - Each MVFB is a single conv path operating at 1/scale resolution
    (avg-pool down, conv stack, bilinear up), not the paper's separate
    motion-path/spatial-path two-branch design -- same "coarse-to-fine
    iterative refinement across a cascade of decreasing scales" idea,
    simpler channel bookkeeping, easier to get right without the original
    figure's exact resolution staging.
  - Operates on this project's existing 2-channel frame representation
    (sm_ratio-blended soundmap + gray, matching SimVP's input convention)
    instead of 3-channel RGB. Both channels are warped/blended at every
    stage -- not because we care about predicting future camera frames,
    but so a predicted frame is immediately valid input for the next
    rollout step without a "freeze the gray channel" special case. Only
    channel 0 (soundmap) is supervised during training; the warp itself
    naturally keeps outputs in [0,1] (a convex blend of already-[0,1]
    inputs), so unlike SimVP there's no need for a sigmoid output head.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

SCALES = (4, 4, 4, 2, 2, 2, 1, 1, 1)
GAMMA = 0.8


def backward_warp(img: torch.Tensor, flow: torch.Tensor) -> torch.Tensor:
    """img: (B,C,H,W). flow: (B,2,H,W) pixel displacement (dx,dy). Returns
    img re-sampled at (x+dx, y+dy) via bilinear grid_sample."""
    B, C, H, W = img.shape
    ys, xs = torch.meshgrid(
        torch.arange(H, device=img.device, dtype=img.dtype),
        torch.arange(W, device=img.device, dtype=img.dtype),
        indexing="ij",
    )
    grid = torch.stack([xs, ys], dim=0).unsqueeze(0).expand(B, -1, -1, -1)  # (B,2,H,W)
    sample = grid + flow
    sample_x = 2.0 * sample[:, 0] / max(W - 1, 1) - 1.0
    sample_y = 2.0 * sample[:, 1] / max(H - 1, 1) - 1.0
    sample_grid = torch.stack([sample_x, sample_y], dim=-1)  # (B,H,W,2)
    return F.grid_sample(img, sample_grid, mode="bilinear", padding_mode="border", align_corners=True)


class MVFB(nn.Module):
    """One refinement stage of the DMVFN cascade (simplified single-path
    version, see module docstring). Consumes and produces a 5-channel
    (flow-to-cur(2), flow-to-prev(2), fusion-mask-logit(1)) estimate.

    The per-stage residual delta is tanh-bounded (max_flow/max_mask_logit)
    before being added to the running estimate. Found this the hard way:
    without a bound, the flow_mask channels blew up to ~1e7 within a couple
    of training steps (each stage's conv trunk sees the previous, already-
    growing flow_mask as part of its input, so any unbounded amplification
    compounds across all 9 stages) -- confirmed by inspecting flow_mask
    after a few epochs and finding it in the tens of millions, with the
    fusion mask saturated to exactly 0.

    Even bounded, max_flow=8.0 + lr=1e-3 (SimVP's lr) still collapsed: all 9
    stages learned to push flow the SAME direction, saturating every stage's
    tanh (near-zero gradient there) and driving the fusion mask to ~0 --
    net effect, the model gives up and always samples I_cur's corner pixel
    (which is ~0 in this domain: soundmap/gray corners are usually far from
    the speaker), a cheap local minimum for an L1 loss on a mostly-near-0
    sparse target. Confirmed on a 10-bag/10-epoch smoke test: loss froze at
    an exact constant from epoch 2 onward -- a dead, zero-gradient trap, not
    slow learning. max_flow=2.0 + lr=1e-4 (smaller steps, less prone to
    saturating the whole cascade in one direction) trains cleanly instead:
    peak_dist ~11-13 at t+1/t+2 within 10 epochs on the same smoke test,
    comparable to SimVP's numbers at similar training amounts. **If tuning
    this further, watch for the loss going exactly flat across epochs --
    that's this saturation trap, not convergence.**"""

    def __init__(self, channels: int, scale: int, feat: int = 32,
                 max_flow: float = 2.0, max_mask_logit: float = 4.0):
        super().__init__()
        self.scale = scale
        self.max_flow = max_flow
        self.max_mask_logit = max_mask_logit
        in_ch = 2 * channels + 5  # I_prev, I_cur, running flow/mask
        self.conv = nn.Sequential(
            nn.Conv2d(in_ch, feat, 3, 1, 1), nn.PReLU(feat),
            nn.Conv2d(feat, feat, 3, 1, 1), nn.PReLU(feat),
            nn.Conv2d(feat, feat, 3, 1, 1), nn.PReLU(feat),
        )
        self.head = nn.Conv2d(feat, 5, 3, 1, 1)
        # zero-init the head so the first forward pass starts as a no-op
        # refinement (flow_mask unchanged) rather than a random jump
        nn.init.zeros_(self.head.weight)
        nn.init.zeros_(self.head.bias)

    def forward(self, i_prev: torch.Tensor, i_cur: torch.Tensor, flow_mask: torch.Tensor) -> torch.Tensor:
        x = torch.cat([i_prev, i_cur, flow_mask], dim=1)
        xs = F.avg_pool2d(x, self.scale) if self.scale > 1 else x
        raw = self.head(self.conv(xs))
        flow_delta = torch.tanh(raw[:, 0:4]) * self.max_flow
        mask_delta = torch.tanh(raw[:, 4:5]) * self.max_mask_logit
        delta = torch.cat([flow_delta, mask_delta], dim=1)
        if self.scale > 1:
            delta = F.interpolate(delta, size=x.shape[-2:], mode="bilinear", align_corners=False)
        return flow_mask + delta


class DMVFN(nn.Module):
    """channels: channels per frame (2: sm_ratio-blended soundmap + gray).
    forward() is single-step (predicts one frame ahead from the last 2
    input frames); rollout() in train.py handles the autoregressive
    multi-step prediction this project needs for t+1..t+4 comparison."""

    def __init__(self, channels: int = 2, feat: int = 32):
        super().__init__()
        self.channels = channels
        self.blocks = nn.ModuleList([MVFB(channels, s, feat=feat) for s in SCALES])

    def forward(self, i_prev: torch.Tensor, i_cur: torch.Tensor):
        """i_prev/i_cur: (B, channels, H, W), both in [0,1]. Returns
        (estimates, flow_mask): estimates is a list of (B,channels,H,W),
        one per MVFB stage (for deep supervision, paper Eq 13) -- last
        entry is the final prediction, guaranteed in [0,1] since it's a
        convex blend of [0,1] inputs (no sigmoid needed)."""
        B, C, H, W = i_cur.shape
        flow_mask = torch.zeros(B, 5, H, W, device=i_cur.device, dtype=i_cur.dtype)
        estimates = []
        for block in self.blocks:
            flow_mask = block(i_prev, i_cur, flow_mask)
            f_to_cur, f_to_prev = flow_mask[:, 0:2], flow_mask[:, 2:4]
            mask = torch.sigmoid(flow_mask[:, 4:5])
            warped_cur = backward_warp(i_cur, f_to_cur)
            warped_prev = backward_warp(i_prev, f_to_prev)
            estimates.append(warped_prev * mask + warped_cur * (1 - mask))
        return estimates, flow_mask


def laplacian_pyramid_l1(pred: torch.Tensor, target: torch.Tensor, levels: int = 3) -> torch.Tensor:
    """Simplified Laplacian-pyramid L1: sum of L1 at `levels` progressively
    downsampled (avg-pool 2x) resolutions -- not the paper's exact
    Laplacian-of-Gaussian formulation, but same spirit (penalize both
    coarse structure and fine detail, not just raw pixels)."""
    loss = F.l1_loss(pred, target)
    p, t = pred, target
    for _ in range(levels - 1):
        if min(p.shape[-2:]) < 2:
            break
        p, t = F.avg_pool2d(p, 2), F.avg_pool2d(t, 2)
        loss = loss + F.l1_loss(p, t)
    return loss


def flow_smoothness_loss(flow_mask: torch.Tensor) -> torch.Tensor:
    """Total-variation smoothness regularization on the flow channels
    (0:4) of a flow_mask estimate -- penalizes neighboring-pixel flow
    differences. Added after visually inspecting early predictions and
    finding non-convex, jagged, "torn plastic bag"-looking blobs instead
    of the smooth Gaussian-ish shape real soundmap peaks have (see
    CONTEXT.md) -- a classic symptom of an unregularized, spatially
    incoherent flow field tearing up whatever it warps. Standard practice
    in essentially every optical-flow paper; this implementation was
    missing it."""
    flow = flow_mask[:, 0:4]
    dx = (flow[:, :, :, 1:] - flow[:, :, :, :-1]).abs().mean()
    dy = (flow[:, :, 1:, :] - flow[:, :, :-1, :]).abs().mean()
    return dx + dy


def dmvfn_loss(estimates: list[torch.Tensor], target: torch.Tensor,
                flow_mask: torch.Tensor | None = None, smooth_weight: float = 0.1) -> torch.Tensor:
    """estimates: list of (B,channels,H,W) per-stage predictions (from
    DMVFN.forward). target: (B,1,H,W) ground-truth next-step soundmap --
    only channel 0 of each estimate is supervised (see module docstring).
    flow_mask (the final stage's, if given): adds flow_smoothness_loss
    weighted by smooth_weight -- see that function's docstring for why."""
    n = len(estimates)
    total = torch.zeros((), device=target.device, dtype=target.dtype)
    for i, est in enumerate(estimates):
        weight = GAMMA ** (n - 1 - i)  # last/finest stage gets weight 1
        total = total + weight * laplacian_pyramid_l1(est[:, 0:1], target)
    if flow_mask is not None:
        total = total + smooth_weight * flow_smoothness_loss(flow_mask)
    return total
