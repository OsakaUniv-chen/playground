"""Loss functions for eighth-run's Experiment A: peak-position supervision.

Motivation (see CONTEXT.md): every run so far trained on a PIXEL-level
reconstruction loss (MSE/BCE/KL) over the whole 64x64 soundmap, but the metric
we actually care about is the PEAK POSITION. A pixel loss is dominated by the
mostly-empty background, whose safe minimum is a diffuse blob near the average
position -- exactly the observed failure mode ("converges to a fixed average
position, doesn't track the input"). These losses instead supervise position
directly, via a differentiable soft-argmax (argmax itself is non-differentiable).

soft-argmax:
  Given a predicted heatmap P (H,W), turn it into a spatial probability
  distribution p (softmax over all HxW cells, temperature tau -> lower tau =
  sharper), then take the EXPECTED coordinate:
     row = sum_ij i * p_ij ,  col = sum_ij j * p_ij
  This expected coordinate is fully differentiable w.r.t. P. Supervise it
  toward the target's TRUE peak (hard argmax of the target heatmap) with a
  squared-distance loss in grid cells -- directly minimizing the quantity
  peak_dist measures.

  **Temperature matters a lot here (learned the hard way).** The model output
  is post-sigmoid, so P is in (0,1) -- a very narrow range. softmax(P/tau) with
  tau=1 over 4096 cells is then almost UNIFORM (peak-vs-background weight ratio
  <= e^1/e^0 = 2.72, so a single peak cell gets weight ~2.72/4096 ~= 0.0007),
  which makes the expected coordinate collapse to ~the grid CENTER regardless
  of the prediction -> near-zero gradient toward the true peak, loss stuck
  (observed: train_loss frozen ~100, peak_dist getting WORSE). Fix: use a
  small tau (default 0.05) so the peak cell actually dominates. Verified: at
  tau>=0.1 the soft-argmax of a clean peak (0.95 vs 0.1 bg) still sits ~5
  cells toward center (loss can't reach 0 even for a correct prediction); at
  tau=0.05 it resolves the peak exactly (loss->0) with healthy gradients, and
  a weak/diffuse prediction gets pulled to center with a strong gradient
  PUSHING the model to sharpen its peak -- a useful side effect (pixel MSE
  never pressures the model to be confident). Do NOT use tau~1 with
  sigmoid-range inputs.

Two entry points:
  - soft_argmax_loss: pure position loss (single-variable comparison vs MSE).
  - combined_loss: MSE + lambda * position loss, a fallback if pure-position
    produces degenerate heatmap shapes (the model could output a weird
    distribution whose mean happens to be right). Not used unless needed.

All shapes follow the rest of the project: pred/target are (B, pred_len, 1, H, W).
"""
from __future__ import annotations

import torch
import torch.nn.functional as F


def _soft_argmax_coords(heat: torch.Tensor, tau: float) -> tuple[torch.Tensor, torch.Tensor]:
    """heat: (..., H, W) raw heatmap. Returns (row, col) expected coordinates,
    each (...) -- differentiable soft-argmax over the last two dims."""
    *lead, H, W = heat.shape
    flat = heat.reshape(*lead, H * W)
    p = F.softmax(flat / tau, dim=-1)                       # (..., H*W) spatial distribution
    device, dtype = heat.device, p.dtype
    rows = torch.arange(H, device=device, dtype=dtype).repeat_interleave(W)   # (H*W,)
    cols = torch.arange(W, device=device, dtype=dtype).repeat(H)              # (H*W,)
    row = (p * rows).sum(dim=-1)
    col = (p * cols).sum(dim=-1)
    return row, col


def _hard_argmax_coords(heat: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """heat: (..., H, W). Returns (row, col) integer-valued peak coordinates
    (as float), each (...) -- the TRUE peak, used as the (detached) target."""
    *lead, H, W = heat.shape
    flat = heat.reshape(*lead, H * W)
    idx = flat.argmax(dim=-1)
    return (idx // W).to(heat.dtype), (idx % W).to(heat.dtype)


def soft_argmax_loss(pred: torch.Tensor, target: torch.Tensor, tau: float = 0.05) -> torch.Tensor:
    """pred/target: (B, T, 1, H, W). Squared distance (grid cells) between the
    prediction's soft-argmax coordinate and the target's true peak, averaged
    over batch and prediction step. tau is the softmax temperature (lower =
    treats the predicted map as sharper)."""
    p = pred[:, :, 0]      # (B,T,H,W)
    t = target[:, :, 0]
    pr_row, pr_col = _soft_argmax_coords(p, tau)                 # (B,T) differentiable
    with torch.no_grad():
        tg_row, tg_col = _hard_argmax_coords(t)                  # (B,T) target peak, detached
    return ((pr_row - tg_row) ** 2 + (pr_col - tg_col) ** 2).mean()


def combined_loss(pred: torch.Tensor, target: torch.Tensor, lam: float = 0.01,
                  tau: float = 0.05) -> torch.Tensor:
    """MSE(pixel) + lam * soft_argmax_loss(position). Fallback if pure position
    loss degenerates the heatmap shape. lam scales the position term (which is
    in squared-grid-cell units, typically O(1-100)) down to be comparable with
    MSE (typically O(1e-3)); default 0.01 is a starting point, may need tuning."""
    return F.mse_loss(pred, target) + lam * soft_argmax_loss(pred, target, tau)
