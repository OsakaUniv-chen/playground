"""Inference-only helpers for the OLD (pre-rewrite) PSSP SimVP checkpoint.

Purely for running the archived exp4 weights forward, to get a fair
old-vs-new comparison later once the new model is trained (see
train-pssp/CONTEXT.md). No training code here on purpose -- see
train-pssp/old-train-scripts/ for the original training pipeline this
checkpoint came from. Weights/config copied verbatim from
/home/chen/Documents/R4/run_pssp/train_orig_sm_target_sm_gray/results/.
(exp1~3 were a sm_ratio ablation sweep, same architecture/data otherwise --
only exp4 was kept.)

Input convention: channel 0 = sm_ratio * exp(raw_soundmap - raw_soundmap.max())
+ (1 - sm_ratio) * gray/255, channel 1 = gray/255. This mirrors the ACTUAL
live robot deployment code (`R4/run_pssp/pssp_policy.py`'s `transform()` +
`fused_sm = cv2.addWeighted(transformed_sm, 0.5, gray_img, 0.5, 0)`, which
matches exp4's sm_ratio=0.5 exactly) -- NOT the archived
`old-train-scripts/create_dataset.py`, whose exp(sm-sm.max()) line turned out
to be commented out and is unverified as the script that actually produced
these checkpoints' training data (see CONTEXT.md's corrected normalization
note; an earlier version of this module wrongly assumed raw 0..160 based on
that unverified script). The original training npz files no longer exist on
disk, so this is the best evidence available, not a 100%-confirmed fact.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch

from simvp import SimVP

_HERE = Path(__file__).resolve().parent
CONFIGS_DIR = _HERE / "configs"
WEIGHTS_DIR = _HERE / "weights"

CLIP_LEN = 10   # old argparse default; not present in the exp4 json config
IMG_SZ = 64     # old argparse default, ditto
CHANNEL_COUNT = 2  # soundmap(1ch) + gray_camimg(1ch)

# Only exp4 (sm_ratio=0.5) was kept -- exp1~3 (sm_ratio 1.0/0.9/0.7, same
# architecture/data otherwise) were an ablation sweep and aren't needed here.
# simvp_exp4_new: a newer checkpoint found alongside exp4's at
# /home/chen/Documents/R4/results/ (config_simvp_exp4_new.pt) -- same
# architecture/sm_ratio (per its json config), different training exp_name
# selection/lr. Never confirmed better/worse than exp4 as the reference
# baseline (open question in archive-runs/old-runs-1/CONTEXT.md). run-1
# tried loading it under the same preprocessing convention as exp4
# (exp-transformed target, sm_ratio=0.5 input blend -- independently
# confirmed correct for exp4 by matching historical numbers exactly) and got
# degenerate output (peak_dist ~46/64, PSR 0%) despite the weights
# themselves looking legitimately trained (no NaN/Inf, normal-scale stats) --
# likely its TRUE preprocessing convention differs from exp4's and was never
# independently verified. Parked (owner decision 2026-07-18, see CONTEXT.md
# open questions) -- still registered here for whenever someone wants to dig
# in, just not used as a default comparison baseline for now.
EXP_NAMES = ("simvp_exp4", "simvp_exp4_new")


def load_config(exp_name: str) -> dict:
    with open(CONFIGS_DIR / f"{exp_name}.json") as f:
        return json.load(f)


def load_model(exp_name: str, device: str = "cuda") -> torch.nn.Module:
    """Build a SimVP matching exp_name's config and load its trained weights."""
    cfg = load_config(exp_name)
    shape_in = (CLIP_LEN, CHANNEL_COUNT, IMG_SZ, IMG_SZ)
    model = SimVP(shape_in, cfg["pred_len"], model_type=cfg["simvp_type"])

    state_dict = torch.load(WEIGHTS_DIR / f"config_{exp_name}.pt", map_location=device, weights_only=True)
    state_dict = {k.replace("module.", ""): v for k, v in state_dict.items()}
    model.load_state_dict(state_dict)

    model = model.to(device)
    model.eval()
    return model


def _exp_transform(sm: np.ndarray) -> np.ndarray:
    """Per-frame exp(x - x.max()), matching pssp_policy.py's transform(). No-op
    (stays all-zero) for a silent/all-zero frame, matching the `if np.max(sm) > 0`
    guard in the original."""
    out = np.zeros_like(sm, dtype=np.float32)
    for t in range(sm.shape[0]):
        frame = sm[t]
        if frame.max() > 0:
            out[t] = np.exp(frame - frame.max())
    return out


def build_input(soundmap: np.ndarray, gray_camimg: np.ndarray, sm_ratio: float) -> torch.Tensor:
    """soundmap: (T,H,W) raw [0,160]. gray_camimg: (T,H,W) uint8 [0,255].
    Returns (1,T,2,H,W) float32 tensor matching the live deployment's input
    convention (see module docstring): exp-transform then sm_ratio blend."""
    sm = _exp_transform(soundmap.astype(np.float32))
    gray = gray_camimg.astype(np.float32) / 255.0
    sm_channel = sm if sm_ratio == 1.0 else sm_ratio * sm + (1.0 - sm_ratio) * gray
    x = np.stack([sm_channel, gray], axis=1)  # (T,2,H,W)
    return torch.from_numpy(x).unsqueeze(0)   # (1,T,2,H,W)


@torch.no_grad()
def predict(model: torch.nn.Module, input_tensor: torch.Tensor, device: str = "cuda") -> np.ndarray:
    """Returns (pred_len,H,W) numpy array, sigmoid-bounded [0,1] (see module docstring)."""
    out = model(input_tensor.to(device).contiguous())  # (1,pred_len,1,H,W)
    return out[0, :, 0].cpu().numpy()
