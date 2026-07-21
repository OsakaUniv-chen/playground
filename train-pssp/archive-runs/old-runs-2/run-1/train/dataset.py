"""run-1 data loading: chat-only (3 bags), per-bag TIME split (train_ratio=0.9,
matching exp4's own `NPZLoader.get_index()` split -- see
archive-runs/old-runs-1/CONTEXT.md). Ported from eighth-run's
ChatWindowDataset, trimmed to just this one dataset (run-1's scope is
chat-only by design -- see run-1/PLAN.md "范围调整"; the multi-group
MixedWindowDataset machinery moves to run-2+ when training-group scale-up
is back in scope).

Naming: split='train' is the front train_ratio fraction of each bag's
timeline, split='val' is the back (1-train_ratio) fraction -- called "val"
everywhere in run-1 (not "test") because it's a same-bag time split, not a
held-out group. See PLAN.md's terminology note.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset

_HERE = Path(__file__).resolve().parent
DATA_DIR = _HERE.parent.parent / "train-data"

CHAT_BAGS = ["chat_debate_exp1_topic1", "chat_debate_exp1_topic2", "chat_debate_exp1_topic3"]
TRAIN_RATIO = 0.9  # matches exp4's train_ratio exactly


def exp_transform(sm: np.ndarray) -> np.ndarray:
    """Per-frame exp(x - x.max()) over the last two axes -- same as every
    other run's dataset.py."""
    m = sm.max(axis=(-2, -1), keepdims=True)
    out = np.zeros_like(sm, dtype=np.float32)
    valid = (m > 0).squeeze(axis=(-2, -1))
    out[valid] = np.exp(sm[valid] - m[valid])
    return out


class ChatWindowDataset(Dataset):
    """2Hz-native clip_len/pred_len windows, restricted per-bag to the
    train_ratio-defined front (split='train') or back (split='val') portion
    of each bag's timeline. Returns (input, target):
      input:  (clip_len, 2, H, W) -- ch0 = sm_ratio-blended exp(sm), ch1 = gray/255
      target: (pred_len, 1, H, W) -- exp(sm)."""

    def __init__(self, split: str, clip_len: int = 10, pred_len: int = 4, sm_ratio: float = 0.5,
                 train_ratio: float = TRAIN_RATIO, bags: list[str] = CHAT_BAGS,
                 pred_offsets: list[int] | None = None):
        assert split in ("train", "val")
        self.clip_len = clip_len
        self.sm_ratio = sm_ratio
        self.pred_offsets = list(pred_offsets) if pred_offsets is not None else list(range(1, pred_len + 1))
        self.pred_len = len(self.pred_offsets)
        self.window_len = clip_len + max(self.pred_offsets)

        self.bags = []
        self.index = []  # (bag_idx, start) -- __getitem__(idx) reads self.index[idx]
        for bag_idx, name in enumerate(bags):
            d = np.load(DATA_DIR / f"{name}.npz")
            sm_raw = d["soundmap"].astype(np.float32)
            gray = d["gray_camimg"].astype(np.float32) / 255.0
            sm_exp = exp_transform(sm_raw)
            n = sm_exp.shape[0]
            self.bags.append({"name": name, "sm_exp": sm_exp, "gray": gray})

            train_num = int(n * train_ratio)
            if split == "train":
                lo, hi = 0, train_num - self.window_len + 1
            else:
                lo, hi = train_num, n - self.window_len + 1
            for start in range(max(lo, 0), max(hi, 0)):
                self.index.append((bag_idx, start))

    def __len__(self) -> int:
        return len(self.index)

    def __getitem__(self, idx: int):
        bag_idx, start = self.index[idx]
        bag = self.bags[bag_idx]
        sm_exp = bag["sm_exp"][start:start + self.window_len]
        gray = bag["gray"][start:start + self.window_len]
        hist_sm, hist_gray = sm_exp[:self.clip_len], gray[:self.clip_len]
        target_idx = [self.clip_len + o - 1 for o in self.pred_offsets]
        target_sm = sm_exp[target_idx]

        sm_ratio = self.sm_ratio
        sm_channel = hist_sm if sm_ratio == 1.0 else sm_ratio * hist_sm + (1.0 - sm_ratio) * hist_gray
        x = np.stack([sm_channel, hist_gray], axis=1)
        return (
            torch.from_numpy(x.astype(np.float32)),
            torch.from_numpy(target_sm[:, None].astype(np.float32)),
        )


def make_datasets(clip_len: int = 10, pred_len: int = 4, sm_ratio: float = 0.5,
                   pred_offsets: list[int] | None = None):
    train_ds = ChatWindowDataset("train", clip_len=clip_len, pred_len=pred_len, sm_ratio=sm_ratio,
                                  pred_offsets=pred_offsets)
    val_ds = ChatWindowDataset("val", clip_len=clip_len, pred_len=pred_len, sm_ratio=sm_ratio,
                                pred_offsets=pred_offsets)
    return train_ds, val_ds
