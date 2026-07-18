"""Training data loading: clip_len/pred_len windowing over train-pssp/train-data/
npz files, explicit hardcoded train/test bag lists, exp(x-max) normalization.

fifth-run's whole purpose (see train-pssp/CONTEXT.md): third-run found that
scaling WordWolfExp data alone (78 bags) didn't clearly beat exp4 (the old
model, trained on unknown but presumably more diverse data) on generalization,
raising the hypothesis that data DIVERSITY matters more than raw volume. That
was hard to test properly with only 2 non-WordWolf bags (chat topics 1/2)
available. The 2026-07-13 PSSPData reprocess (see DATA_REPORT.md) changed
that: ~278 bags across 15+ genuinely different collections/scenes are now in
train-data/. fifth-run holds architecture/loss/hyperparameters fixed at
third-run's settings and changes only the data, to see whether real diversity
(not just volume) moves the position-correlation ceiling that third-run
couldn't move.

  - TRAIN_BAGS: every train-data/*.npz bag as of the 2026-07-13 reprocess
    EXCEPT the 5 TEST_BAGS below (283 - 5 = 278 bags). Frozen into
    train_bags.txt at fifth-run creation time (generated once from
    train-data/index.csv) rather than live-globbed -- same reasoning as
    third-run's hardcoded list ("an explicit list can't silently pick up a
    future extraction"), just not hand-typed at this scale (278 names across
    15+ collections) -- see train_bags.txt itself for the frozen names.
  - TEST_BAGS: chat_debate_exp1_topic3 (same as third-run, for continuity)
    PLUS WordWolfExp G13's game3_DoA/game4_Random/game5_PSSP/game6_Tele.
    **Owner's explicit choice, accepting a real leakage risk**: G13_game2_Video
    and G13_interview stay in TRAIN_BAGS -- i.e. this is NOT a clean group
    holdout for G13 like every other WordWolfExp group gets. See
    KNOWN_GROUP_OVERLAP below and CONTEXT.md's fifth-run section for the
    tradeoff discussion. Interpret G13's test numbers with that in mind: the
    model may have seen the same room/people/conversation via the other two
    G13 bags, so this isn't as clean a generalization check as chat_topic3 is.

Design decisions carried over unchanged from first/third-run (see CONTEXT.md
"训练数据加载逻辑设计" for the full reasoning):
  - Dense sliding windows (every start frame) are kept -- oversampling, not
    augmentation, harmless given a clean split (modulo the G13 exception).
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

_HERE = Path(__file__).resolve().parent

WORDWOLF_RE = re.compile(r"^G(?P<group>\d+)_")

# chat_topic3 for continuity with third-run, plus G13's 4 numbered games --
# see module docstring for the deliberate G13 group-overlap exception.
TEST_BAGS = ["chat_debate_exp1_topic3", "G13_game3_DoA", "G13_game4_Random", "G13_game5_PSSP", "G13_game6_Tele"]

# access-model/access-model-train/utils_all_load.py's NPZLoader.get_index()
# confirms the OLD model (exp4) was trained with train_ratio=0.9 and
# exp_name=[] (its config's literal value, meaning "every npz under its
# npz_path", not a curated subset) -- i.e. for ANY bag that happened to be
# under that old npz_path, the FIRST 90% of its frames (time-ordered) went
# into exp4's own training set, only the LAST 10% held out as exp4's own
# test data. We can't confirm from this old, git-less codebase whether
# chat_debate_exp1_topic3 was actually present there at exp4's train time --
# but if it was, evaluating exp4 on the FULL bag would unfairly let it
# "remember" ~90% of these windows instead of genuinely predicting unseen
# data, while our new model has never seen ANY of this bag. Owner's call:
# trim chat_debate_exp1_topic3 to just its last 10% (matching exp4's own
# split exactly, so this portion is guaranteed unseen by exp4 regardless of
# whether it was in its npz_path) for the test set; G13's 4 bags are used in
# full since the owner confirmed WordWolfExp/G13 was never part of either
# model's training data, so no such contamination risk applies there.
TEST_BAG_MIN_START_FRAC = {"chat_debate_exp1_topic3": 0.9}

# Frozen snapshot of every other train-data/*.npz bag (283 total - 5 test =
# 278), generated once from index.csv at fifth-run creation time -- see
# module docstring.
TRAIN_BAGS = (_HERE / "train_bags.txt").read_text().split()
assert len(TRAIN_BAGS) == 278

# Owner's explicit, accepted-risk exception: G13_game2_Video/G13_interview
# stay in TRAIN while G13's other 4 bags are the test set (see module
# docstring). Every other group is still required to be clean.
KNOWN_GROUP_OVERLAP = {"G13"}

# 2026-07-15: seventh-run's full-278-bag run (sixth-run's chat-only winning
# recipe: sparse+lr=1e-3+noise_ratio augmentation) badly underperformed --
# early-stopped at epoch1, never improved. Leading suspect is epoch-level
# checkpointing being too coarse once a dataset is big enough that ONE
# nominal epoch already packs in a huge number of gradient steps (278 bags
# -> ~7318 batches/epoch vs chat's ~181). This subset -- WordWolfExp (78
# bags, G-prefixed) + chat's 2 training bags (topic1/2) -- is a deliberate
# middle scale (~1300ish batches/epoch, matches third-run's original data
# scope) to isolate whether dataset SIZE alone (not the newly-added
# diversity from GRP_meeting/ATR_teleoperation/etc.) is what's driving the
# problem. Same TEST_BAGS throughout for comparability.
WORDWOLF_AND_CHAT_TRAIN_BAGS = [b for b in TRAIN_BAGS if WORDWOLF_RE.match(b) or b.startswith("chat_")]


def group_of(bag_name: str) -> str:
    """WordWolfExp bags group by their G-number (same conversation/room);
    every other bag (chat's debate sessions, GRP_meeting sessions, etc.) is
    its own group -- each is already a disjoint recording, no further
    grouping needed."""
    m = WORDWOLF_RE.match(bag_name)
    return f"G{m['group']}" if m else bag_name


def assert_no_group_overlap(train_bags: list[str], test_bags: list[str]) -> None:
    train_groups = {group_of(b) for b in train_bags}
    test_groups = {group_of(b) for b in test_bags}
    overlap = (train_groups & test_groups) - KNOWN_GROUP_OVERLAP
    if overlap:
        raise ValueError(f"TRAIN_BAGS and TEST_BAGS share group(s) {overlap} -- fix the hardcoded lists")


def unassigned_bags(data_dir: Path, train_bags: list[str] = TRAIN_BAGS, test_bags: list[str] = TEST_BAGS) -> list[str]:
    """npz files present in data_dir but not listed in either split -- not
    necessarily a problem here, just informational."""
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

    def __init__(self, bag_paths: list[Path], clip_len: int = 10, pred_len: int = 4, sm_ratio: float = 0.5,
                 bag_min_start_frac: dict[str, float] | None = None):
        """bag_min_start_frac: optional {bag_name: frac} -- restricts that
        bag's windows to start >= frac * n_frames (e.g. 0.9 -> only windows
        entirely within the bag's last 10%). See TEST_BAG_MIN_START_FRAC's
        docstring for why chat_debate_exp1_topic3 needs this."""
        self.clip_len = clip_len
        self.pred_len = pred_len
        self.sm_ratio = sm_ratio
        self.window_len = clip_len + pred_len
        bag_min_start_frac = bag_min_start_frac or {}

        self.bags = []   # [{"name": str, "sm_exp": (N,H,W) f32, "gray": (N,H,W) f32}, ...]
        self.index = []  # [(bag_idx, start_frame), ...]

        for bag_idx, path in enumerate(bag_paths):
            d = np.load(path)
            sm_raw = d["soundmap"].astype(np.float32)             # (N,H,W) raw [0,160]
            gray = d["gray_camimg"].astype(np.float32) / 255.0    # (N,H,W) [0,1]
            sm_exp = exp_transform(sm_raw)                        # (N,H,W) (0,1], computed once here

            n = sm_exp.shape[0]
            self.bags.append({"name": path.stem, "sm_exp": sm_exp, "gray": gray})
            min_start = int(np.ceil(bag_min_start_frac.get(path.stem, 0.0) * n))
            for start in range(min_start, n - self.window_len + 1):
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
    test_ds = PSSPWindowDataset(test_paths, clip_len=clip_len, pred_len=pred_len, sm_ratio=sm_ratio,
                                 bag_min_start_frac=TEST_BAG_MIN_START_FRAC)
    return train_ds, test_ds
