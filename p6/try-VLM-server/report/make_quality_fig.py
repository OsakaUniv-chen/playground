#!/usr/bin/env python3
"""品質比較ストリップ: sample14 を VLM 実効解像度(756x756)に縮小し、各 JPEG 画質で
エンコード→デコードした「VLM が実際に受け取る画像」を横一列に並べる。各パネルに
q とファイルサイズを注記。report/fig/ に保存し、q ごとのサイズも標準出力に出す。

pipeline(以後の本番と同じ): RGB(1080) → 756 に縮小 → JPEG(q) → 送信 → デコード → VLM。
756 は 28 の倍数かつ 756^2<602112 なので server 側の再縮小は起きず、これがそのまま入力。
"""
import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "mode_A" / "sample" / "sample_14.png"
FIG = HERE / "fig"
R = 756
QS = [100, 90, 80, 70, 60, 50, 40, 30]
BAR = 46


def jpeg_roundtrip(pil, q):
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=q)
    data = buf.getvalue()
    dec = Image.open(io.BytesIO(data)).convert("RGB")
    return np.array(dec)[:, :, ::-1].copy(), len(data)   # BGR, bytes


def label(panel, text):
    h, w = panel.shape[:2]
    out = np.zeros((h + BAR, w, 3), np.uint8)
    out[BAR:] = panel
    cv2.putText(out, text, (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.95, (255, 255, 255), 2,
                cv2.LINE_AA)
    return out


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    base = Image.open(SRC).convert("RGB").resize((R, R), Image.LANCZOS)
    # 例として使う素の RGB(756、重畳なし)も保存
    cv2.imwrite(str(FIG / "example_rgb_756.png"), np.array(base)[:, :, ::-1])
    panels, sizes = [], []
    for q in QS:
        img, n = jpeg_roundtrip(base, q)
        sizes.append((q, n))
        panels.append(label(img, "q%d  %.0fKB" % (q, n / 1024)))
        print("q%-3d %6.1f KB" % (q, n / 1024))
    strip = cv2.hconcat(panels)
    out = FIG / "jpeg_quality_756.png"
    cv2.imwrite(str(out), strip)
    print("strip %dx%d -> %s" % (strip.shape[1], strip.shape[0], out))


if __name__ == "__main__":
    main()
