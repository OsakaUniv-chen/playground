"""Extract PSSP training data (soundmap + gray camera sequences) from every
known bag source, and render a sanity-check video per bag.

This is the promoted, full-scale successor to first-run/preprocessing/'s
small-scale script (see train-pssp/CONTEXT.md and preprocessing/DATA_REPORT.md
for the full history of how each collection below was vetted). No more
--bag-root/--out flags: sources and output pools are hardcoded in JOBS below,
since the whole point of this rewrite is "one script, one registry of known
sources" instead of ad-hoc one-off extraction scripts per collection.

For every bag, walks a fixed TICK-second output grid and for each tick:
  - builds the AUDIO_WIN-message /audio/audio_raw window ending at that tick
    and feeds it to the torch sound-map generator -> raw [0,160] soundmap,
    deliberately NOT normalized here (exp(x-max) normalization is applied at
    training-time data loading, not baked into the npz).
  - looks up the nearest /camera/image_raw/compressed frame at or before the
    tick, converts to grayscale, resizes to SM_SIZE.

Two outputs per bag:
  - train-data/<name>.npz or train-data-aux/<name>.npz: soundmap (N,SM,SM)
    float32, gray_camimg (N,SM,SM) uint8, tick_ts (N,) int64.
  - soundmap-videos/<name>.mp4 (flat, not split by pool): the
    exp(x-max)-normalized soundmap overlaid on the full-color camera frame
    (yellow "sm_to_color" map, same recipe as
    Playground/video-generator/bag2video.py), PLUS -- matching that same
    reference video -- a PANEL_H-tall strip below the main scene showing
    room1 VAD (gray = per-tick RMS envelope, blue = silero speech segments,
    both computed once per bag straight from the raw mic audio, so this part
    works for every collection). If the bag ALSO has `/head/head_box` +
    `/room2_audio/vad` (only WordWolfExp/Testrun0420-style boxie recordings
    do), the panel additionally gets the 4-label (Left/Right/Teleoperator/
    Others) detection bars and the main scene gets head-box/speaking-box
    annotations + a "room1 VAD SPEAK/silent" text overlay, exactly like
    bag2video.py -- for every other collection these are simply omitted
    (missing topics, not an error), leaving just the universal VAD-only
    strip. See preprocessing/room1_vad.py and preprocessing/labeling.py
    (vendored from video-generator/, not re-implemented).
    Rendered at VIDEO_FPS=1/VIDEO_TICK=10Hz for smooth camera motion, but the
    soundmap/labels are NOT recomputed at 10Hz -- still only once per TICK
    (2Hz), held across the VIDEO_SUBTICKS video frames until the next TICK.
    Written directly during the tick loop (not buffered in memory first)
    since some bags run 1-2+ hours -- buffering would be tens of GB.

Each pool directory keeps its own index.csv of per-bag metadata (bag, group,
game, mode, dur_s, n_ticks, sec). Resumable per output, independently: a bag
with an existing npz but no video only gets its video re-rendered (re-reads
the bag's audio+camera topics to rebuild the VAD panel and, if available,
labels, but reuses the already-computed soundmap array and never touches the
GPU generator -- the expensive part); a bag missing its npz gets the full
extraction (which also produces the video in the same pass). `--force`
re-does both regardless of what already exists.
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import bag_io as B
import labeling as L
import room1_vad as R1
from soundmap import SoundMapGenerator

TICK = 0.5
AUDIO_WIN = 160
SKIP_S = 10.0
SM_SIZE = 64
FS = 44100
CHANNELS = 16
BLOCKSIZE = 4096
VIDEO_TICK = 0.1  # 10Hz camera sampling for the QC video -- independent of
                  # TICK (the 2Hz soundmap rate): re-running the beamforming
                  # generator at 10Hz would be 5x the GPU cost for no benefit,
                  # since the soundmap itself only updates every TICK anyway.
VIDEO_FPS = 1.0 / VIDEO_TICK
VIDEO_SIZE = 720          # main scene size (bag2video.py's PLOT_SIZE)
PANEL_H = 260             # room1-VAD (+ 4-label, when available) strip height
PPF = 12                  # strip pixels per TICK (same visual density as bag2video.py)
SM_BLEND_ALPHA = 0.6      # weights match video-generator/bag2video.py's
CAM_BLEND_BETA = 0.8      # cv2.addWeighted(sm_color, 0.6, cam, 0.8, 0)
VIDEO_SUBTICKS = round(TICK / VIDEO_TICK)
assert abs(TICK / VIDEO_TICK - VIDEO_SUBTICKS) < 1e-9, "TICK must be an exact multiple of VIDEO_TICK"

PSSPDATA_ROOT = Path("/media/chen/Extreme SSD/PSSPData")

TRAIN_PSSP_ROOT = _HERE.parent
TRAIN_DATA_DIR = TRAIN_PSSP_ROOT / "train-data"
TRAIN_DATA_AUX_DIR = TRAIN_PSSP_ROOT / "train-data-aux"
VIDEO_ROOT = TRAIN_PSSP_ROOT / "soundmap-videos"

WORDWOLF_MODES = ("Interview", "Video", "DoA", "PSSP", "Random", "Tele")


@dataclass
class Job:
    label: str                     # index.csv "group" column
    root: Path                     # WordWolfExp root, or a PSSPData/<collection> dir
    pool: str                      # "train-data" | "train-data-aux"
    naming: str                    # "wordwolf" | "prefixed"
    camera_flip: bool = False      # some PSSPData collections mounted the camera reversed
    only_bags: list[str] | None = None   # restrict to these bag_dir.name values
    npz_prefix: str | None = None  # defaults to label; override to share a prefix across jobs
    mode_from_suffix: bool = False  # mode = bag_dir.name's last "_"-separated token


# Provenance / vetting notes are in DATA_REPORT.md (may be stale re: pool
# assignment, see below). Owner reorganized PSSPData on disk (2026-07-11):
# everything now lives under PSSPDATA_ROOT, WordWolfExp included (was a
# separate external root before); GRP_meeting/olab_0630/olab_rev_0630 moved
# under a "Meeting/" parent; demo_data_0318_becap/egoSAS_test_data/riken_3f
# moved under "egoSAS_demo_data/" (which also gained a new "kitchen"
# sub-collection); GRP_meeting grew from 5 to ~44 sessions, riken_3f from 1
# to 8, demo_data_0318_becap from 6 to 8. Owner's call this pass: don't
# spend time judging train-data vs train-data-aux -- default EVERY job to
# pool="train-data", owner will triage later. expo_2025 stays excluded: it's
# an empty folder, nothing to extract, not a judgment call.
#
# Worth flagging (not blocking): spot-checking the new GRP_meeting sessions
# found several (e.g. 2025-12-17-16_39_30) carry `/camera/pose/sample` +
# Tobii gaze topics -- i.e. they're egocentric-wearable recordings like
# egoSAS_test_data, mixed in under the SAME collection name as the original
# 5 fixed-camera sessions. The extraction still runs (moving camera is a
# geometry-correctness problem, not a crash), but "GRP_meeting" is no longer
# a uniformly fixed-camera collection -- worth knowing when triaging later.
JOBS: list[Job] = [
    Job("WordWolfExp", PSSPDATA_ROOT / "WordWolfExp", "train-data", "wordwolf"),
    Job("Experiment0312", PSSPDATA_ROOT / "Experiment0312", "train-data", "prefixed", mode_from_suffix=True),
    Job("Experiment1126", PSSPDATA_ROOT / "Experiment1126", "train-data", "prefixed", mode_from_suffix=True),
    Job("olab_0630", PSSPDATA_ROOT / "Meeting" / "olab_0630", "train-data", "prefixed"),
    Job("olab_rev_0630", PSSPDATA_ROOT / "Meeting" / "olab_rev_0630", "train-data", "prefixed", camera_flip=True),
    Job("GRP_meeting", PSSPDATA_ROOT / "Meeting" / "GRP_meeting", "train-data", "prefixed"),
    Job("chat", PSSPDATA_ROOT / "chat", "train-data", "prefixed"),
    Job("Testrun0420", PSSPDATA_ROOT / "Testrun0420", "train-data", "prefixed", mode_from_suffix=True),
    Job("Demonstration_Data", PSSPDATA_ROOT / "Demonstration_Data", "train-data", "prefixed",
        only_bags=["5", "6", "7", "8", "9", "10"]),
    Job("Demonstration_Data_nonconv", PSSPDATA_ROOT / "Demonstration_Data", "train-data", "prefixed",
        only_bags=["1", "2", "3", "4"], npz_prefix="Demonstration_Data"),
    Job("demo_data_0318_becap", PSSPDATA_ROOT / "egoSAS_demo_data" / "demo_data_0318_becap", "train-data", "prefixed"),
    Job("ProjectMobileRobot_3f", PSSPDATA_ROOT / "ProjectMobileRobot_3f", "train-data", "prefixed"),
    Job("riken_3f", PSSPDATA_ROOT / "egoSAS_demo_data" / "riken_3f", "train-data", "prefixed"),
    Job("egoSAS_test_data", PSSPDATA_ROOT / "egoSAS_demo_data" / "egoSAS_test_data", "train-data", "prefixed"),
    Job("kitchen", PSSPDATA_ROOT / "egoSAS_demo_data" / "kitchen", "train-data", "prefixed"),
]
JOBS_BY_LABEL = {j.label: j for j in JOBS}


def _latest_idx(ts_arr, t):
    """Index of the latest element with ts <= t (or -1)."""
    return int(np.searchsorted(ts_arr, t, side="right")) - 1


def discover_bags(job: Job) -> list[tuple[Path, dict | None]]:
    if job.naming == "wordwolf":
        out = []
        for d in sorted(job.root.iterdir()):
            if not d.is_dir():
                continue
            info = B.parse_bag_name(d.name)
            if info and info["mode"] in WORDWOLF_MODES:
                out.append((d, info))
        return out
    if not job.root.is_dir():
        return []
    out = []
    for d in sorted(job.root.iterdir()):
        if not d.is_dir():
            continue
        if job.only_bags is not None and d.name not in job.only_bags:
            continue
        out.append((d, None))
    return out


def out_name_for(job: Job, bag_dir: Path) -> str:
    if job.naming == "wordwolf":
        return bag_dir.name
    prefix = job.npz_prefix or job.label
    return f"{prefix}_{bag_dir.name}"


def group_game_mode(job: Job, bag_dir: Path, info: dict | None):
    if job.naming == "wordwolf":
        return str(info["group"]), info["game"], info["mode"]
    mode = bag_dir.name.rsplit("_", 1)[-1] if job.mode_from_suffix else None
    return job.label, None, mode


def _exp_normalize(sm: np.ndarray) -> np.ndarray:
    m = sm.max()
    if m <= 0:
        return np.zeros_like(sm, dtype=np.float32)
    return np.exp(sm - m).astype(np.float32)


def _camera_frame_cache(c_ts: np.ndarray, c_d: list, camera_flip: bool):
    """Shared camera-frame lookup+decode+cache closure -- called both at the
    2Hz soundmap ticks and (more often) at the 10Hz video sub-ticks, so a
    frame is only decoded once no matter which caller reaches its message
    index first."""
    last_ci, last_frame = -1, None

    def camera_frame_at(t_ns):
        nonlocal last_ci, last_frame
        ci = _latest_idx(c_ts, t_ns)
        if ci < 0:
            return None
        if ci != last_ci:
            frame = B.decode_compressed_image(c_d[ci])
            if frame is not None:
                if camera_flip:
                    frame = cv2.flip(frame, -1)
                last_ci, last_frame = ci, frame
        return last_frame

    return camera_frame_at


class VideoPanel:
    """Everything needed to render one bag's QC video panel + (optionally)
    the 4-label overlay, built once per bag from the whole-bag audio (and,
    if available, /head/head_box + /room2_audio/vad). See module docstring."""

    def __init__(self, a_ts: np.ndarray, a_d: list, t0: int, t_end: int,
                 head_series, vad_series):
        self.t0 = t0
        self.pps = PPF / TICK
        duration_s = (t_end - t0) / 1e9
        lo = int(np.searchsorted(a_ts, t0 - int(1e9)))
        hi = int(np.searchsorted(a_ts, t_end, side="right"))
        # Downmix to mono PER MESSAGE CHUNK before concatenating -- found the
        # hard way that concatenating the full 16-channel interleaved int16
        # array first (then .astype(float32).mean()) peaks at ~33GB for a
        # long bag (e.g. a 2.15h GRP_meeting session: ~11GB int16 concat +
        # ~22GB float32 copy), enough to OOM the machine on its own even
        # with each bag isolated in its own subprocess (see process_job()'s
        # memory-leak note -- that fix was necessary but not sufficient).
        # Downmixing each small chunk first means the concatenate only ever
        # allocates the final MONO-sized array (~1/16th as large).
        mono = np.concatenate([
            np.frombuffer(b, np.int16).reshape(-1, CHANNELS).astype(np.float32).mean(axis=1)
            for b in a_d[lo:hi]
        ])

        self.has_labels = head_series is not None and vad_series is not None
        label_bars = R1.LABEL_BARS if self.has_labels else ()
        self.strip, self.geom = R1.build_strip(duration_s, self.pps, PANEL_H, label_bars)

        tick_ts = np.arange(t0, t_end, int(TICK * 1e9))
        tick_off_s = (tick_ts - t0) / 1e9
        rms_db = R1.per_tick_rms(mono, FS, int(a_ts[lo]), tick_ts, 0.46)
        R1.paint_env(self.strip, self.geom, rms_db, tick_off_s, TICK, self.pps)

        segs = R1.speech_segments(mono, FS)
        self.vad_segs = R1.clip_segments(segs, (int(a_ts[lo]) - t0) / 1e9, duration_s)
        R1.paint_vad_segments(self.strip, self.geom, self.vad_segs, self.pps)

        self.speaking_box = L.get_speaking_box()
        self.painter = None
        self.h_ts = self.head = self.vts = self.vval = None
        if self.has_labels:
            self.painter = R1.LabelRunPainter(self.strip, self.geom, self.pps)
            self.head = [(t, v) for t, v in head_series if v and len(v) >= 8]
            self.h_ts = np.asarray([t for t, _ in self.head], dtype=np.int64)
            self.vts = [t / 1e9 for t, _ in vad_series]
            self.vval = [bool(v) for _, v in vad_series]

    def label_at(self, t: int, sm_raw: np.ndarray):
        """Returns (label, metrics, head_boxes, marks, sm_for_color) for tick
        t, and records it into the scrolling label bars. sm_for_color is the
        (possibly speaking-box-masked) raw sm to colorize for the main scene
        -- masking depends on local VAD, matching the live DoADetector."""
        jh = _latest_idx(self.h_ts, t)
        hb = ([list(self.head[jh][1][0:4]), list(self.head[jh][1][4:8])] if jh >= 0
              else [[-99] * 4, [-99] * 4])
        va_local = L.vad_active_at(self.vts, self.vval, t / 1e9)
        sm_for_color = sm_raw if va_local else L.mask_speaking_box(sm_raw)
        label, metrics, marks = L.label_current_sm(sm_raw, hb, va_local, speaking_box=self.speaking_box)
        t_s = (t - self.t0) / 1e9
        self.painter.update(t_s, t_s + TICK, label)
        return label, metrics, hb, marks, sm_for_color

    def finalize(self, t_end: int) -> None:
        if self.painter is not None:
            self.painter.finalize((t_end - self.t0) / 1e9)


def _write_video_ticks(video_writer: cv2.VideoWriter, camera_frame_at, t: int,
                        sm_raw: np.ndarray, fallback_frame: np.ndarray,
                        panel: VideoPanel | None, label_info: tuple | None) -> None:
    """Writes VIDEO_SUBTICKS frames covering [t, t+TICK) for one already-
    computed soundmap tick, re-sampling just the camera at VIDEO_TICK
    granularity. `panel` (the VAD strip) is always present; `label_info` is
    only non-None for bags with /head/head_box + /room2_audio/vad (see
    module docstring) -- without it: plain yellow-overlay-on-camera at
    VIDEO_SIZE + the VAD-only strip. With it: full bag2video.py-style frame
    -- 1080-space blend (head-box/speaking-box coordinates are calibrated to
    that resolution) resized down to VIDEO_SIZE, label annotations + VAD
    text, vstacked with the scrolling panel (now with label bars) crop.
    Shared by extract_bag() (fresh extraction) and render_video_only()
    (video-only regen from an existing npz)."""
    if label_info is None:
        # Universal case (no /head/head_box + /room2_audio/vad on this bag):
        # plain yellow overlay at VIDEO_SIZE, still vstacked with the
        # VAD-only strip (panel is always built, see extract_bag/
        # render_video_only -- only the label bars are conditional).
        sm_color = L.sm_to_color(_exp_normalize(sm_raw), plot_size=VIDEO_SIZE)
        for k in range(VIDEO_SUBTICKS):
            vt = t + int(k * VIDEO_TICK * 1e9)
            vframe = camera_frame_at(vt)
            if vframe is None:
                vframe = fallback_frame
            cam = cv2.resize(vframe, (VIDEO_SIZE, VIDEO_SIZE), interpolation=cv2.INTER_AREA)
            scene = cv2.addWeighted(sm_color, SM_BLEND_ALPHA, cam, CAM_BLEND_BETA, 0)
            t_s = (vt - panel.t0) / 1e9
            panel_crop = R1.crop_panel(panel.strip, panel.geom, t_s, panel.pps, VIDEO_SIZE)
            video_writer.write(np.vstack([scene, panel_crop]))
        return

    label, metrics, hb, marks, sm_for_color = label_info
    sm_color_1080 = L.sm_to_color(L.transform_sm(sm_for_color), plot_size=1080)
    for k in range(VIDEO_SUBTICKS):
        vt = t + int(k * VIDEO_TICK * 1e9)
        vframe = camera_frame_at(vt)
        if vframe is None:
            vframe = fallback_frame
        cam1080 = cv2.resize(vframe, (1080, 1080), interpolation=cv2.INTER_AREA)
        blend = cv2.addWeighted(sm_color_1080, SM_BLEND_ALPHA, cam1080, CAM_BLEND_BETA, 0)
        L.plot_annotations(blend, label, metrics, hb, speaking_box=panel.speaking_box, marker_points=marks)
        t_s = (vt - panel.t0) / 1e9
        speaking_now = R1.in_speech(panel.vad_segs, t_s)
        cv2.putText(blend, f"room1 VAD {'SPEAK' if speaking_now else 'silent'}  t={t_s:6.2f}s",
                    (10, 1050), cv2.FONT_HERSHEY_SIMPLEX, 1.4,
                    (0, 255, 0) if speaking_now else (0, 0, 255), 3)
        scene = cv2.resize(blend, (VIDEO_SIZE, VIDEO_SIZE))
        panel_crop = R1.crop_panel(panel.strip, panel.geom, t_s, panel.pps, VIDEO_SIZE)
        video_writer.write(np.vstack([scene, panel_crop]))


def render_video_only(bag_dir: Path, npz_path: Path, video_writer: cv2.VideoWriter,
                       camera_flip: bool = False) -> None:
    """For a bag that already has a valid npz but is missing its QC video
    (e.g. extracted before video generation existed, or before this
    project's PSSPData reorg) -- regenerates just the video. Reuses the
    already-computed soundmap array (never touches the GPU generator, the
    expensive part) but DOES re-read audio (for the VAD panel) and camera."""
    data = np.load(npz_path)
    soundmap, tick_ts = data["soundmap"], data["tick_ts"]

    con = B.open_bag(bag_dir)
    audio = B.read_series(con, B.AUDIO_TOPIC, decode=B.audio_decoder_for(con))
    a_ts = np.asarray([t for t, _ in audio], dtype=np.int64)
    a_d = [d for _, d in audio]
    cam = B.read_series(con, B.CAMERA_TOPIC, decode=lambda d: d)
    has_labels = B.topic_type(con, B.HEAD_TOPIC) is not None and B.topic_type(con, B.VAD_TOPIC) is not None
    head_series = B.read_series(con, B.HEAD_TOPIC) if has_labels else None
    vad_series = B.read_series(con, B.VAD_TOPIC) if has_labels else None
    con.close()
    if not cam:
        raise RuntimeError("no camera messages")
    c_ts = np.asarray([t for t, _ in cam], dtype=np.int64)
    c_d = [d for _, d in cam]
    camera_frame_at = _camera_frame_cache(c_ts, c_d, camera_flip)

    t0, t_end = int(tick_ts[0]), int(tick_ts[-1]) + int(TICK * 1e9)
    panel = VideoPanel(a_ts, a_d, t0, t_end, head_series, vad_series) if len(a_ts) else None

    for i in range(len(tick_ts)):
        t = int(tick_ts[i])
        frame = camera_frame_at(t)
        if frame is None:
            continue
        label_info = panel.label_at(t, soundmap[i]) if (panel is not None and panel.has_labels) else None
        _write_video_ticks(video_writer, camera_frame_at, t, soundmap[i], frame, panel, label_info)

    if panel is not None:
        panel.finalize(t_end)


def extract_bag(bag_dir: Path, generator: SoundMapGenerator, camera_flip: bool = False,
                 video_writer: cv2.VideoWriter | None = None) -> dict:
    """Runs the full tick loop for one bag. Returns a dict with the arrays
    that get saved to npz plus dur_s -- raises on any hard failure (too few
    audio msgs, no camera msgs, bag too short, no valid ticks). If
    video_writer is given, writes the QC frame for every tick directly to it
    (streamed, not buffered -- see module docstring)."""
    con = B.open_bag(bag_dir)
    # Auto-detects AudioDataStamped vs plain AudioData per-bag -- see
    # bag_io.py's module docstring.
    audio = B.read_series(con, B.AUDIO_TOPIC, decode=B.audio_decoder_for(con))
    if len(audio) < AUDIO_WIN + 1:
        con.close()
        raise RuntimeError(f"too few audio msgs ({len(audio)})")
    a_ts = np.asarray([t for t, _ in audio], dtype=np.int64)
    a_d = [d for _, d in audio]

    cam = B.read_series(con, B.CAMERA_TOPIC, decode=lambda d: d)  # keep raw bytes; decode lazily
    has_labels = B.topic_type(con, B.HEAD_TOPIC) is not None and B.topic_type(con, B.VAD_TOPIC) is not None
    head_series = B.read_series(con, B.HEAD_TOPIC) if has_labels else None
    vad_series = B.read_series(con, B.VAD_TOPIC) if has_labels else None
    con.close()
    if not cam:
        raise RuntimeError("no camera messages")
    c_ts = np.asarray([t for t, _ in cam], dtype=np.int64)
    c_d = [d for _, d in cam]

    t0 = int(a_ts[0] + SKIP_S * 1e9)
    t_end = int(min(a_ts[-1], c_ts[-1]))
    if t_end <= t0:
        raise RuntimeError("bag too short after warm-up skip")
    ticks = range(t0, t_end + 1, int(TICK * 1e9))

    def audio_window(end_ns):
        j = int(np.searchsorted(a_ts, end_ns, side="right"))
        return a_d[j - AUDIO_WIN:j] if j >= AUDIO_WIN else None

    camera_frame_at = _camera_frame_cache(c_ts, c_d, camera_flip)
    panel = VideoPanel(a_ts, a_d, t0, t_end, head_series, vad_series) if video_writer is not None else None

    sm_list, gray_list, ts_list = [], [], []
    for t in ticks:
        w = audio_window(t)
        if w is None:
            continue
        frame = camera_frame_at(t)
        if frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (SM_SIZE, SM_SIZE), interpolation=cv2.INTER_AREA)

        sm = generator.generate(w)

        sm_list.append(sm.astype(np.float32))
        gray_list.append(gray)
        ts_list.append(t)

        if video_writer is not None:
            label_info = panel.label_at(t, sm) if panel.has_labels else None
            _write_video_ticks(video_writer, camera_frame_at, t, sm, frame, panel, label_info)

    if panel is not None:
        panel.finalize(t_end)

    if not sm_list:
        raise RuntimeError("no valid ticks produced")

    return {
        "soundmap": np.stack(sm_list),
        "gray_camimg": np.stack(gray_list),
        "tick_ts": np.asarray(ts_list, dtype=np.int64),
        "dur_s": (t_end - t0) / 1e9,
    }


def save_npz(data: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_path,
        soundmap=data["soundmap"],
        gray_camimg=data["gray_camimg"],
        tick_ts=data["tick_ts"],
    )


def load_index(idx_path: Path) -> dict:
    if not idx_path.exists():
        return {}
    with open(idx_path) as f:
        return {row["bag"]: row for row in csv.DictReader(f)}


def write_index(idx_path: Path, rows: dict) -> None:
    fields = ["bag", "group", "game", "mode", "dur_s", "n_ticks", "sec"]
    with open(idx_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in sorted(rows.values(), key=lambda r: str(r["bag"])):
            w.writerow(row)


def _video_writer(video_path: Path) -> cv2.VideoWriter:
    return cv2.VideoWriter(
        str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), VIDEO_FPS, (VIDEO_SIZE, VIDEO_SIZE + PANEL_H)
    )


def process_job(job: Job, generator: SoundMapGenerator, force: bool = False, limit: int = 0) -> None:
    out_dir = TRAIN_DATA_DIR if job.pool == "train-data" else TRAIN_DATA_AUX_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    VIDEO_ROOT.mkdir(parents=True, exist_ok=True)

    bags = discover_bags(job)
    idx_path = out_dir / "index.csv"
    existing = load_index(idx_path)

    todo_full, todo_video_only = [], []
    for bag_dir, info in bags:
        out_name = out_name_for(job, bag_dir)
        npz_path = out_dir / f"{out_name}.npz"
        video_path = VIDEO_ROOT / f"{out_name}.mp4"
        if force or not npz_path.exists():
            todo_full.append((bag_dir, info, out_name))
        elif not video_path.exists():
            todo_video_only.append((bag_dir, out_name))
    if limit:
        todo_full = todo_full[:limit]
        todo_video_only = todo_video_only[:limit]
    n_done = len(bags) - len(todo_full) - len(todo_video_only)
    print(f"[{job.label}] {len(todo_full)} full extract, {len(todo_video_only)} video-only, {n_done} already done")

    for bag_dir, info, out_name in todo_full:
        t0 = time.time()
        video_path = VIDEO_ROOT / f"{out_name}.mp4"
        writer = _video_writer(video_path)
        try:
            data = extract_bag(bag_dir, generator, camera_flip=job.camera_flip, video_writer=writer)
            save_npz(data, out_dir / f"{out_name}.npz")
            sec = round(time.time() - t0, 1)
            n_ticks = data["tick_ts"].shape[0]
            print(f"  done {out_name}: {n_ticks} ticks ({data['dur_s']:.0f}s bag) in {sec}s")
            group, game, mode = group_game_mode(job, bag_dir, info)
            existing[out_name] = {
                "bag": out_name, "group": group, "game": game, "mode": mode,
                "dur_s": round(data["dur_s"], 1), "n_ticks": n_ticks, "sec": sec,
            }
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED {out_name}: {e}")
            video_path.unlink(missing_ok=True)  # partial/corrupt on failure
        finally:
            writer.release()

    # Video-only regen runs each bag in its OWN subprocess (see
    # render_one_video_only_subprocess docstring) -- found the hard way that
    # running many of these in-process leaks memory across bags (RSS grew
    # from ~8GB to ~25GB over ~5 GRP_meeting bags before the whole run had
    # to be killed; never pinned down exactly which library -- silero-vad,
    # scipy's resample_poly, or plain CPython/malloc fragmentation on a lot
    # of large audio arrays -- so subprocess isolation sidesteps the
    # question entirely: OS-level process exit always reclaims everything).
    for bag_dir, out_name in todo_video_only:
        t0 = time.time()
        ok = render_one_video_only_subprocess(job.label, bag_dir.name)
        sec = time.time() - t0
        print(f"  video-only {out_name}: {'done' if ok else 'FAILED'} in {sec:.1f}s")

    write_index(idx_path, existing)
    print(f"index.csv updated -> {idx_path}")


def render_one_video_only_subprocess(job_label: str, bag_name: str) -> bool:
    """Spawns `python build_dataset.py --video-only-bag <job_label> <bag_name>`
    and waits for it -- see the memory-leak note in process_job(). Returns
    True on success (subprocess exit code 0)."""
    r = subprocess.run(
        [sys.executable, str(_HERE / "build_dataset.py"), "--video-only-bag", job_label, bag_name],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"    subprocess stderr (last 2000 chars): {r.stderr[-2000:]}")
    return r.returncode == 0


def _run_one_video_only(job_label: str, bag_name: str) -> None:
    """Entry point for the --video-only-bag subprocess: render exactly one
    bag's video from its existing npz, no GPU generator involved."""
    job = JOBS_BY_LABEL[job_label]
    out_dir = TRAIN_DATA_DIR if job.pool == "train-data" else TRAIN_DATA_AUX_DIR
    bag_dir = job.root / bag_name
    out_name = out_name_for(job, bag_dir)
    npz_path = out_dir / f"{out_name}.npz"
    video_path = VIDEO_ROOT / f"{out_name}.mp4"
    VIDEO_ROOT.mkdir(parents=True, exist_ok=True)
    writer = _video_writer(video_path)
    try:
        render_video_only(bag_dir, npz_path, writer, camera_flip=job.camera_flip)
    except Exception:
        video_path.unlink(missing_ok=True)
        writer.release()
        raise
    writer.release()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job", default=None,
                     help=f"process only this job's label (one of: {', '.join(JOBS_BY_LABEL)}); "
                          "omit to process every job")
    ap.add_argument("--list-jobs", action="store_true", help="print the job registry and exit")
    ap.add_argument("--device", default="cuda", help="torch device for the sound-map generator (cuda|cpu)")
    ap.add_argument("--force", action="store_true", help="re-extract bags that already have an npz")
    ap.add_argument("--limit", type=int, default=0, help="cap how many bags per job (for quick tests)")
    ap.add_argument("--video-only-bag", nargs=2, metavar=("JOB_LABEL", "BAG_NAME"), default=None,
                     help="internal: render exactly one bag's video-only regen and exit "
                          "(used by process_job() to isolate each bag in its own subprocess, see "
                          "the memory-leak note there) -- not meant to be run by hand")
    args = ap.parse_args()

    if args.video_only_bag:
        _run_one_video_only(*args.video_only_bag)
        return

    if args.list_jobs:
        for j in JOBS:
            print(f"{j.label:30s} pool={j.pool:16s} root={j.root}")
        return

    if args.job is not None and args.job not in JOBS_BY_LABEL:
        raise SystemExit(f"unknown --job {args.job!r}; use --list-jobs to see valid labels")
    jobs = [JOBS_BY_LABEL[args.job]] if args.job else JOBS

    with warnings.catch_warnings():
        warnings.simplefilter("once")
        generator = SoundMapGenerator(
            fs=FS, channels=CHANNELS, blocksize=BLOCKSIZE,
            sm_size=SM_SIZE, device=args.device,
        )
    print(f"generator ready on {generator.device}")

    for job in jobs:
        process_job(job, generator, force=args.force, limit=args.limit)


if __name__ == "__main__":
    main()
