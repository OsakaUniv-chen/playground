"""Standalone check for PLAN.md Phase 0b: does DataLoader(shuffle=True)
actually reshuffle every epoch, or only once? Doesn't touch training -- just
mirrors what DataLoader does internally when shuffle=True (constructs a
RandomSampler once, then calls iter() on it fresh each epoch) and prints the
resulting index order for a few "epochs" so it can be eyeballed/diffed.

Run once, paste the result into CONTEXT.md as a confirmed fact, then forget
about it -- this is a one-time sanity check, not something to run per
training job.
"""
from __future__ import annotations

from torch.utils.data import RandomSampler

from dataset import ChatWindowDataset

N_EPOCHS = 4
N_SHOW = 12


def main():
    ds = ChatWindowDataset("train", clip_len=10)
    sampler = RandomSampler(ds)
    print(f"train windows: {len(ds)}")

    orders = []
    for epoch in range(1, N_EPOCHS + 1):
        order = list(iter(sampler))  # DataLoader does exactly this each epoch
        orders.append(order)
        print(f"epoch {epoch} first {N_SHOW} indices: {order[:N_SHOW]}")

    all_different = all(orders[i] != orders[i + 1] for i in range(len(orders) - 1))
    all_same_multiset = all(sorted(o) == sorted(orders[0]) for o in orders)
    print(f"\nconsecutive epochs all have different order: {all_different}")
    print(f"every epoch still covers the same index set (just reordered): {all_same_multiset}")
    if all_different and all_same_multiset:
        print("CONFIRMED: shuffle=True reshuffles a fresh permutation every epoch.")
    else:
        print("UNEXPECTED -- do not assume shuffling is correct, investigate.")


if __name__ == "__main__":
    main()
