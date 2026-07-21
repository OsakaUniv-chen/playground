"""Visual sanity check for DMVFN: true vs predicted soundmap at each
prediction step (t+1..t+4), for a handful of test windows. Same lesson as
second-run's visualize_predictions.py -- normalize to a shared color scale
PER SAMPLE ROW (across all 8 true/pred tiles), not per tile independently,
or real sharpness/accuracy differences get hidden by independent contrast
stretching.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import torch

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "train"))

from dataset import TEST_BAGS, PSSPWindowDataset
from dmvfn import DMVFN
from train import DATA_DIR, RUNS_DIR, rollout

CLIP_LEN, PRED_LEN, SM_RATIO = 10, 4, 0.5
N_SAMPLES = 8
CELL = 96


def to_prob_dist(x: np.ndarray) -> np.ndarray:
    """x: (H,W). Clip negatives (shouldn't occur here, values are already
    in [0,1]) then normalize to sum=1 so peak "brightness" is comparable
    across tiles the same way second-run's metrics.to_prob_dist was used."""
    x = np.clip(x, 0, None)
    s = x.sum()
    return x / s if s > 0 else x


def to_display(p: np.ndarray, vmax: float) -> np.ndarray:
    x = np.clip(p / vmax, 0, 1) if vmax > 0 else p
    x8 = (x * 255).astype(np.uint8)
    x8 = cv2.resize(x8, (CELL, CELL), interpolation=cv2.INTER_NEAREST)
    return cv2.applyColorMap(x8, cv2.COLORMAP_INFERNO)


def gray_to_display(gray: np.ndarray) -> np.ndarray:
    g8 = (np.clip(gray, 0, 1) * 255).astype(np.uint8)
    g8 = cv2.resize(g8, (CELL, CELL), interpolation=cv2.INTER_NEAREST)
    return cv2.cvtColor(g8, cv2.COLOR_GRAY2BGR)


def label(img: np.ndarray, text: str, y: int = 14) -> np.ndarray:
    out = img.copy()
    cv2.putText(out, text, (3, y), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1, cv2.LINE_AA)
    return out


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    test_bags = [DATA_DIR / f"{b}.npz" for b in TEST_BAGS]
    test_ds = PSSPWindowDataset(test_bags, clip_len=CLIP_LEN, pred_len=PRED_LEN, sm_ratio=SM_RATIO)
    print(f"test set: {TEST_BAGS} ({len(test_ds)} windows)")

    model = DMVFN(channels=2).to(device)
    ckpt_path = RUNS_DIR / "baseline" / "best_model.pt"
    state_dict = torch.load(ckpt_path, map_location=device, weights_only=True)
    model.load_state_dict(state_dict)
    model.eval()
    print(f"checkpoint: {ckpt_path}")

    idxs = np.linspace(0, len(test_ds) - 1, N_SAMPLES, dtype=int)
    rows = []
    for row_i, idx in enumerate(idxs):
        x, y = test_ds[idx]
        cam = x[-1, 1].numpy()  # last input frame's gray channel
        true_maps = [to_prob_dist(y[t, 0].numpy()) for t in range(PRED_LEN)]

        with torch.no_grad():
            out = rollout(model, x.unsqueeze(0).to(device), PRED_LEN)[0, :, 0].cpu().numpy()
        pred_maps = [to_prob_dist(out[t]) for t in range(PRED_LEN)]

        vmax = max(m.max() for m in true_maps + pred_maps)

        top = [gray_to_display(cam)] + [to_display(m, vmax) for m in true_maps]
        bottom = [np.zeros((CELL, CELL, 3), np.uint8)] + [to_display(m, vmax) for m in pred_maps]
        if row_i == 0:
            cols = ["camera"] + [f"t+{t+1}" for t in range(PRED_LEN)]
            top = [label(t, c) for t, c in zip(top, cols)]
            bottom[0] = label(bottom[0], "true (top)")
        bottom[0] = label(bottom[0], "pred (bot)", y=CELL - 6) if row_i > 0 else \
            label(bottom[0], "pred (bot)", y=CELL - 6)
        rows.append(np.hstack(top))
        rows.append(np.hstack(bottom))

    montage = np.vstack(rows)
    out_path = _HERE / "prediction_comparison.png"
    cv2.imwrite(str(out_path), montage)
    print(f"saved -> {out_path}")


if __name__ == "__main__":
    main()
