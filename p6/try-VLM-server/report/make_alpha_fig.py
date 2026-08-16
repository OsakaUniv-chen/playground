#!/usr/bin/env python3
"""mode_B の α 比較ストリップ: sample14 の凸結合重畳(音図×α + RGB×(1−α))を α=0.3/0.5/0.7 で
横一列に並べ、VLM 実効入力の 756x756 で示す。既存の mode_B/sample/sample_14_aNN.png(1080)を
756 に縮小して使う。"""
from pathlib import Path
import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
SAMPLE = HERE.parent / "mode_B" / "sample"
FIG = HERE / "fig"
R = 756
ALPHAS = [0.3, 0.5, 0.7]
BAR = 46


def label(panel, text):
    h, w = panel.shape[:2]
    out = np.zeros((h + BAR, w, 3), np.uint8)
    out[BAR:] = panel
    cv2.putText(out, text, (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.95, (255, 255, 255), 2,
                cv2.LINE_AA)
    return out


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    panels = []
    for a in ALPHAS:
        p = SAMPLE / ("sample_14_a%02d.png" % round(a * 100))
        img = cv2.imread(str(p))
        if img is None:
            raise SystemExit("missing %s (先に mode_B/sample/gen_samples.py)" % p)
        img = cv2.resize(img, (R, R), interpolation=cv2.INTER_AREA)
        panels.append(label(img, "alpha=%.1f" % a))
    strip = cv2.hconcat(panels)
    out = FIG / "mode_b_alpha_756.png"
    cv2.imwrite(str(out), strip)
    print("strip %dx%d -> %s" % (strip.shape[1], strip.shape[0], out))


if __name__ == "__main__":
    main()
