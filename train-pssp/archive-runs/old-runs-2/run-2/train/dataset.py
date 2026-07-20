"""run-2 data loading: three domains mixed into one training pool
(owner's design, 2026-07-18, see PLAN.md "训练/评估集设计"):

  - chat (3 bags): per-bag TIME split, train_ratio=0.9 -- front 90% train,
    back 10% val. Same convention as run-1/exp4.
  - wordwolfexp (78 bags = 13 groups x 6): one group's game3~6 (4 bags) held
    out WHOLLY as test -- never seen in training, unlike chat's val which is
    a same-bag time split. Which group is a parameter (`ww_test_group`,
    default "G1") -- see `ww_split()` -- so a second held-out group can be
    swapped in to cross-check the first result isn't a fluke tied to G1
    specifically (owner's request, 2026-07-18 Phase 2).
  - grpmtg (45 GRP_meeting bags): MTG_TRAIN_BAGS (44 bags) wholly for
    training; MTG_TEST_BAGS (1 bag, picked arbitrarily) wholly held out.
  - other (OTHER_BAGS, derived = every train-data bag not in chat/wordwolfexp/
    grpmtg -- ATR_RIKEN_1F/3f, olab_0630, olab_rev_0630, Demonstration_Data,
    demo_data_0318, egoSAS, riken_3f, Testrun0420, etc, 157 bags as of
    2026-07-19): used WHOLLY for training, no held-out counterpart -- this is
    "use the full 283-bag pool" (owner's request, run-2 Phase 4), just for
    training volume/diversity. No dedicated eval scenario table for it (no
    natural held-out split defined yet); its windows still count toward the
    "总的" scenario's TRAIN row in report.py.

Domain windows are never mixed at eval time -- report.py scores each domain
separately (plus one explicit combined table), never a silent merged
average (see archive-runs/old-runs-1/CONTEXT.md fifth-run's lesson).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torch.utils.data import ConcatDataset, Dataset

_HERE = Path(__file__).resolve().parent
DATA_DIR = _HERE.parent.parent / "train-data"

CHAT_BAGS = ["chat_debate_exp1_topic1", "chat_debate_exp1_topic2", "chat_debate_exp1_topic3"]
CHAT_TRAIN_RATIO = 0.9

WW_ALL_BAGS = [
    "G10_game2_Video", "G10_game3_PSSP", "G10_game4_Tele", "G10_game5_DoA", "G10_game6_Random", "G10_interview",
    "G11_game2_Video", "G11_game3_Random", "G11_game4_DoA", "G11_game5_Tele", "G11_game6_PSSP", "G11_interview",
    "G12_game2_Video", "G12_game3_Tele", "G12_game4_PSSP", "G12_game5_Random", "G12_game6_DoA", "G12_interview",
    "G13_game2_Video", "G13_game3_DoA", "G13_game4_Random", "G13_game5_PSSP", "G13_game6_Tele", "G13_interview",
    "G1_game2_Video", "G1_game3_Tele", "G1_game4_PSSP", "G1_game5_DoA", "G1_game6_Random", "G1_interview",
    "G2_game2_Video", "G2_game3_PSSP", "G2_game4_DoA", "G2_game5_Tele", "G2_game6_Random", "G2_interview",
    "G3_game2_Video", "G3_game3_Tele", "G3_game4_DoA", "G3_game5_PSSP", "G3_game6_Random", "G3_interview",
    "G4_game2_Video", "G4_game3_DoA", "G4_game4_PSSP", "G4_game5_Tele", "G4_game6_Random", "G4_interview",
    "G5_game2_Video", "G5_game3_Random", "G5_game4_Tele", "G5_game5_PSSP", "G5_game6_DoA", "G5_interview",
    "G6_game2_Video", "G6_game3_Tele", "G6_game4_Random", "G6_game5_PSSP", "G6_game6_DoA", "G6_interview",
    "G7_game2_Video", "G7_game3_DoA", "G7_game4_Tele", "G7_game5_Random", "G7_game6_PSSP", "G7_interview",
    "G8_game2_Video", "G8_game3_PSSP", "G8_game4_Random", "G8_game5_Tele", "G8_game6_DoA", "G8_interview",
    "G9_game2_Video", "G9_game3_Random", "G9_game4_DoA", "G9_game5_PSSP", "G9_game6_Tele", "G9_interview",
]
assert len(WW_ALL_BAGS) == 78
DEFAULT_WW_TEST_GROUP = "G1"


def ww_split(test_group: str = DEFAULT_WW_TEST_GROUP) -> tuple[list[str], list[str]]:
    """Returns (train_bags, test_bags): test_bags = `test_group`'s
    game3/4/5/6 (4 bags, e.g. G1_game3_Tele..G1_game6_Random); train_bags =
    every other WordWolfExp bag (74), INCLUDING test_group's own
    game2_Video/interview."""
    test_bags = [b for b in WW_ALL_BAGS
                 if b.split("_")[0] == test_group and b.split("_")[1] in ("game3", "game4", "game5", "game6")]
    assert len(test_bags) == 4, f"expected 4 game3~6 bags for group {test_group}, found {len(test_bags)}"
    train_bags = [b for b in WW_ALL_BAGS if b not in test_bags]
    return train_bags, test_bags

MTG_TEST_BAGS = ["GRP_meeting_2025-01-16-13_08_04"]  # picked arbitrarily, no special reason (owner's "随便选")
MTG_TRAIN_BAGS = [
    "GRP_meeting_2025-01-16-13_56_44", "GRP_meeting_2025-07-17-13_06_49", "GRP_meeting_2025-07-17-15_16_21",
    "GRP_meeting_2025-07-24-15_06_03", "GRP_meeting_2025-09-11-13_15_48", "GRP_meeting_2025-12-17-16_39_30",
    "GRP_meeting_2025-12-18-13_05_13", "GRP_meeting_2025-12-18-13_15_19", "GRP_meeting_2025-12-18-13_25_25",
    "GRP_meeting_2025-12-18-13_35_31", "GRP_meeting_2025-12-18-13_45_37", "GRP_meeting_2026-04-23-13_13_44",
    "GRP_meeting_2026-04-23-13_23_50", "GRP_meeting_2026-04-23-13_33_56", "GRP_meeting_2026-04-23-13_44_02",
    "GRP_meeting_2026-04-23-13_54_08", "GRP_meeting_2026-04-23-14_04_14", "GRP_meeting_2026-04-23-14_14_20",
    "GRP_meeting_2026-04-23-14_27_07", "GRP_meeting_2026-04-23-14_37_22", "GRP_meeting_2026-04-30-13_12_49",
    "GRP_meeting_2026-04-30-13_13_16", "GRP_meeting_2026-04-30-13_22_55", "GRP_meeting_2026-04-30-13_23_22",
    "GRP_meeting_2026-04-30-13_33_01", "GRP_meeting_2026-04-30-13_33_28", "GRP_meeting_2026-04-30-13_43_07",
    "GRP_meeting_2026-04-30-13_43_34", "GRP_meeting_2026-04-30-13_53_13", "GRP_meeting_2026-04-30-13_53_40",
    "GRP_meeting_2026-04-30-14_03_19", "GRP_meeting_2026-04-30-14_03_46", "GRP_meeting_2026-04-30-14_13_25",
    "GRP_meeting_2026-04-30-14_13_52", "GRP_meeting_2026-04-30-14_23_31", "GRP_meeting_2026-04-30-14_23_58",
    "GRP_meeting_2026-04-30-14_33_37", "GRP_meeting_2026-04-30-14_34_04", "GRP_meeting_2026-04-30-14_43_43",
    "GRP_meeting_2026-04-30-14_44_10", "GRP_meeting_2026-04-30-14_53_49", "GRP_meeting_2026-04-30-14_54_17",
    "GRP_meeting_2026-04-30-15_03_55", "GRP_meeting_2026-04-30-15_04_23",
]
assert len(MTG_TRAIN_BAGS) == 44 and len(MTG_TEST_BAGS) == 1

# every train-data bag not already claimed by chat/wordwolfexp/grpmtg -- see
# module docstring point 4. Derived (not hardcoded) since it's "everything
# else": a 157-name literal list would be unwieldy and this pool is meant to
# track train-data/ as-is for a "use it all" run, not a curated fixed set.
_claimed = set(CHAT_BAGS) | set(WW_ALL_BAGS) | set(MTG_TRAIN_BAGS) | set(MTG_TEST_BAGS)
OTHER_BAGS = sorted(p.stem for p in DATA_DIR.glob("*.npz") if p.stem not in _claimed)

# 2026-07-20 (owner's request): held-out split carved out of OTHER_BAGS to
# directly test whether the 157 Phase-4 bags carry any generalization value
# -- ATR_RIKEN_1F (largest single source, 49 bags) held out WHOLLY as an
# unseen-domain test; the remaining 108 OTHER_BAGS are available for
# training via make_datasets(atr1f_holdout=True). See PLAN.md.
ATR1F_TEST_BAGS = sorted(b for b in OTHER_BAGS if b.startswith("ATR_RIKEN_1F"))
OTHER_TRAIN_BAGS_EX_ATR1F = sorted(b for b in OTHER_BAGS if b not in ATR1F_TEST_BAGS)


def exp_transform(sm: np.ndarray) -> np.ndarray:
    """Per-frame exp(x - x.max()) over the last two axes -- same as every
    other run's dataset.py."""
    m = sm.max(axis=(-2, -1), keepdims=True)
    out = np.zeros_like(sm, dtype=np.float32)
    valid = (m > 0).squeeze(axis=(-2, -1))
    out[valid] = np.exp(sm[valid] - m[valid])
    return out


