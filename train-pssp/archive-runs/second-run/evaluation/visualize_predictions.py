"""Visual sanity check for the loss ablation: render N sample windows'
ground truth vs MSE/BCE/KL predictions side by side, so the entropy/peak_mass
numbers in RESULTS.md can be checked against what the maps actually look
like, not just trusted as numbers.

IMPORTANT (learned the hard way, see CONTEXT.md): the first version of this
script normalized each tile independently (its own min-max), which stretches
every tile's contrast to fill the display range regardless of its actual
entropy -- this HIDES real sharpness differences, since a moderately diffuse
blob and a genuinely sharp peak both end up looking "bright in the middle,
dark at the edges" once independently stretched. The owner looked at that
version and correctly didn't buy that MSE looked blurrier, and they were
right to push back.

Fixed here: for each row (one test window), target + mse + bce + kl are all
normalized to a probability distribution first (metrics.to_prob_dist, same
function RESULTS.md's numbers come from) and then displayed on a SHARED
color scale (that row's own max across all four maps, not each tile's own
max) -- so relative brightness across the four columns is honest. Each
tile is also annotated with its actual peak_mass value so the image can be
cross-checked against RESULTS.md directly, not just eyeballed.
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
from metrics import peak_mass, to_prob_dist
from simvp import SimVP
from train import DATA_DIR, LOSS_OUTPUT_ACTIVATION, RUNS_DIR

CLIP_LEN, PRED_LEN, SM_RATIO = 10, 4, 0.5
STEP = 1  # which prediction step to visualize (0-indexed -> t+2)
N_SAMPLES = 10
CELL = 128  # display size per tile


def load_checkpoint(loss_name, shape_in, pred_len, simvp_type, device):
    model = SimVP(shape_in, pred_len, model_type=simvp_type,
                  output_activation=LOSS_OUTPUT_ACTIVATION[loss_name]).to(device)
    state_dict = torch.load(RUNS_DIR / loss_name / "best_model.pt", map_location=device, weights_only=True)
    state_dict = {k.replace("module.", ""): v for k, v in state_dict.items()}
    model.load_state_dict(state_dict)
    model.eval()
    return model


def to_display(p: np.ndarray, vmax: float) -> np.ndarray:
    """p: (H,W) probability distribution (already to_prob_dist'd). Renders on
    a SHARED [0, vmax] scale (not this tile's own max) so tiles in the same
    row are honestly comparable."""
    x = np.clip(p / vmax, 0, 1)
    x8 = (x * 255).astype(np.uint8)
    x8 = cv2.resize(x8, (CELL, CELL), interpolation=cv2.INTER_NEAREST)
    return cv2.applyColorMap(x8, cv2.COLORMAP_INFERNO)


def gray_to_display(gray: np.ndarray) -> np.ndarray:
    g8 = (gray * 255).astype(np.uint8) if gray.max() <= 1.0 else gray.astype(np.uint8)
    g8 = cv2.resize(g8, (CELL, CELL), interpolation=cv2.INTER_NEAREST)
    return cv2.cvtColor(g8, cv2.COLOR_GRAY2BGR)


def label(img: np.ndarray, text: str, y: int = 16) -> np.ndarray:
    out = img.copy()
    cv2.putText(out, text, (4, y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    return out


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    shape_in = (CLIP_LEN, 2, 64, 64)

    test_bags = [DATA_DIR / f"{b}.npz" for b in TEST_BAGS]
    test_ds = PSSPWindowDataset(test_bags, clip_len=CLIP_LEN, pred_len=PRED_LEN, sm_ratio=SM_RATIO)
    print(f"test set: {TEST_BAGS} ({len(test_ds)} windows)")

    models = {name: load_checkpoint(name, shape_in, PRED_LEN, "gsta", device) for name in ["mse", "bce", "kl"]}

    idxs = np.linspace(0, len(test_ds) - 1, N_SAMPLES, dtype=int)
    columns = ["camera", "ground truth"] + list(models.keys())
    rows = []
    for row_i, idx in enumerate(idxs):
        x, y = test_ds[idx]
        x_dev = x.unsqueeze(0).to(device)
        target = y[STEP, 0].numpy()
        cam = x[-1, 1].numpy()  # last input frame's gray channel

        dists = {"ground truth": to_prob_dist(target, from_logits=False)}
        for name, model in models.items():
            with torch.no_grad():
                out = model(x_dev)[0, STEP, 0].cpu().numpy()
            dists[name] = to_prob_dist(out, from_logits=LOSS_OUTPUT_ACTIVATION[name] == "none")

        vmax = max(peak_mass(d) for d in dists.values())  # shared scale across this row's 4 maps

        tiles = [gray_to_display(cam)]
        for name, d in dists.items():
            tile = to_display(d, vmax)
            tile = label(tile, f"pm={peak_mass(d):.4f}", y=CELL - 6)
            tiles.append(tile)

        if row_i == 0:
            tiles = [label(t, columns[i]) for i, t in enumerate(tiles)]
        rows.append(np.hstack(tiles))

    montage = np.vstack(rows)
    out_path = _HERE / "prediction_comparison.png"
    cv2.imwrite(str(out_path), montage)
    print(f"saved -> {out_path}")


if __name__ == "__main__":
    main()
