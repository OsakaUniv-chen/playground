# analysis2/code — WP0 extracted pipeline

Self-contained reproduction of the live robot decision pipeline. **No imports
from the external SSD** — everything needed is vendored here.

## Layout

| File | Role |
|---|---|
| `soundmap/acoular/`, `soundmap/sound_map.py` | vendored acoular 24.05 ('old' generator) + mic xml |
| `pssp/simvp.py`, `pssp/utils_all_load.py`, `pssp/config_simvp_exp4.pt` | SimVP + **live** weights (exp4) |
| `soundmap_api.py` | `SoundMapAPI.generate(audio_chunks) -> (64,64)` sound map |
| `pssp_api.py` | `PsspAPI.predict(clip10) -> (4,64,64)` at +0.5/1.0/1.5/2.0 s (device-agnostic) |
| `labeling.py` | mask / transform / colorize / **4-label** extract7 / VAD gate / clip frame / HeadBoxProcessor |
| `head_box.py` | `HeadBoxAPI.detect(frame) -> [left,right]` (MediaPipe FaceDetection, head_node logic) |
| `head_orientation.py` | `HeadOrientationAPI.detect(img) -> (pitch,yaw,roll)` (MediaPipe FaceMesh + solvePnP) |
| `bag_io.py` | sqlite bag reader + hand-written CDR decoders (no rosbags dep) |
| `validate.py` | WP1 bag audit + WP2 reproduction gate |
| `extract.py` | WP3 per-bag tick extraction → `results/ticks/*.parquet` |
| `bag2video.py` | WP2 step-0 sound-map QC video |
| `analyses/wp4.py` | WP4 analyses (confusion / kappa / speech / prediction / pswitch) → `results/metrics/` |
| `smoke_test.py` | end-to-end import + shape check (synthetic inputs) |

## Environment

**Hard constraint: numpy < 2** (vendored acoular + numba). See `requirements.txt`
for the exact verified set.

```bash
python3.10 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
OPENBLAS_NUM_THREADS=1 python smoke_test.py     # all checks pass on synthetic data
```

## Verified (2026-07-05, M1 Mac CPU)

- Smoke test: all 6 checks pass.
- Real bag `G11_game4_DoA`: 160 real audio msgs → sound map; a real room1 frame →
  two faces detected `[[296,388,122,122],[670,370,142,142]]`; VAD + 4-label → `Right`.
- **Timing**: acoular sound-map generation ≈ **0.33 s / map** on M1 CPU. GT + DoA =
  2 maps/tick ⇒ ~0.66 s/tick ⇒ ~9 min/bag (800 ticks) ⇒ ~8 h for 52 bags single-thread.
  → WP3 must run on the high-spec PC with per-bag parallelism (`--workers`). SimVP
  inference is ~0.2 s/tick on CPU (much faster on CUDA/MPS).