def _load_bag(name: str) -> dict:
    d = np.load(DATA_DIR / f"{name}.npz")
    return {"name": name, "sm_exp": exp_transform(d["soundmap"].astype(np.float32)),
            "gray": d["gray_camimg"].astype(np.float32) / 255.0}


def _getitem_common(self, idx: int):
    bag_idx, start = self.index[idx]
    bag = self.bags[bag_idx]
    sm_exp = bag["sm_exp"][start:start + self.window_len]
    gray = bag["gray"][start:start + self.window_len]
    hist_sm, hist_gray = sm_exp[:self.clip_len], gray[:self.clip_len]
    target_sm = sm_exp[self.clip_len:self.window_len]

    r = self.sm_ratio
    sm_channel = hist_sm if r == 1.0 else r * hist_sm + (1.0 - r) * hist_gray
    x = np.stack([sm_channel, hist_gray], axis=1)
    return (
        torch.from_numpy(x.astype(np.float32)),
        torch.from_numpy(target_sm[:, None].astype(np.float32)),
    )


class TimeSplitWindowDataset(Dataset):
    """chat-style: each bag's timeline split front/back by train_ratio.
    split='train' -> front train_ratio fraction; split='val' -> the rest."""

    def __init__(self, bags: list[str], split: str, clip_len: int = 10, pred_len: int = 4,
                 sm_ratio: float = 0.5, train_ratio: float = CHAT_TRAIN_RATIO):
        assert split in ("train", "val")
        self.clip_len, self.pred_len, self.sm_ratio = clip_len, pred_len, sm_ratio
        self.window_len = clip_len + pred_len

        self.bags, self.index = [], []
        for bag_idx, name in enumerate(bags):
            bag = _load_bag(name)
            self.bags.append(bag)
            n = bag["sm_exp"].shape[0]
            train_num = int(n * train_ratio)
            lo, hi = (0, train_num - self.window_len + 1) if split == "train" \
                else (train_num, n - self.window_len + 1)
            for start in range(max(lo, 0), max(hi, 0)):
                self.index.append((bag_idx, start))

    def __len__(self) -> int:
        return len(self.index)

    __getitem__ = _getitem_common


