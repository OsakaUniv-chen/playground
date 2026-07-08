# utils

Reusable detection / prediction utilities, vendored from `analysis2/code` and
`video-generator` so the rest of `word-wolf-exp-eval` doesn't reach outside
this repo. No acoular anywhere — sound-map generation uses the torch
beamforming reimplementation instead (see `generator-compare/` in the repo
root for the validation that it's a harmless, faster drop-in for the old
acoular generator).

| file | purpose | source |
|---|---|---|
| `pssp/pssp_api.py`, `pssp/simvp.py`, `pssp/config_simvp_exp4.pt` | `PsspAPI.predict(clip10) -> (4,64,64)` sound-map prediction at +0.5/1.0/1.5/2.0s (SimVP, live exp4 weights) | `analysis2/code/pssp_api.py` + `analysis2/code/pssp/` (dropped `utils_all_load.py`, training-only) |
| `soundmap_api.py` | `SoundMapAPI.generate(audio_chunks) -> (64,64)` torch frequency-domain beamforming sound map, no acoular/xml | `video-generator/beamform_soundmap.py` |
| `head_box.py` | `HeadBoxAPI.detect(frame) -> [left,right]` (MediaPipe FaceDetection, head_node logic) | `analysis2/code/head_box.py` |
| `head_orientation.py` | `HeadOrientationAPI.detect(img) -> (pitch,yaw,roll)` (MediaPipe FaceMesh + solvePnP) | `analysis2/code/head_orientation.py` |
| `labeling.py` | mask / transform / colorize / 4-label extract7 / VAD gate / clip-frame builder / `HeadBoxProcessor` (needed by `head_box.py`) | `analysis2/code/labeling.py` |
| `room1_vad.py` | silero-vad room1 speech-activity gate (chosen operating point from `analysis2/vad_check/`) | `analysis2/code/room1_vad.py` |
| `bag_io.py` | sqlite ROS2 bag reader + CDR decoders, needed by `room1_vad.py`'s bag-convenience functions | `analysis2/code/bag_io.py` |

## Environment note (2026-07-08)

`head_box.py` / `head_orientation.py` need the legacy `mp.solutions.*` API,
which mediapipe removed starting around 0.10.20. The `wolf` virtualenv had
0.10.35 installed; downgraded to `mediapipe==0.10.14` (matches
`analysis2/code/requirements.txt`) and re-pinned `numpy==1.26.4` +
`numba==0.59.1` to keep the resolver consistent (see `env/memo.py`).
