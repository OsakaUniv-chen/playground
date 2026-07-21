"""Shared machinery for trial-2 (word-wolf 4-label probe).

Trial-2 question: given the grayscale scene + the sound map, can a VLM name the
sound source among the natural 4-label of the Word Wolf experiment
(Left / Right / Teleoperator / Others)? This is closer to the real P6 task than
trial-1's abstract "which clock direction" -- here the labels are real people /
regions and there is a hard geometric ground truth per tick.

Data path: the tick tables (behavior-analysis/results/ticks/{bag}.parquet)
already store gt_label + head boxes + vad per tick, but NOT the sound map or the
camera frame (extract.py dropped --save-sm). So for each sampled tick we
regenerate the sound map from the bag audio (SoundMapAPI, identical to how
gt_label was computed) and grab the camera frame.

Faithfulness: gt_label = label_current_sm(sm, head_boxes, vad) which
  (1) masks the tele speaking-box when vad is inactive, then
  (2) transform_sm = exp(sm - max).
We feed the VLM the SAME masked+transformed map (as a jet overlay on the gray
frame), so it sees exactly what the geometric labeler saw -- an apples-to-apples
"can you read the sound direction as well as the rule does" test.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import cv2

# --- repo wiring ---------------------------------------------------------
PLAYGROUND = Path(__file__).resolve().parents[3]
WWE = PLAYGROUND / "word-wolf-exp-eval"
UTILS = WWE / "utils"
TICKS = WWE / "behavior-analysis" / "results" / "ticks"
for p in (UTILS, UTILS / "pssp"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import bag_io as B                          # noqa: E402
from labeling import (transform_sm, mask_speaking_box, SPEAKING_BOX,  # noqa: E402
                      LABELS)

AUDIO_WIN = 160                             # msgs per sound map (mirror extract.py)
LABELS = tuple(LABELS)                      # ("Left","Right","Teleoperator","Others")


def bag_dir(bag: str) -> Path:
    return Path(B.resolve_bag_root()) / bag


def load_audio(con):
    audio = B.read_series(con, B.AUDIO_TOPIC)
    return np.asarray([t for t, _ in audio]), [d for _, d in audio]


def gen_sm(sm_api, a_ts, a_d, t_ns: int):
    """Regenerate the 64x64 sound map from the audio window ending at t_ns."""
    j = int(np.searchsorted(a_ts, t_ns, side="right"))
    if j < AUDIO_WIN:
        return None
    return sm_api.generate(a_d[j - AUDIO_WIN:j])


def frame_at(con, cam_tid, t_ns: int):
    row = con.execute(
        "SELECT data FROM messages WHERE topic_id=? AND timestamp<=? "
        "ORDER BY timestamp DESC LIMIT 1", (cam_tid, int(t_ns))).fetchone()
    return B.decode_compressed_image(row[0]) if row else None


def label_input_sm(sm_64: np.ndarray, vad_active: bool) -> np.ndarray:
    """Exactly the map label_current_sm feeds to the colorizer:
    mask the tele box when VAD is inactive, then exp-transform. Values in [0,1]."""
    sm = sm_64 if vad_active else mask_speaking_box(sm_64)
    return transform_sm(sm)


def render_overlay(frame_bgr: np.ndarray, sm_transformed: np.ndarray,
                   alpha: float = 0.45, size: int = 768) -> np.ndarray:
    """Gray fisheye frame + jet sound-map overlay -> RGB uint8 (what the VLM sees).

    No boxes / labels are drawn: the VLM must visually find the two people and
    judge where the sound sits. The tele region is described in the prompt only.
    """
    import matplotlib.cm as cm
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    gray3 = np.stack([gray] * 3, axis=-1)
    n = sm_transformed / (sm_transformed.max() + 1e-9)
    heat = (cm.jet(cv2.resize(n, gray.shape[::-1]))[..., :3] * 255).astype(np.uint8)
    blend = ((1 - alpha) * gray3 + alpha * heat).astype(np.uint8)
    return cv2.resize(blend, (size, size), interpolation=cv2.INTER_AREA)
