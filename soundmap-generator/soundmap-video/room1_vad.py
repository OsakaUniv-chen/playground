"""Room1 speech-activity gate (silero-vad) + its QC-video strip renderer.

The GT/DoA/PSSP labels are recomputed on room1 audio at every 4 Hz tick with no speech
gate, so during room1 silence the beamformer still picks a peak and the label flickers
(shrinking 発話ターン長). This module marks whether someone is really speaking in room1,
so downstream code can gate on it, and (second half of the file) draws that speech state
into the scrolling QC-video strip.

speech_segments()/speech_mask() run silero-vad streaming over the whole clip (its RNN
state + silence-hysteresis keep soft/continuing speech alive) and report speech spans at
silero's native ~32 ms chunk resolution — the QC-video overlay (bag2video*.py) draws these
directly instead of quantizing them to the 4 Hz GT tick grid, so the strip shows real
onsets/offsets rather than 250 ms blocks.
"""
from __future__ import annotations

import bisect

import cv2
import numpy as np
import torch
from scipy.signal import resample_poly
from silero_vad import get_speech_timestamps, load_silero_vad

VAD_FS = 16000
THRESHOLD = 0.7          # silero speech-probability threshold
MIN_SPEECH_MS = 30
MIN_SILENCE_MS = 150
SPEECH_PAD_MS = 50

_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        _MODEL = load_silero_vad()
    return _MODEL


def speech_mask(mono: np.ndarray, fs: int, threshold: float = THRESHOLD) -> np.ndarray:
    """silero speech mask (bool @ VAD_FS) over the whole clip. mono: float32 @ fs."""
    if len(mono) == 0:
        return np.zeros(0, bool)
    w = resample_poly(mono, VAD_FS, fs).astype(np.float32)
    w = w / (np.abs(w).max() + 1e-9) * 0.95     # normalize; silero is level-robust
    segs = get_speech_timestamps(
        torch.from_numpy(w), _model(), sampling_rate=VAD_FS, threshold=threshold,
        min_speech_duration_ms=MIN_SPEECH_MS, min_silence_duration_ms=MIN_SILENCE_MS,
        speech_pad_ms=SPEECH_PAD_MS)
    m = np.zeros(len(w), bool)
    for s in segs:
        m[s["start"]:s["end"]] = True
    return m


def speech_segments(mono, fs, threshold=THRESHOLD):
    """silero speech segments as (start_s, end_s) seconds, clip-relative, native resolution
    (silero's ~32 ms chunk / sample-resolution boundaries) — this is what the QC-video
    overlay draws directly.
    """
    if len(mono) == 0:
        return []
    w = resample_poly(mono, VAD_FS, fs).astype(np.float32)
    w = w / (np.abs(w).max() + 1e-9) * 0.95
    segs = get_speech_timestamps(
        torch.from_numpy(w), _model(), sampling_rate=VAD_FS, threshold=threshold,
        min_speech_duration_ms=MIN_SPEECH_MS, min_silence_duration_ms=MIN_SILENCE_MS,
        speech_pad_ms=SPEECH_PAD_MS)
    return [(s["start"] / VAD_FS, s["end"] / VAD_FS) for s in segs]


def in_speech(segments_s, t_s) -> bool:
    """Native point-query: is t_s (clip-relative seconds) inside a speech_segments() span?"""
    if not segments_s:
        return False
    starts = [s for s, _ in segments_s]
    i = bisect.bisect_right(starts, t_s) - 1
    return i >= 0 and t_s <= segments_s[i][1]


def clip_segments(segments_s, offset_s, t_max, t_min=0.0):
    """Shift speech_segments() by offset_s (e.g. to re-anchor from a pre-roll buffer start
    to the strip's t=0) and clip to [t_min, t_max]. Drops/truncates spans outside the range.
    """
    out = []
    for s0, s1 in segments_s:
        a, b = max(t_min, s0 + offset_s), min(t_max, s1 + offset_s)
        if b > a:
            out.append((a, b))
    return out


