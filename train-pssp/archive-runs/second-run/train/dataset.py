"""Training data loading: clip_len/pred_len windowing over train-pssp/train-data/
npz files, explicit hardcoded train/test bag lists, exp(x-max) normalization.

train-data/ is a shared pool (see train-pssp/preprocessing/build_dataset.py),
not scoped to any one run -- both first-run and second-run point their
DATA_DIR at it. Only this file/simvp.py/train.py are second-run's own copy;
the raw npz data itself is shared, never duplicated per run.

Design decisions (see train-pssp/CONTEXT.md "训练数据加载逻辑设计" for the full
reasoning, this is just the summary):
  - TRAIN_BAGS/TEST_BAGS below are hardcoded on purpose, not auto-split by
    parsing the group number out of each bag's filename -- explicit lists are
    easier to eyeball/audit than a regex-driven rule, and a typo in a filename
    can't silently misroute a bag into the wrong split. `group_of` is still
    used, but only to ASSERT the two lists don't share a WordWolf group
    (G1..G13), not to decide the split itself.
  - The split is by whole group, not by time-position within a file. A
    file-internal time split doesn't leak frames across the boundary (the old
    code got that part right), but train and test end up being adjacent
    seconds of the SAME conversation/speakers/room -- group-level split avoids
    that.
  - Dense sliding windows (every start frame, not just every clip_len-th) are
    kept; this is oversampling, not augmentation, and is fine as long as splits
    are done at the group level so no window crosses train/test.
  - exp(x-max) is applied ONCE per bag when a Dataset is built (not per
    __getitem__ call) -- this is the settled normalization (see CONTEXT.md),
    baked in for efficiency. sm_ratio blending stays in __getitem__ since it's
    still an open hyperparameter (the old exp1~4 checkpoints were an ablation
    over exactly this).
  - Raw npz on disk (produced by build_dataset.py) is untouched/unmodified by
    this module -- re-deriving a different transform never requires re-running
    the expensive rosbag extraction.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset

BAG_RE = re.compile(r"^G(?P<group>\d+)_")

# Explicit train/test bag lists -- update by hand as train-data/ grows.
# Held-out groups: G2 and G6 (entirely, all sessions) -- see CONTEXT.md for why
# a whole-group holdout instead of a within-file time split.
# Frame counts as of 2026-07-09 (each frame = one 0.5s tick; update these
# comments when TRAIN_BAGS/TEST_BAGS change). Expanded ~4x (2026-07-09): G1 (was
# the whole train set) plus new groups G3, G4; test G2 plus new group G6.
#   G1_game3_Tele     378 frames
#   G1_game4_PSSP     392 frames
#   G1_game5_DoA      386 frames
#   G1_interview     1472 frames   (G1 subtotal  2628)
#   G3_game2_Video    385 frames
#   G3_game3_Tele     375 frames
#   G3_game4_DoA      391 frames
#   G3_game5_PSSP     390 frames
#   G3_game6_Random   378 frames
#   G3_interview     1457 frames   (G3 subtotal  3376)
#   G4_game2_Video    385 frames
#   G4_game3_DoA      381 frames
#   G4_game4_PSSP     390 frames
#   G4_game5_Tele     380 frames
#   G4_game6_Random   377 frames
#   G4_interview     1005 frames   (G4 subtotal  2918)
#   TRAIN_BAGS total 8922 frames 
TRAIN_BAGS = [
    "G1_game3_Tele",
    "G1_game4_PSSP",
    "G1_game5_DoA",
    "G1_interview",
    "G3_game2_Video",
    "G3_game3_Tele",
    "G3_game4_DoA",
    "G3_game5_PSSP",
    "G3_game6_Random",
    "G3_interview",
    "G4_game2_Video",
    "G4_game3_DoA",
    "G4_game4_PSSP",
    "G4_game5_Tele",
    "G4_game6_Random",
    "G4_interview",
]
#   G2_game3_PSSP     393 frames
#   G2_game4_DoA      385 frames   (G2 subtotal   778)
#   G6_game2_Video    402 frames
#   G6_game3_Tele     378 frames
#   G6_game4_Random   378 frames
#   G6_game5_PSSP     391 frames
#   G6_game6_DoA      377 frames
#   G6_interview      922 frames   (G6 subtotal  2848)
#   TEST_BAGS total  3626 frames
TEST_BAGS = [
    "G2_game3_PSSP",
    "G2_game4_DoA",
    "G6_game2_Video",
    "G6_game3_Tele",
    "G6_game4_Random",
    "G6_game5_PSSP",
    "G6_game6_DoA",
    "G6_interview",
]


def group_of(bag_name: str) -> int:
    m = BAG_RE.match(bag_name)
    if not m:
        raise ValueError(f"can't parse group from bag name: {bag_name!r}")
    return int(m["group"])


def assert_no_group_overlap(train_bags: list[str], test_bags: list[str]) -> None:
    train_groups = {group_of(b) for b in train_bags}
    test_groups = {group_of(b) for b in test_bags}
    overlap = train_groups & test_groups
    if overlap:
        raise ValueError(f"TRAIN_BAGS and TEST_BAGS share group(s) {overlap} -- fix the hardcoded lists")


def unassigned_bags(data_dir: Path, train_bags: list[str] = TRAIN_BAGS, test_bags: list[str] = TEST_BAGS) -> list[str]:
    """npz files present in data_dir but not listed in either split -- a hint
    that TRAIN_BAGS/TEST_BAGS need updating after a new extraction."""
    assigned = set(train_bags) | set(test_bags)
    present = {p.stem for p in Path(data_dir).glob("*.npz")}
    return sorted(present - assigned)


def exp_transform(sm: np.ndarray) -> np.ndarray:
    """Per-frame exp(x - x.max()) over the last two axes, matching the live
    deployment's transform_sound_map(). sm: (...,H,W) raw values. A frame whose
    max is <= 0 (silent) is left at 0 rather than turned into all-1s."""
    m = sm.max(axis=(-2, -1), keepdims=True)
    out = np.zeros_like(sm, dtype=np.float32)
    valid = (m > 0).squeeze(axis=(-2, -1))
    out[valid] = np.exp(sm[valid] - m[valid])
    return out


class PSSPWindowDataset(Dataset):
    """clip_len/pred_len sliding windows over a fixed list of bag npz files.
    Windows never cross a bag boundary. Returns (input, target):
      input:  (clip_len, 2, H, W) float32 -- ch0 = sm_ratio-blended exp(sm),
              ch1 = gray/255.
      target: (pred_len, 1, H, W) float32 -- exp(sm), matches SimVP's output
              shape and the sigmoid-bounded [0,1] range it predicts into.
    """

    def __init__(self, bag_paths: list[Path], clip_len: int = 10, pred_len: int = 4, sm_ratio: float = 0.5):
        self.clip_len = clip_len
        self.pred_len = pred_len
        self.sm_ratio = sm_ratio
        self.window_len = clip_len + pred_len

        self.bags = []   # [{"name": str, "sm_exp": (N,H,W) f32, "gray": (N,H,W) f32}, ...]
        self.index = []  # [(bag_idx, start_frame), ...]

        for bag_idx, path in enumerate(bag_paths):
            d = np.load(path)
            sm_raw = d["soundmap"].astype(np.float32)             # (N,H,W) raw [0,160]
            gray = d["gray_camimg"].astype(np.float32) / 255.0    # (N,H,W) [0,1]
            sm_exp = exp_transform(sm_raw)                        # (N,H,W) (0,1], computed once here

            n = sm_exp.shape[0]
            self.bags.append({"name": path.stem, "sm_exp": sm_exp, "gray": gray})
            for start in range(0, n - self.window_len + 1):
                self.index.append((bag_idx, start))

    def __len__(self) -> int:
        return len(self.index)

    def bag_names(self) -> list[str]:
        return [b["name"] for b in self.bags]

    def __getitem__(self, idx: int):
        bag_idx, start = self.index[idx]
        bag = self.bags[bag_idx]
        sm_exp = bag["sm_exp"][start:start + self.window_len]
        gray = bag["gray"][start:start + self.window_len]

        hist_sm, hist_gray = sm_exp[:self.clip_len], gray[:self.clip_len]
        target_sm = sm_exp[self.clip_len:]  # (pred_len,H,W)

        sm_ratio = self.sm_ratio
        sm_channel = hist_sm if sm_ratio == 1.0 else sm_ratio * hist_sm + (1.0 - sm_ratio) * hist_gray
        x = np.stack([sm_channel, hist_gray], axis=1)  # (clip_len,2,H,W)

        return (
            torch.from_numpy(x.astype(np.float32)),
            torch.from_numpy(target_sm[:, None].astype(np.float32)),  # (pred_len,1,H,W)
        )


def make_datasets(data_dir: Path, train_bags: list[str] = TRAIN_BAGS, test_bags: list[str] = TEST_BAGS,
                   clip_len: int = 10, pred_len: int = 4, sm_ratio: float = 0.5) -> tuple[PSSPWindowDataset, PSSPWindowDataset]:
    assert_no_group_overlap(train_bags, test_bags)
    data_dir = Path(data_dir)
    train_paths = [data_dir / f"{b}.npz" for b in train_bags]
    test_paths = [data_dir / f"{b}.npz" for b in test_bags]
    train_ds = PSSPWindowDataset(train_paths, clip_len=clip_len, pred_len=pred_len, sm_ratio=sm_ratio)
    test_ds = PSSPWindowDataset(test_paths, clip_len=clip_len, pred_len=pred_len, sm_ratio=sm_ratio)
    return train_ds, test_ds
