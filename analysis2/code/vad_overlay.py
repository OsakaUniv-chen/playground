"""Scrolling room1-VAD strip (RMS envelope + silero speech band + optional label bars).

Shared by vad_compare.py and the bag2video QC videos so they all show the same room1
speech gate the same way. One panel = a scrolling window of the last N ticks:
  - gray  : room1 window-RMS envelope (dBFS), reference only
  - blue  : room1 VAD (silero, room1_vad.py) 4 Hz speaking band
  - (optional) one bar per 4-label detection (Left/Right/Teleoperator/Others), each lit
    on the ticks where the live sound-map label equals it (painted incrementally with
    paint_label as the labels are computed in the render loop)
  - white : "now" cursor at the right edge
Lane labels are drawn on a black background so the bars never hide them.
"""
from __future__ import annotations

import cv2
import numpy as np

FONT = cv2.FONT_HERSHEY_SIMPLEX
BLUE, GRAY, WHITE = (240, 160, 40), (110, 110, 110), (240, 240, 240)
DB_MIN, DB_MAX = -80.0, -30.0
# BGR detection-bar colors for the 4 labels (match plot_turn_hist hues)
LABEL_COLORS = {"Left": (180, 119, 31), "Right": (14, 127, 255),
                "Teleoperator": (44, 160, 44), "Others": (189, 103, 148)}
LABEL_BARS = [(nm, LABEL_COLORS[nm]) for nm in ("Left", "Right", "Teleoperator", "Others")]
_SHORT = {"Teleoperator": "Tele"}


def rms_dbfs(x):
    if not len(x):
        return DB_MIN
    return 20.0 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2)) / 32768.0 + 1e-9)


def per_tick_rms(mono, fs, clip_t0_ns, tick_ts_ns, win_s):
    """room1 window-RMS (dBFS) per tick, window [t-win, t]."""
    wlen = int(win_s * fs)
    out = np.full(len(tick_ts_ns), DB_MIN, np.float32)
    for i, t in enumerate(tick_ts_ns):
        e = int(round((int(t) - int(clip_t0_ns)) / 1e9 * fs))
        b = max(0, e - wlen)
        out[i] = rms_dbfs(mono[b:e])
    return out


def build_strip(rms_db, speaking, ppf, panel_h, label_bars=(), db_min=DB_MIN, db_max=DB_MAX):
    """Full-width strip image + lane geometry.

    label_bars: sequence of (name, bgr_color) reserved as empty bars under the VAD band;
    fill them per tick with paint_label(). Crop a display window with crop_panel().
    """
    n = len(rms_db)
    strip = np.zeros((panel_h, max(n, 1) * ppf, 3), np.uint8)
    n_bars = 1 + len(label_bars)
    env_y0 = 6
    env_y1 = env_y0 + int(panel_h * (0.52 if n_bars == 1 else 0.30))

    def yv(db):
        frac = (np.clip(db, db_min, db_max) - db_min) / (db_max - db_min)
        return int(round(env_y1 - frac * (env_y1 - env_y0)))

    for k in range(n):
        cv2.rectangle(strip, (k * ppf, yv(rms_db[k])), ((k + 1) * ppf, env_y1), GRAY, -1)

    gap = 4
    top = env_y1 + 6
    bh = int((panel_h - top - 4 - gap * (n_bars - 1)) / n_bars)
    names = ["room1 VAD"] + [nm for nm, _ in label_bars]
    colors = [BLUE] + [c for _, c in label_bars]
    bars, y = [], top
    for name, col in zip(names, colors):
        bars.append({"name": name, "color": col, "y0": y, "y1": y + bh})
        y += bh + gap

    b0 = bars[0]                                   # VAD band is known up front
    for k in range(n):
        if speaking[k]:
            cv2.rectangle(strip, (k * ppf, b0["y0"]), ((k + 1) * ppf, b0["y1"]), b0["color"], -1)
    return strip, {"env_y0": env_y0, "env_y1": env_y1, "bars": bars}


def paint_label(strip, geom, idx, label, ppf):
    """Light tick `idx` in the label bar whose name == `label` (incremental)."""
    for b in geom["bars"][1:]:
        if b["name"] == label:
            cv2.rectangle(strip, (idx * ppf, b["y0"]), ((idx + 1) * ppf, b["y1"]), b["color"], -1)
            return


def _label(img, text, org, color, scale=0.45, thick=1):
    (w, h), bl = cv2.getTextSize(text, FONT, scale, thick)
    x, y = org
    cv2.rectangle(img, (x - 3, y - h - 3), (x + w + 3, y + bl + 1), (0, 0, 0), -1)
    cv2.putText(img, text, org, FONT, scale, color, thick, cv2.LINE_AA)


def crop_panel(strip, geom, cur_idx, ppf, width):
    """Scrolling window of `width` px ending at the current tick, with cursor + labels."""
    panel_h = strip.shape[0]
    x_now = max(0, (cur_idx + 1) * ppf)
    win = np.zeros((panel_h, width, 3), np.uint8)
    src0 = max(0, x_now - width)
    if x_now > src0:
        win[:, width - (x_now - src0):] = strip[:, src0:x_now]
    cv2.line(win, (width - 1, 0), (width - 1, panel_h), WHITE, 1)
    _label(win, "room1 RMS (ref)", (6, geom["env_y0"] + 16), WHITE, 0.45, 1)
    for b in geom["bars"]:
        _label(win, _SHORT.get(b["name"], b["name"]), (6, b["y1"] - 4), b["color"], 0.45, 1)
    return win
