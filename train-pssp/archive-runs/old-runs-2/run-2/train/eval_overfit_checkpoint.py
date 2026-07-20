"""One-off side-check for overfit_probe.py (2026-07-19, owner's request): the
probe itself tracks TRAIN metrics only (no held-out split, by design -- see
its docstring). This script loads whatever `last_model.pt` currently holds
(overwritten every epoch by the running probe) and scores it against two
domains that are entirely absent from the probe's 24-bag pool -- WordWolfExp
G8 (all 6 games, matches Phase2's cross-validation group) and the official
GRP_meeting held-out bag (MTG_TEST_BAGS) -- to get a coarse, unofficial
train-vs-unseen-data snapshot while the probe keeps running. Not a real
held-out eval protocol (single snapshot, no repeated runs), just a sanity
check on whether the probe's overfitting is also visible on unseen domains.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "evaluation"))

from dataset import FullBagWindowDataset, WW_ALL_BAGS, MTG_TEST_BAGS
from simvp import SimVP
from train import peak_dist_batch, psr_batch

CLIP_LEN, PRED_LEN, SM_RATIO, BS = 10, 4, 0.5, 32
CKPT = _HERE / "runs" / "overfit_probe_g1g2g3" / "last_model.pt"

G8_BAGS = [b for b in WW_ALL_BAGS if b.split("_")[0] == "G8"]


def score(model, bags, device):
    ds = FullBagWindowDataset(bags, clip_len=CLIP_LEN, pred_len=PRED_LEN, sm_ratio=SM_RATIO)
    loader = DataLoader(ds, batch_size=BS, shuffle=False, drop_last=True)
    pd_sum = psr_sum = None
    n = 0
    with torch.no_grad():
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            out = model(x)
            pd = peak_dist_batch(out, y)
            psr = psr_batch(out, y)
            pd_sum = pd if pd_sum is None else pd_sum + pd
            psr_sum = psr if psr_sum is None else psr_sum + psr
            n += 1
    return float((pd_sum / n).mean()), float((psr_sum / n).mean()), len(ds)


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    shape_in = (CLIP_LEN, 2, 64, 64)
    model = SimVP(shape_in, PRED_LEN, model_type="gsta", N_S=4, N_T=4).to(device)
    state = torch.load(CKPT, map_location=device)
    model.load_state_dict(state)
    model.eval()
    mtime = time.strftime("%H:%M:%S", time.localtime(CKPT.stat().st_mtime))
    print(f"checkpoint mtime: {mtime}")

    pd, psr, n = score(model, G8_BAGS, device)
    print(f"wordwolfexp G8 (unseen, {n} windows): peak_dist={pd:.3f}  psr={psr:.2%}")

    pd, psr, n = score(model, MTG_TEST_BAGS, device)
    print(f"grpmtg held-out bag (unseen, {n} windows): peak_dist={pd:.3f}  psr={psr:.2%}")


if __name__ == "__main__":
    main()
