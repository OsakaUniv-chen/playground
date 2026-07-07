"""Room1 speech-activity gate — silero-vad, the operating point chosen in vad_check/.

The GT/DoA/PSSP labels are recomputed on room1 audio at every 4 Hz tick with no speech
gate, so during room1 silence the beamformer still picks a peak and the label flickers
(shrinking 発話ターン長). This module marks, per tick, whether someone is really speaking
in room1, so downstream code can gate on it.

Chosen operating point (vad_check, method "B / strict"): silero-vad run streaming over the
whole clip (its RNN state + silence-hysteresis keep soft / continuing speech alive), then a
tick counts as speech if >= RATIO of its GT window [t-WIN, t] falls inside a silero speech
segment. This is the single source of those parameters — bag2video overlay, the turn-length
analysis, and (later) extract.py all import from here.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import torch
from scipy.signal import resample_poly
from silero_vad import get_speech_timestamps, load_silero_vad

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bag_io as B  # noqa: E402

# chosen operating point ("B / strict")
VAD_FS = 16000
WIN_S = 0.46             # GT audio window [t-0.46, t]
THRESHOLD = 0.7          # silero speech-probability threshold
RATIO = 0.6              # tick = speech if >= this fraction of its window is speech
MIN_SPEECH_MS = 30
MIN_SILENCE_MS = 150
SPEECH_PAD_MS = 50
HANGOVER_TICKS = 1       # ~200 ms @ 4 Hz — keep ON this many ticks after a drop

_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        _MODEL = load_silero_vad()
    return _MODEL


def _hangover(mask: np.ndarray, hang: int) -> np.ndarray:
    if hang <= 0:
        return mask.copy()
    out = mask.copy()
    c = 0
    for i, on in enumerate(mask):
        if on:
            c = hang
        elif c > 0:
            out[i] = True
            c -= 1
    return out


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
    """silero speech segments as (start_s, end_s) seconds, clip-relative, native resolution.

    Dense (silero's ~32 ms frame / sample-resolution boundaries) — for turn-DURATION
    analysis, unlike speaking_at_ticks() which quantizes to the 4 Hz GT grid for gating.
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


def speaking_at_ticks(mono, fs, clip_t0_ns, tick_ts_ns, win_s=WIN_S, ratio=RATIO,
                      threshold=THRESHOLD, hangover=HANGOVER_TICKS) -> np.ndarray:
    """Bool per tick: room1 speech in window [t-win, t]. mono sample 0 == clip_t0_ns."""
    m = speech_mask(mono, fs, threshold)
    wl = int(win_s * VAD_FS)
    tick_ts_ns = np.asarray(tick_ts_ns, dtype=np.int64)
    out = np.zeros(len(tick_ts_ns), bool)
    for i, t in enumerate(tick_ts_ns):
        e = min(int(round((int(t) - int(clip_t0_ns)) / 1e9 * VAD_FS)), len(m))
        b = max(0, e - wl)
        if e > b:
            out[i] = m[b:e].mean() >= ratio
    return _hangover(out, hangover)


def load_bag_mono(con, t0_ns=None, t1_ns=None, channels=16):
    """Downmixed mono float32 for [t0,t1] (whole bag if None), plus its first ts (ns)."""
    tid = B.topic_id(con, B.AUDIO_TOPIC)
    if tid is None:
        return np.zeros(0, np.float32), None
    if t0_ns is None:
        rows = con.execute(
            "SELECT timestamp, data FROM messages WHERE topic_id=? ORDER BY timestamp",
            (tid,)).fetchall()
    else:
        rows = con.execute(
            "SELECT timestamp, data FROM messages WHERE topic_id=? AND timestamp BETWEEN ? AND ? "
            "ORDER BY timestamp", (tid, int(t0_ns), int(t1_ns))).fetchall()
    if not rows:
        return np.zeros(0, np.float32), None
    mono = np.concatenate(
        [np.frombuffer(B.decode_audio(d), np.int16).reshape(-1, channels) for _, d in rows]
    ).astype(np.float32).mean(axis=1)
    return mono, int(rows[0][0])


def speaking_for_bag(con, tick_ts_ns, **kw) -> np.ndarray:
    """Convenience: room1 speaking per tick for an open bag over the tick_ts span."""
    tick_ts_ns = np.asarray(tick_ts_ns, dtype=np.int64)
    t0 = int(tick_ts_ns.min()) - int((WIN_S + 1.0) * 1e9)
    t1 = int(tick_ts_ns.max()) + int(1e9)
    mono, clip_t0 = load_bag_mono(con, t0, t1)
    if clip_t0 is None:
        return np.zeros(len(tick_ts_ns), bool)
    return speaking_at_ticks(mono, 44100, clip_t0, tick_ts_ns, **kw)
