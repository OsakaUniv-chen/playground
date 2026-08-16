#!/usr/bin/env python3
"""第4部の例シーンの α 重畳ストリップ(a30/a50/a70 @756)を 2 枚生成。"""
from pathlib import Path
import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
MB = HERE.parent / "mode_B" / "sample"
FIG = HERE / "fig"
R, BAR = 756, 46
# ex4 は G1_game3 tick734（綺麗な環境音 Others）を g1data から別途構築するためここには含めない
SCENES = [("ex1", "sample_05"), ("ex2", "sample_14"), ("ex3", "sample_01")]
ALPHAS = [30, 50, 70]


def label(p, t):
    h, w = p.shape[:2]
    o = np.zeros((h + BAR, w, 3), np.uint8)
    o[BAR:] = p
    cv2.putText(o, t, (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.95, (255, 255, 255), 2, cv2.LINE_AA)
    return o


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    for tag, sid in SCENES:
        panels = []
        for a in ALPHAS:
            img = cv2.imread(str(MB / ("%s_a%02d.png" % (sid, a))))
            img = cv2.resize(img, (R, R), interpolation=cv2.INTER_AREA)
            panels.append(label(img, "alpha=0.%d" % (a // 10)))
        out = FIG / ("%s_alpha_756.png" % tag)
        cv2.imwrite(str(out), cv2.hconcat(panels))
        print("->", out)


if __name__ == "__main__":
    main()
