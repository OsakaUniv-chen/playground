# 1bit-vs-pytorch — FFT beamformer vs 1-bit XOR generator

Head-to-head comparison of the **1-bit XOR** sound-map generator against the
**FFT / pytorch** beamformer, on real bags and on synthetic point sources.

- **1-bit** generator: [`../../generator-1bit/onebit_soundmap.py`](../../generator-1bit/onebit_soundmap.py)
  (`OneBitSoundMapAPI`, CPU-only). Its full design + validation write-up lives in
  that folder's README.
- **FFT / pytorch** reference: [`../../generator-pytorch/new_soundmap_api.py`](../../generator-pytorch/new_soundmap_api.py)
  (`NewSoundMapAPI`) — the same pytorch generator the `acoular-vs-pytorch/`
  comparison uses. (It produces bit-identical maps to the older
  `video-generator/beamform_soundmap.py` this comparison originally referenced —
  same grid, band, window and mic geometry — so the swap doesn't change any result.)
- shared helpers (bag I/O, 4-label pipeline): `../utils.py`.

The scripts prepend `../../generator-pytorch`, `../../generator-1bit`, and `..`
(for `utils`) to `sys.path`, so run them from this folder.

## Scripts

| file | purpose |
|---|---|
| `compare_video.py` | real-bag side-by-side video: left = FFT beamformer (GPU), right = 1-bit (CPU) |
| `compare-generator.mp4` | rendered output on bag `G11_game4_DoA` |
| `analyze_disagreement.py` | label-only pass over a whole bag: agreement by VAD state / confusion / margins (fast, no video) |
| `save_disagreement_frames.py` | one annotated PNG per disagreeing tick → `results/disagreement_frames/…` |
| `validate_synthetic.py` | synthetic point-source correctness/precision check vs the FFT beamformer (no bag) |
| `make_comparison_figure.py` | renders `results/comparison.png` from the synthetic tests |

```bash
python compare_video.py --bag G11_game4_DoA   # real-data video: pytorch on GPU, 1-bit on CPU
python validate_synthetic.py                  # synthetic precision check
python make_comparison_figure.py
```

Run inside the `wolf` virtualenv (numpy/scipy/cv2; torch only because the FFT
reference generator uses it — `onebit_soundmap.py` never imports torch).
`compare_video.py` reads bags from `/media/chen/Extreme SSD/PSSPData/WordWolfExp`
(post-2026-07 PSSPData reorg — see `train-pssp/CONTEXT.md`).

## Headline

Over 40s of `G11_game4_DoA`: **142/160 ticks (88.75%) label agreement**; PYTORCH
(CUDA) ~5.6 ms/map vs 1-BIT (CPU) ~13.6 ms/map, and 1-BIT's CPU path (~15.7 ms)
actually beats PYTORCH's own CPU path (~18.4 ms). Full analysis — the all-pairs
boundary-flip fix, the bit-packing speed recovery, precision-vs-SNR sweeps — is
in [`../../generator-1bit/README.md`](../../generator-1bit/README.md).
