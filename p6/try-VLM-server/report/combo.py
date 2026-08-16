#!/usr/bin/env python3
"""combo 手法: mode_A coord(座標テキスト) + mode_B α=0.5(黄色重畳画像) を結合。

入力画像は α=0.5 の重畳、プロンプトの音源情報は「重畳がある + ピーク座標」の両方を渡す。
2 つのモダリティが同じ位置を補強し合う"最強手法"の候補。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mode_A"))
from textifier import _dominant_xy                        # noqa: E402


def make_combo_info(row):
    x, y = _dominant_xy(row)
    return ("A translucent YELLOW sound-energy heatmap is overlaid on the scene (brighter "
            "yellow = louder). In addition, a localization front-end reports the strongest "
            "sound at image pixel coordinate (%d, %d). Use BOTH the overlay and this "
            "coordinate together. Decide PRIMARILY from this sound information; use the image "
            "only to map the indicated location to one of the four labels — do NOT pick "
            "whoever merely looks like they are speaking (mouth, gestures)." % (x, y))
