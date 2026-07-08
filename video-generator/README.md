# video-generator

Self-contained sound-map QC video generator, split out of `analysis2/code`.
Everything this folder needs lives inside it — no imports reach into
`analysis2/` or any other folder in the repo.

| file | purpose |
|---|---|
| `bag2video.py` | render one hardcoded bag (`BAG_PATH` + `BAG_NAME` at the top of the file) |
| `bag2video_all_bag.py` | render a hardcoded list of bags (`BAG_PATH` + `BAG_NAMES` at the top of the file) |
| `bag_io.py` | sqlite ROS2 bag reader + hand-written CDR decoders |
| `room1_vad.py` | silero VAD gate for the room1 mic, plus the QC-video VAD strip / label-bar renderer |
| `labeling.py` | sound-map mask / transform / colorize / 4-label extraction |
| `beamform_soundmap.py` | torch-based frequency-domain beamforming sound-map generator (`SoundMapGenerator` + `SoundMapAPI`), 16-mic geometry embedded as `MIC_POSITIONS` — no xml file, no acoular |
| `results/qc_video/` | rendered `*_sm_qc.mp4` output |

Uses a torch-based (no acoular) sound-map generator — see
`generator-compare/` for the validation that it's a harmless, ~7-8x faster
drop-in for the old acoular generator still used in `analysis2/`.

To point at a different bag, edit the `BAG_PATH`/`BAG_NAME` (or `BAG_NAMES`)
constants directly in the script — these are intentionally hardcoded rather
than passed as CLI args.

```bash
OPENBLAS_NUM_THREADS=1 python bag2video.py                  # single hardcoded bag
OPENBLAS_NUM_THREADS=1 python bag2video_all_bag.py --limit 5  # first 5 of the hardcoded list
```

No acoular / numpy<2 constraint (see `requirements.txt`) — run inside the
`wolf` virtualenv.

## Benchmark: one sound-map tick

Time to generate a single (64,64) sound map from one real 160-message GT audio
window (bag `G11_game4_DoA`), warmed up, averaged over multiple calls. Both
generators produce the same output given the same input (see
`generator-compare/report.md`), so this is purely a speed comparison.

| generator | device | ms/call | max throughput |
|---|---|---|---|
| **new** (`beamform_soundmap.SoundMapAPI`, this folder) | CPU | 17.7 ms | ~56 Hz |
| **new** (`beamform_soundmap.SoundMapAPI`, this folder) | CUDA | 4.4 ms | ~226 Hz |
| **old** (`generator-compare/soundmap_api.SoundMapAPI`, acoular) | CPU | 183.8 ms | ~5.4 Hz |

The old (acoular `BeamformerBase`) generator has **no GPU path** — it imports
`torch` only for an `isinstance` check, all beamforming runs on
numpy/numba/CPU — so there is no "old generator, GPU" number to report.

Both numbers are far inside the 4 Hz (250 ms/tick) budget this pipeline needs,
but the new generator is ~10x faster than old-on-CPU, and ~42x faster than
old-on-CPU when the new generator runs on a GPU. One-time model-build cost
(mic geometry + grid + interpolator) is ~0.07-0.2s for the new generator;
the old generator pays a one-time numba JIT-compile cost of a few seconds on
first import (faster on repeat runs once numba's on-disk cache is warm).
