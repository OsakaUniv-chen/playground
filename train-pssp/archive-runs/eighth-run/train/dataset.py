"""eighth-run data loading: chat-only (3 bags), per-bag TIME split
(train_ratio=0.9, matching exp4's own `NPZLoader.get_index()` split exactly --
see CONTEXT.md). Simplified from sixth-run's dataset.py to sparse-only: both
of eighth-run's experiments (peak-position loss, longer input window) use the
2Hz-native sparse windows, so the dense-extraction path is dropped entirely.

eighth-run's purpose (see CONTEXT.md "当前开放问题/下一步方向"): after seven
runs of varying lr/data-scale/diversity/augmentation/loss-SHAPE without solving
the core "position correlation below naive baseline" problem, attack the one
never-varied lever -- the loss's supervision TARGET. Two experiments, done
separately, small-tested on chat first:
  - Experiment A: peak-position (soft-argmax) loss vs the usual pixel MSE.
  - Experiment B: longer input window (clip_len=20 = 10s @2Hz vs the usual 10).

Both keep the SAME chat train/test split (last 10% of each bag = test), so
peak_dist/PSR/correlation are directly comparable across experiments. clip_len
is a plain constructor arg (Experiment B just passes clip_len=20).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset

_HERE = Path(__file__).resolve().parent
# archive-runs/ adds one extra directory level vs. this run's original
# top-level location -- see CONTEXT.md "仓库结构": archived runs need
# .parent.parent.parent to reach train-pssp/, not .parent.parent.
DATA_DIR = _HERE.parent.parent.parent / "train-data"

CHAT_BAGS = ["chat_debate_exp1_topic1", "chat_debate_exp1_topic2", "chat_debate_exp1_topic3"]
TRAIN_RATIO = 0.9  # matches exp4's train_ratio exactly

# WordWolfExp G1/G2 as extra full-training data, G3's game3-6 as a fully held-out
# test group (owner's request, 2026-07-16). G3_game2_Video/G3_interview are
# deliberately unused (neither train nor test) -- owner chose the clean split
# over reusing fifth-run's "leave same-group bags in training" precedent.
G1_BAGS = ["G1_game2_Video", "G1_game3_Tele", "G1_game4_PSSP", "G1_game5_DoA",
           "G1_game6_Random", "G1_interview"]
G2_BAGS = ["G2_game2_Video", "G2_game3_PSSP", "G2_game4_DoA", "G2_game5_Tele",
           "G2_game6_Random", "G2_interview"]
G3_TEST_BAGS = ["G3_game3_Tele", "G3_game4_DoA", "G3_game5_PSSP", "G3_game6_Random"]


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
    train_ratio-defined front (split='train') or back (split='test') portion
    of each bag's timeline. Returns (input, target):
      input:  (clip_len, 2, H, W) -- ch0 = sm_ratio-blended exp(sm), ch1 = gray/255
      target: (pred_len, 1, H, W) -- exp(sm)."""

    def __init__(self, split: str, clip_len: int = 10, pred_len: int = 4, sm_ratio: float = 0.5,
                 train_ratio: float = TRAIN_RATIO, bags: list[str] = CHAT_BAGS,
                 pred_offsets: list[int] | None = None):
        assert split in ("train", "test")
        self.clip_len = clip_len
        self.sm_ratio = sm_ratio
        # pred_offsets: which ticks-ahead (1-indexed from the last history
        # frame) to predict. Defaults to 1..pred_len (every prior run's dense
        # multi-step target). A single non-default offset (e.g. [2] for a
        # single "+1s @2Hz" target) skips the intermediate frames entirely --
        # both in the window construction AND the loss, unlike just slicing
        # a multi-step prediction after the fact.
        self.pred_offsets = list(pred_offsets) if pred_offsets is not None else list(range(1, pred_len + 1))
        self.pred_len = len(self.pred_offsets)
        self.window_len = clip_len + max(self.pred_offsets)

        self.bags = []
        self.index = []
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


