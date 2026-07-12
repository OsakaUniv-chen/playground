"""Training data loading: clip_len/pred_len windowing over train-pssp/train-data/
npz files, explicit hardcoded train/test bag lists, exp(x-max) normalization.

fourth-run DELIBERATELY reuses this file byte-for-byte from third-run (same
TRAIN_BAGS/TEST_BAGS, same clip_len=10/pred_len=4 windows) -- this run's
single variable is the model architecture (DMVFN vs SimVP, see
train/dmvfn.py), not the data. DMVFN itself only ever looks at the last 2
frames of whatever window PSSPWindowDataset builds (see dmvfn.py's module
docstring), so clip_len doesn't affect its receptive field either way --
keeping it at 10 (unchanged from third-run) just keeps the exact same
train/test windows/sample counts for a clean apples-to-apples comparison.

third-run's whole purpose (see train-pssp/CONTEXT.md): first-run/second-run's
diagnostics found the loss function wasn't why localization accuracy was
poor -- all 3 loss conditions badly under-tracked true position variation,
and even the best (MSE) sat below the naive "last-input-position predicts
next" correlation ceiling. The open question was whether more/more-diverse
training data would move that ceiling. This run tests exactly that, holding
the loss function fixed at plain MSE (first-run's setup) and changing only
the data:
  - TRAIN_BAGS: ALL 78 WordWolfExp bags (first-run/second-run only used a
    16-bag subset, G1/G3/G4) PLUS chat's first two debate sessions
    (chat_debate_exp1_topic1/2) -- a genuinely different scene/setup
    (two-person debate, not the WordWolfExp game rooms), added specifically
    to test generalization beyond WordWolfExp.
  - TEST_BAGS: chat's third debate session (chat_debate_exp1_topic3) only.
    Not a WordWolfExp holdout this time -- since ALL WordWolfExp data is now
    in TRAIN_BAGS, testing on a same-series-but-unseen chat session is a
    stronger generalization check than another WordWolfExp group would be.
    The old model (access-model/exp4) is also re-evaluated on this same test
    set for a fair comparison (see evaluation/compare_old_new.py).

Design decisions carried over unchanged from first-run (see CONTEXT.md
"训练数据加载逻辑设计" for the full reasoning):
  - TRAIN_BAGS/TEST_BAGS are hardcoded on purpose, not auto-split by
    scanning train-data/ -- an explicit list can't silently pick up a future
    extraction. "All 78 WordWolfExp bags" is itself a deliberate choice
    already made (see CONTEXT.md), not a live glob.
  - Dense sliding windows (every start frame) are kept -- oversampling, not
    augmentation, harmless given a clean split.
  - exp(x-max) is applied ONCE per bag when a Dataset is built (not per
    __getitem__ call).
  - Raw npz on disk is untouched/unmodified by this module.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset

WORDWOLF_RE = re.compile(r"^G(?P<group>\d+)_")

# All 78 WordWolfExp bags currently in train-data/ (13 groups G1..G13, 6
# sessions each) -- see train-pssp/CONTEXT.md/preprocessing/DATA_REPORT.md.
# 39118 ticks total. No WordWolfExp holdout this run (see module docstring).
WORDWOLF_BAGS = [
    "G1_game2_Video", "G1_game3_Tele", "G1_game4_PSSP", "G1_game5_DoA", "G1_game6_Random", "G1_interview",
    "G2_game2_Video", "G2_game3_PSSP", "G2_game4_DoA", "G2_game5_Tele", "G2_game6_Random", "G2_interview",
    "G3_game2_Video", "G3_game3_Tele", "G3_game4_DoA", "G3_game5_PSSP", "G3_game6_Random", "G3_interview",
    "G4_game2_Video", "G4_game3_DoA", "G4_game4_PSSP", "G4_game5_Tele", "G4_game6_Random", "G4_interview",
    "G5_game2_Video", "G5_game3_Random", "G5_game4_Tele", "G5_game5_PSSP", "G5_game6_DoA", "G5_interview",
    "G6_game2_Video", "G6_game3_Tele", "G6_game4_Random", "G6_game5_PSSP", "G6_game6_DoA", "G6_interview",
    "G7_game2_Video", "G7_game3_DoA", "G7_game4_Tele", "G7_game5_Random", "G7_game6_PSSP", "G7_interview",
    "G8_game2_Video", "G8_game3_PSSP", "G8_game4_Random", "G8_game5_Tele", "G8_game6_DoA", "G8_interview",
    "G9_game2_Video", "G9_game3_Random", "G9_game4_DoA", "G9_game5_PSSP", "G9_game6_Tele", "G9_interview",
    "G10_game2_Video", "G10_game3_PSSP", "G10_game4_Tele", "G10_game5_DoA", "G10_game6_Random", "G10_interview",
    "G11_game2_Video", "G11_game3_Random", "G11_game4_DoA", "G11_game5_Tele", "G11_game6_PSSP", "G11_interview",
    "G12_game2_Video", "G12_game3_Tele", "G12_game4_PSSP", "G12_game5_Random", "G12_game6_DoA", "G12_interview",
    "G13_game2_Video", "G13_game3_DoA", "G13_game4_Random", "G13_game5_PSSP", "G13_game6_Tele", "G13_interview",
]
assert len(WORDWOLF_BAGS) == 78

# chat/debate_exp1_topic{1,2} train, topic3 held out as the ONLY test bag.
# 2168 + 2167 ticks train, 2151 ticks test -- see module docstring for why.
CHAT_TRAIN_BAGS = ["chat_debate_exp1_topic1", "chat_debate_exp1_topic2"]
TEST_BAGS = ["chat_debate_exp1_topic3"]

TRAIN_BAGS = WORDWOLF_BAGS + CHAT_TRAIN_BAGS


def group_of(bag_name: str) -> str:
    """WordWolfExp bags group by their G-number (same conversation/room);
    every other bag (chat's debate sessions, etc.) is its own group -- each
    is already a disjoint recording, no further grouping needed."""
    m = WORDWOLF_RE.match(bag_name)
    return f"G{m['group']}" if m else bag_name


def assert_no_group_overlap(train_bags: list[str], test_bags: list[str]) -> None:
    train_groups = {group_of(b) for b in train_bags}
    test_groups = {group_of(b) for b in test_bags}
    overlap = train_groups & test_groups
    if overlap:
        raise ValueError(f"TRAIN_BAGS and TEST_BAGS share group(s) {overlap} -- fix the hardcoded lists")


def unassigned_bags(data_dir: Path, train_bags: list[str] = TRAIN_BAGS, test_bags: list[str] = TEST_BAGS) -> list[str]:
    """npz files present in data_dir but not listed in either split -- not
    necessarily a problem here (train-data/ deliberately holds a lot more
    than this run uses), just informational."""
    assigned = set(train_bags) | set(test_bags)
    present = {p.stem for p in Path(data_dir).glob("*.npz")}
    return sorted(present - assigned)


def exp_transform(sm: np.ndarray) -> np.ndarray:
    """Per-frame exp(x - x.max()) over the last two axes, matching the live
    deployment's transform_sound_map(). A frame whose max is <= 0 (silent) is
    left at 0 rather than turned into all-1s."""
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