# =============================================================================
# QC-video strip renderer (formerly vad_overlay.py) — shared by bag2video.py and
# bag2video_all_bag.py so both draw the same room1 speech gate the same way.
#
# The strip is one continuous image in real time: x = round(t_seconds * pps), where t is
# clip-relative seconds and pps ("pixels per second") is fixed for the whole render. One
# panel = a scrolling window of the last few seconds:
#   - gray  : room1 window-RMS envelope (dBFS), reference only, still one block per GT tick
#   - blue  : room1 VAD — silero speech_segments() drawn at native resolution (not the 4 Hz
#             gate above), each span annotated with its duration if wide enough to read
#   - (optional) one bar per 4-label detection (Left/Right/Teleoperator/Others), one cell
#     per GT tick, each finished same-label run annotated with its duration
#   - white : "now" cursor at the right edge
# Lane labels are drawn on a black background so the bars never hide them.
# =============================================================================

FONT = cv2.FONT_HERSHEY_SIMPLEX
BLUE, GRAY, WHITE = (240, 160, 40), (110, 110, 110), (240, 240, 240)
DB_MIN, DB_MAX = -80.0, -30.0
# BGR detection-bar colors for the 4 labels (match plot_turn_hist hues)
LABEL_COLORS = {"Left": (180, 119, 31), "Right": (14, 127, 255),
                "Teleoperator": (44, 160, 44), "Others": (189, 103, 148)}
LABEL_BARS = [(nm, LABEL_COLORS[nm]) for nm in ("Left", "Right", "Teleoperator", "Others")]
_SHORT = {"Teleoperator": "Tele"}
# lane label suffix: VAD is drawn at silero's native ~31 Hz chunk rate, the 4-label
# detection bars are drawn at the SM refresh rate (4 Hz GT tick grid)
_RATE = {"room1 VAD": "~31Hz"}


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


def build_strip(duration_s, pps, panel_h, label_bars=()):
    """Empty full-clip strip (width = duration_s * pps) + lane geometry.

    label_bars: sequence of (name, bgr_color) reserved as empty bars under the VAD band.
    Fill content with paint_env() / paint_vad_segments() / paint_label() (or
    LabelRunPainter for incremental per-tick labels); crop a display window with
    crop_panel().
    """
    width = max(1, int(round(duration_s * pps)))
    strip = np.zeros((panel_h, width, 3), np.uint8)
    n_bars = 1 + len(label_bars)
    env_y0 = 6
    env_y1 = env_y0 + int(panel_h * (0.52 if n_bars == 1 else 0.30))

    gap = 4
    top = env_y1 + 6
    bh = int((panel_h - top - 4 - gap * (n_bars - 1)) / n_bars)
    names = ["room1 VAD"] + [nm for nm, _ in label_bars]
    colors = [BLUE] + [c for _, c in label_bars]
    bars, y = [], top
    for name, col in zip(names, colors):
        bars.append({"name": name, "color": col, "y0": y, "y1": y + bh})
        y += bh + gap
    return strip, {"env_y0": env_y0, "env_y1": env_y1, "bars": bars}


def paint_env(strip, geom, rms_db, tick_off_s, tick_dt_s, pps, db_min=DB_MIN, db_max=DB_MAX):
    """Paint the gray RMS envelope; one block per tick at [tick_off_s, tick_off_s+dt)."""
    env_y0, env_y1 = geom["env_y0"], geom["env_y1"]
    w = strip.shape[1]

    def yv(db):
        frac = (np.clip(db, db_min, db_max) - db_min) / (db_max - db_min)
        return int(round(env_y1 - frac * (env_y1 - env_y0)))

    for db, t0 in zip(rms_db, tick_off_s):
        x0 = max(0, int(round(t0 * pps)))
        x1 = min(w, int(round((t0 + tick_dt_s) * pps)))
        if x1 > x0:
            cv2.rectangle(strip, (x0, yv(db)), (x1, env_y1), GRAY, -1)


