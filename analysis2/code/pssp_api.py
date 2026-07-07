"""PSSP API: SimVP +Δt sound-map prediction (device-agnostic).

    api = PsspAPI()                        # device="auto": cuda -> mps -> cpu
    preds = api.predict(clip10)            # clip10: (10, 2, 64, 64) float
    # preds: (4, 64, 64) = predicted sound maps at +0.5/+1.0/+1.5/+2.0 s

Uses the live weights `config_simvp_exp4.pt` (NOT the epoch31 file analysis1
loaded by mistake). Build the clip frames with labeling.build_clip_frame and
subsample the 19-tick history [::2] to 10 frames @2 Hz, exactly as mode_pssp.
"""
from __future__ import annotations

import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_PSSP_DIR = os.path.join(_HERE, "pssp")
if _PSSP_DIR not in sys.path:
    sys.path.insert(0, _PSSP_DIR)

WEIGHTS = os.path.join(_PSSP_DIR, "config_simvp_exp4.pt")
HORIZONS_S = (0.5, 1.0, 1.5, 2.0)  # SimVP pred_len=4 at 2 Hz


class PsspAPI:
    def __init__(self, weights_path: str = WEIGHTS, device: str = "auto",
                 clip_len: int = 10, sm_size: int = 64, pred_len: int = 4,
                 model_type: str = "gsta"):
        import torch
        from simvp import SimVP

        self.torch = torch
        self.device = self._resolve_device(torch, device)
        self.pred_len = pred_len

        model = SimVP((clip_len, 2, sm_size, sm_size), pred_len, model_type=model_type)
        state = torch.load(weights_path, map_location="cpu", weights_only=True)
        if isinstance(state, dict) and "state_dict" in state:
            state = state["state_dict"]
        state = {k.replace("module.", ""): v for k, v in state.items()}
        model.load_state_dict(state)
        self.model = model.to(self.device).eval()

    @staticmethod
    def _resolve_device(torch, device: str) -> str:
        """'auto' -> cuda if available, else Apple mps, else cpu.
        Any explicit value ('cuda'/'mps'/'cpu') is honored as-is."""
        if device != "auto":
            return device
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def predict(self, clip10) -> np.ndarray:
        """clip10: (10, 2, 64, 64) float32. Returns (pred_len, 64, 64) predicted sound maps."""
        arr = np.asarray(clip10, dtype=np.float32)
        with self.torch.no_grad():
            inp = self.torch.as_tensor(arr).unsqueeze(0).to(self.device)
            out = self.model(inp)
            preds = out[0, :, 0, :, :].cpu().numpy()
        return preds
