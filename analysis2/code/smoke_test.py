"""WP0 smoke test — verify the vendored pipeline imports and runs end-to-end.

Uses synthetic inputs only (no rosbag). On random noise, no faces are detected
(head boxes = -99, orientation = None) — that is expected; the point is that
every component imports and produces the right shapes.

    OPENBLAS_NUM_THREADS=1 python smoke_test.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import labeling as L
from soundmap_api import SoundMapAPI
from pssp_api import PsspAPI, HORIZONS_S
from head_box import HeadBoxAPI
from head_orientation import HeadOrientationAPI


def main():
    np.random.seed(0)
    hb = [[200, 400, 270, 270], [700, 400, 270, 270]]  # left, right in 1080 coords

    # 1. sound map (acoular 'old')
    sm_api = SoundMapAPI()
    chunks = [(np.random.randn(128, 16) * 500).astype(np.int16).tobytes() for _ in range(160)]
    t = time.time(); sm = sm_api.generate(chunks); t_sm = time.time() - t
    assert sm.shape == (64, 64)
    print(f"1 SoundMap   {sm.shape} min/max {sm.min():.1f}/{sm.max():.1f}  gen {t_sm:.3f}s")

    # 2. labeling
    lab, _, _ = L.label_current_sm(sm, hb, vad_active=True)
    labm, _, _ = L.label_current_sm(sm, hb, vad_active=False)
    assert lab in L.LABELS and labm in L.LABELS
    print(f"2 label      vad_on={lab} vad_off={labm}")

    # 3. clip frame + 4. PSSP (SimVP exp4)
    frame = (np.random.rand(1080, 1080, 3) * 255).astype(np.uint8)
    clip_frame = L.build_clip_frame(sm, True, frame)
    assert clip_frame.shape == (2, 64, 64)
    p = PsspAPI(device="cpu")
    t = time.time(); preds = p.predict(np.stack([clip_frame] * 10).astype(np.float32)); t_p = time.time() - t
    assert preds.shape == (4, 64, 64)
    labp, _, _ = L.label_prediction_sm(preds[1], hb)
    print(f"3 clip {clip_frame.shape}  4 PSSP {preds.shape} horizons {HORIZONS_S} infer {t_p:.3f}s  +1s={labp}")

    # 5. head box
    boxes = HeadBoxAPI().detect(frame)
    assert len(boxes) == 2
    print(f"5 HeadBox    {boxes}")

    # 6. head orientation
    ho = HeadOrientationAPI()
    ori = ho.detect((np.random.rand(480, 640, 3) * 255).astype(np.uint8))
    assert ho.yaw_to_side(5) == "left" and ho.yaw_to_side(-5) == "right"
    print(f"6 HeadOri    {ori}  yaw_to_side(+/-) = left/right")

    print("=== WP0 SMOKE TEST: ALL CHECKS PASSED ===")


if __name__ == "__main__":
    main()