class FullBagWindowDataset(Dataset):
    """wordwolfexp/grpmtg-style: every bag in `bags` used in full (no
    per-bag split) -- the split between train/test happens at the BAG level
    (which list a bag is placed in), not within a bag's timeline."""

    def __init__(self, bags: list[str], clip_len: int = 10, pred_len: int = 4, sm_ratio: float = 0.5):
        self.clip_len, self.pred_len, self.sm_ratio = clip_len, pred_len, sm_ratio
        self.window_len = clip_len + pred_len

        self.bags, self.index = [], []
        for bag_idx, name in enumerate(bags):
            bag = _load_bag(name)
            self.bags.append(bag)
            n = bag["sm_exp"].shape[0]
            for start in range(max(n - self.window_len + 1, 0)):
                self.index.append((bag_idx, start))

    def __len__(self) -> int:
        return len(self.index)

    __getitem__ = _getitem_common


def make_datasets(clip_len: int = 10, pred_len: int = 4, sm_ratio: float = 0.5,
                   ww_test_group: str = DEFAULT_WW_TEST_GROUP, use_full_pool: bool = False,
                   atr1f_holdout: bool = False) -> dict:
    """Returns a dict: combined_train (ConcatDataset, for the actual training
    loop) plus each domain's train/val-or-test set separately (for
    report.py's per-domain tables). `ww_test_group` picks which WordWolfExp
    group's game3~6 is held out (see ww_split()). `use_full_pool=True` adds
    OTHER_BAGS (the remaining ~157 train-data bags outside chat/wordwolfexp/
    grpmtg) into combined_train -- Phase 4's "use the full 283-bag pool"
    (default False so Phase 1~3's 121-bag runs stay reproducible as-is).
    `atr1f_holdout=True` instead adds OTHER_TRAIN_BAGS_EX_ATR1F (108 bags,
    everything in OTHER_BAGS except ATR_RIKEN_1F) into combined_train, WHOLLY
    keeping ATR1F_TEST_BAGS unseen -- mutually exclusive with use_full_pool.
    ATR1F_TEST_BAGS itself isn't returned here; report.py's
    --extra-eval-domain evaluates it directly against any checkpoint,
    trained with this flag or not, so the same eval path scores both the
    zero-shot baseline (no OTHER_BAGS at all) and this run."""
    assert not (use_full_pool and atr1f_holdout), "use_full_pool and atr1f_holdout are mutually exclusive"
    common = dict(clip_len=clip_len, pred_len=pred_len, sm_ratio=sm_ratio)

    chat_train = TimeSplitWindowDataset(CHAT_BAGS, "train", train_ratio=CHAT_TRAIN_RATIO, **common)
    chat_val = TimeSplitWindowDataset(CHAT_BAGS, "val", train_ratio=CHAT_TRAIN_RATIO, **common)
    ww_train_bags, ww_test_bags = ww_split(ww_test_group)
    ww_train = FullBagWindowDataset(ww_train_bags, **common)
    ww_test = FullBagWindowDataset(ww_test_bags, **common)
    mtg_train = FullBagWindowDataset(MTG_TRAIN_BAGS, **common)
    mtg_test = FullBagWindowDataset(MTG_TEST_BAGS, **common)

    train_sets = [chat_train, ww_train, mtg_train]
    result = {
        "combined_train": None,  # filled below, after optionally adding other_train
        "chat_train": chat_train, "chat_val": chat_val,
        "wordwolfexp_train": ww_train, "wordwolfexp_test": ww_test,
        "grpmtg_train": mtg_train, "grpmtg_test": mtg_test,
    }
    if use_full_pool:
        other_train = FullBagWindowDataset(OTHER_BAGS, **common)
        train_sets.append(other_train)
        result["other_train"] = other_train
    elif atr1f_holdout:
        other_train = FullBagWindowDataset(OTHER_TRAIN_BAGS_EX_ATR1F, **common)
        train_sets.append(other_train)
        result["other_train"] = other_train
    result["combined_train"] = ConcatDataset(train_sets)
    return result