class MixedWindowDataset(Dataset):
    """Generalizes ChatWindowDataset: `split_bags` are time-split by
    train_ratio (front=train, back=test, e.g. chat); `full_train_bags` are
    used whole for training only (e.g. G1+G2); `full_test_bags` are used
    whole for testing only (e.g. G3's game3-6). Same (input, target) format
    as ChatWindowDataset."""

    def __init__(self, split: str, clip_len: int = 10, pred_len: int = 4, sm_ratio: float = 0.5,
                 train_ratio: float = TRAIN_RATIO, split_bags: list[str] = CHAT_BAGS,
                 full_train_bags: list[str] = (), full_test_bags: list[str] = (),
                 pred_offsets: list[int] | None = None):
        assert split in ("train", "test")
        self.clip_len = clip_len
        self.sm_ratio = sm_ratio
        self.pred_offsets = list(pred_offsets) if pred_offsets is not None else list(range(1, pred_len + 1))
        self.pred_len = len(self.pred_offsets)
        self.window_len = clip_len + max(self.pred_offsets)

        self.bags = []
        self.index = []

        def add_bag(name: str, lo: int, hi: int):
            d = np.load(DATA_DIR / f"{name}.npz")
            sm_exp = exp_transform(d["soundmap"].astype(np.float32))
            gray = d["gray_camimg"].astype(np.float32) / 255.0
            bag_idx = len(self.bags)
            self.bags.append({"name": name, "sm_exp": sm_exp, "gray": gray})
            for start in range(max(lo, 0), max(hi, 0)):
                self.index.append((bag_idx, start))

        for name in split_bags:
            d = np.load(DATA_DIR / f"{name}.npz")
            n = d["soundmap"].shape[0]
            train_num = int(n * train_ratio)
            lo, hi = (0, train_num - self.window_len + 1) if split == "train" \
                else (train_num, n - self.window_len + 1)
            add_bag(name, lo, hi)

        extra_bags = full_train_bags if split == "train" else full_test_bags
        for name in extra_bags:
            n = np.load(DATA_DIR / f"{name}.npz")["soundmap"].shape[0]
            add_bag(name, 0, n - self.window_len + 1)

    def __len__(self) -> int:
        return len(self.index)

    __getitem__ = ChatWindowDataset.__getitem__


def make_datasets(clip_len: int = 10, pred_len: int = 4, sm_ratio: float = 0.5,
                   dataset: str = "chat", pred_offsets: list[int] | None = None):
    """dataset='chat' (default, every prior eighth-run experiment): chat-only,
    per-bag 90/10 time split. dataset='chat_g1g2_g3': chat (same 90/10 split)
    + WordWolfExp G1+G2 (full, training only) + G3's game3-6 (full, test
    only) -- see MixedWindowDataset / G1_BAGS / G2_BAGS / G3_TEST_BAGS.
    pred_offsets overrides pred_len's implicit 1..pred_len target ticks (see
    ChatWindowDataset docstring) -- e.g. [2] for a single "+1s @2Hz" target."""
    if dataset == "chat":
        train_ds = ChatWindowDataset("train", clip_len=clip_len, pred_len=pred_len, sm_ratio=sm_ratio,
                                      pred_offsets=pred_offsets)
        test_ds = ChatWindowDataset("test", clip_len=clip_len, pred_len=pred_len, sm_ratio=sm_ratio,
                                     pred_offsets=pred_offsets)
        return train_ds, test_ds
    if dataset == "chat_g1g2_g3":
        common = dict(clip_len=clip_len, pred_len=pred_len, sm_ratio=sm_ratio, pred_offsets=pred_offsets,
                      full_train_bags=G1_BAGS + G2_BAGS, full_test_bags=G3_TEST_BAGS)
        train_ds = MixedWindowDataset("train", **common)
        test_ds = MixedWindowDataset("test", **common)
        return train_ds, test_ds
    raise ValueError(dataset)