def _duration_text(strip, x0, x1, y0, y1, dur_s, min_px=32):
    """Center a "<dur>s" label inside [x0,x1]x[y0,y1]; skip if it won't fit legibly."""
    if x1 - x0 < min_px:
        return
    text = f"{dur_s:.2f}s"
    scale, thick = 0.35, 1
    (tw, th), bl = cv2.getTextSize(text, FONT, scale, thick)
    if tw + 4 > x1 - x0:
        return
    tx = x0 + max(0, (x1 - x0 - tw) // 2)
    ty = (y0 + y1 + th) // 2
    cv2.rectangle(strip, (tx - 2, ty - th - 2), (tx + tw + 2, ty + bl + 1), (0, 0, 0), -1)
    cv2.putText(strip, text, (tx, ty), FONT, scale, WHITE, thick, cv2.LINE_AA)


def paint_vad_segments(strip, geom, segments_s, pps, min_label_px=32):
    """Paint room1 VAD (silero, native resolution) spans into the VAD band, each
    annotated with its duration when the span is wide enough to hold the text.
    """
    b0 = geom["bars"][0]
    w = strip.shape[1]
    for s0, s1 in segments_s:
        x0 = max(0, int(round(s0 * pps)))
        x1 = min(w, int(round(s1 * pps)))
        if x1 <= x0:
            continue
        cv2.rectangle(strip, (x0, b0["y0"]), (x1, b0["y1"]), b0["color"], -1)
        _duration_text(strip, x0, x1, b0["y0"], b0["y1"], s1 - s0, min_label_px)


def paint_label(strip, geom, t0_s, t1_s, label, pps):
    """Light the tick [t0_s, t1_s) cell in the label bar whose name == `label`."""
    w = strip.shape[1]
    for b in geom["bars"][1:]:
        if b["name"] == label:
            x0 = max(0, int(round(t0_s * pps)))
            x1 = min(w, int(round(t1_s * pps)))
            if x1 > x0:
                cv2.rectangle(strip, (x0, b["y0"]), (x1, b["y1"]), b["color"], -1)
            return


class LabelRunPainter:
    """Feed label_current_sm() output tick-by-tick (labels only become known live, as the
    render loop decodes each SM tick, so they can't be precomputed like the VAD band).
    Paints every tick's cell and, once a run of consecutive same-label ticks ends, stamps
    that run's duration onto its bar.
    """

    def __init__(self, strip, geom, pps):
        self.strip, self.geom, self.pps = strip, geom, pps
        self._label = None
        self._run_t0 = None

    def update(self, t0_s, t1_s, label):
        paint_label(self.strip, self.geom, t0_s, t1_s, label, self.pps)
        if label != self._label:
            if self._label is not None:
                self._stamp(self._label, self._run_t0, t0_s)
            self._label, self._run_t0 = label, t0_s

    def finalize(self, t_end_s):
        """Stamp the still-open run at the end of the clip, if any."""
        if self._label is not None:
            self._stamp(self._label, self._run_t0, t_end_s)
            self._label = None

    def _stamp(self, label, t0_s, t1_s):
        for b in self.geom["bars"][1:]:
            if b["name"] == label:
                x0 = max(0, int(round(t0_s * self.pps)))
                x1 = min(self.strip.shape[1], int(round(t1_s * self.pps)))
                _duration_text(self.strip, x0, x1, b["y0"], b["y1"], t1_s - t0_s, min_px=28)
                return


def _label(img, text, org, color, scale=0.45, thick=1):
    (w, h), bl = cv2.getTextSize(text, FONT, scale, thick)
    x, y = org
    cv2.rectangle(img, (x - 3, y - h - 3), (x + w + 3, y + bl + 1), (0, 0, 0), -1)
    cv2.putText(img, text, org, FONT, scale, color, thick, cv2.LINE_AA)


def crop_panel(strip, geom, t_now_s, pps, width):
    """Scrolling window of `width` px ending at clip-relative time t_now_s, with cursor
    + lane labels.
    """
    panel_h = strip.shape[0]
    x_now = max(0, min(int(round(t_now_s * pps)), strip.shape[1]))
    win = np.zeros((panel_h, width, 3), np.uint8)
    src0 = max(0, x_now - width)
    if x_now > src0:
        win[:, width - (x_now - src0):] = strip[:, src0:x_now]
    cv2.line(win, (width - 1, 0), (width - 1, panel_h), WHITE, 1)
    _label(win, "room1 RMS (ref)", (6, geom["env_y0"] + 16), WHITE, 0.45, 1)
    for b in geom["bars"]:
        name = _SHORT.get(b["name"], b["name"])
        rate = _RATE.get(b["name"], "4Hz")
        _label(win, f"{name} {rate}", (6, b["y1"] - 4), b["color"], 0.45, 1)
    return win
