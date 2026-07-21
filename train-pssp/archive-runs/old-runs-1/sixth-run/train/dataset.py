"""sixth-run's data loading: chat-only (3 bags), per-bag TIME split
(train_ratio=0.9, matching exp4's own `NPZLoader.get_index()` split exactly --
see CONTEXT.md's fifth-run section), used to ablate the three real training-
pipeline differences found by reading access-model-train/ line by line:
lr, sliding-window start-index density, and data augmentation.

Two dataset classes, same exp(x-max) normalization / sm_ratio blend / output
shapes as fifth-run's PSSPWindowDataset:

  - SparseChatWindowDataset: window starts only at 2Hz-native tick indices
    (our usual extraction grid, reused from train-data/chat_*.npz) -- this is
    what every run before sixth-run has used, for train AND test.
  - DenseChatWindowDataset: window starts at EVERY native-camera-rate frame
    (~30Hz, extracted once by ../extract_chat_dense.py into
    ../data-dense/*_dense.npz), with window CONTENT internally strided by
    skip_frames to keep the same ~2Hz per-frame spacing inside each window --
    this exactly replicates exp4's actual windowing mechanism
    (utils_all_load.py's NPZLoader.get_index()/CustomDataset.__getitem__).
    ~15x more (heavily overlapping, phase-shifted) windows per bag than the
    sparse version, same underlying recording.

**The TEST set is ALWAYS the sparse/2Hz version** (last 10% of each bag's
2Hz-native timeline), for both ablation arms -- only the TRAIN set's density
is the variable being tested in ablation #1. Keeping eval construction fixed
means peak_dist/PSR/correlation numbers are directly comparable between the
sparse-trained and dense-trained models.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset

_HERE = Path(__file__).resolve().parent
SPARSE_DATA_DIR = _HERE.parent.parent.parent / "train-data"
DENSE_DATA_DIR = _HERE.parent / "data-dense"

CHAT_BAGS = ["chat_debate_exp1_topic1", "chat_debate_exp1_topic2", "chat_debate_exp1_topic3"]
TRAIN_RATIO = 0.9  # matches exp4's train_ratio exactly
SKIP_FRAMES = 15   # matches exp4's `30 // fps` with fps=2 (native chat camera rate measured ~29.95Hz)


def exp_transform(sm: np.ndarray) -> np.ndarray:
    """Per-frame exp(x - x.max()) over the last two axes -- same as every
    other run's dataset.py."""
    m = sm.max(axis=(-2, -1), keepdims=True)
    out = np.zeros_like(sm, dtype=np.float32)
    valid = (m > 0).squeeze(axis=(-2, -1))
    out[valid] = np.exp(sm[valid] - m[valid])
    return out


class SparseChatWindowDataset(Dataset):
    """2Hz-native windows, restricted per-bag to the train_ratio-defined
    front (split='train') or back (split='test') portion of each bag's
    timeline. Used as: the sparse ablation arm's train set, AND (always,
    regardless of ablation arm) the shared test set."""

    def __init__(self, split: str, clip_len: int = 10, pred_len: int = 4, sm_ratio: float = 0.5,
                 train_ratio: float = TRAIN_RATIO, bags: list[str] = CHAT_BAGS):
        assert split in ("train", "test")
        self.clip_len = clip_len
        self.pred_len = pred_len
        self.sm_ratio = sm_ratio
        self.window_len = clip_len + pred_len

        self.bags = []
        self.index = []
        for bag_idx, name in enumerate(bags):
            d = np.load(SPARSE_DATA_DIR / f"{name}.npz")
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
        target_sm = sm_exp[self.clip_len:]

        sm_ratio = self.sm_ratio
        sm_channel = hist_sm if sm_ratio == 1.0 else sm_ratio * hist_sm + (1.0 - sm_ratio) * hist_gray
        x = np.stack([sm_channel, hist_gray], axis=1)
        return (
            torch.from_numpy(x.astype(np.float32)),
            torch.from_numpy(target_sm[:, None].astype(np.float32)),
        )


class DenseChatWindowDataset(Dataset):
    """Native-camera-rate (~30Hz) window starts, every raw frame index in
    the bag's train_ratio-defined front portion is a valid start -- window
    CONTENT is strided by skip_frames, replicating exp4's actual windowing
    (see module docstring). Train-only (no 'split' arg) -- the shared test
    set is always SparseChatWindowDataset(split='test')."""

    def __init__(self, clip_len: int = 10, pred_len: int = 4, sm_ratio: float = 0.5,
                 train_ratio: float = TRAIN_RATIO, skip_frames: int = SKIP_FRAMES,
                 bags: list[str] = CHAT_BAGS):
        self.clip_len = clip_len
        self.pred_len = pred_len
        self.sm_ratio = sm_ratio
        self.skip_frames = skip_frames
        self.window_span = (clip_len + pred_len - 1) * skip_frames + 1  # raw frames spanned

        self.bags = []
        self.index = []
        for bag_idx, name in enumerate(bags):
            raw_name = name.removeprefix("chat_")  # "debate_exp1_topicN"
            d = np.load(DENSE_DATA_DIR / f"{raw_name}_dense.npz")
            sm_raw = d["soundmap"].astype(np.float32)
            gray = d["gray_camimg"].astype(np.float32) / 255.0
            sm_exp = exp_transform(sm_raw)
            n = sm_exp.shape[0]
            self.bags.append({"name": name, "sm_exp": sm_exp, "gray": gray})

            train_num = int(n * train_ratio)
            hi = train_num - self.window_span + 1  # last valid raw start index (exclusive)
            for start in range(0, max(hi, 0)):
                self.index.append((bag_idx, start))

    def __len__(self) -> int:
        return len(self.index)

    def __getitem__(self, idx: int):
        bag_idx, start = self.index[idx]
        bag = self.bags[bag_idx]
        sel = slice(start, start + self.window_span, self.skip_frames)
        sm_exp = bag["sm_exp"][sel]
        gray = bag["gray"][sel]
        hist_sm, hist_gray = sm_exp[:self.clip_len], gray[:self.clip_len]
        target_sm = sm_exp[self.clip_len:]

        sm_ratio = self.sm_ratio
        sm_channel = hist_sm if sm_ratio == 1.0 else sm_ratio * hist_sm + (1.0 - sm_ratio) * hist_gray
        x = np.stack([sm_channel, hist_gray], axis=1)
        return (
            torch.from_numpy(x.astype(np.float32)),
            torch.from_numpy(target_sm[:, None].astype(np.float32)),
        )


def make_datasets(density: str, clip_len: int = 10, pred_len: int = 4, sm_ratio: float = 0.5):
    """density: 'sparse' or 'dense' -- selects the TRAIN set's construction.
    Test set is always sparse (see module docstring)."""
    assert density in ("sparse", "dense")
    test_ds = SparseChatWindowDataset("test", clip_len=clip_len, pred_len=pred_len, sm_ratio=sm_ratio)
    if density == "sparse":
        train_ds = SparseChatWindowDataset("train", clip_len=clip_len, pred_len=pred_len, sm_ratio=sm_ratio)
    else:
        train_ds = DenseChatWindowDataset(clip_len=clip_len, pred_len=pred_len, sm_ratio=sm_ratio)
    return train_ds, test_ds
