#!/usr/bin/env python3
"""Render 1-bit sound-map visualization-parameter comparison videos.

Four panels, each computed per camera frame with band-pass 2000-8000 Hz,
all mic pairs, and exact integer delay:
  top-left      filter order 2
  top-right     filter order 4 (current baseline)
  bottom-left   filter order 6
  bottom-right  filter order 8

All panels use max(raw - p99(raw), 0) + minmax.
"""
from __future__ import annotations

import argparse
import subprocess
import sqlite3
import struct
import sys
from pathlib import Path

import cv2
import numpy as np
from scipy.io import wavfile

WORK_DIR = Path(__file__).resolve().parent


def find_playground(start: Path) -> Path:
    for p in (start, *start.parents):
        if (p / "soundmap-generator").is_dir():
            return p
    raise RuntimeError("could not find Playground root containing soundmap-generator")


PLAYGROUND = find_playground(WORK_DIR)
ONEBIT_DIR = PLAYGROUND / "soundmap-generator" / "generator-1bit"
DEFAULT_DATA_ROOTS = (
    Path.home() / "ros2_ws" / "xyz_data" / "Demonstration_Data",
    WORK_DIR / "xyz_data" / "Demonstration_Data",
)

sys.path.insert(0, str(ONEBIT_DIR))
from onebit_soundmap import OneBitSoundMapAPI  # noqa: E402

AUDIO_TOPIC = "/audio/audio_raw"
CAMERA_TOPIC = "/camera/image_raw/compressed"
CHANNELS = 16
AUDIO_WIN = 160
SKIP_S = 0.0
PANEL_SIZE = 720
GRID_COLS = 2
FS = 44100
FIXED_SM_ALPHA = 0.6
CAM_ALPHA = 0.8


def _align(off: int, n: int, base: int = 4) -> int:
    return off + (-(off - base) % n)


def decode_audio_stamped(data: bytes) -> bytes:
    off = 4 + 8
    off = _align(off, 4)
    (slen,) = struct.unpack_from("<I", data, off)
    off += 4 + slen
    off = _align(off, 4)
    (n,) = struct.unpack_from("<I", data, off)
    off += 4
    return data[off:off + n]


def decode_audio_unstamped(data: bytes) -> bytes:
    off = _align(4, 4)
    (n,) = struct.unpack_from("<I", data, off)
    off += 4
    return data[off:off + n]


def decode_compressed_image(data: bytes):
    try:
        off = 4 + 8
        off = _align(off, 4)
        (slen,) = struct.unpack_from("<I", data, off)
        off += 4 + slen
        off = _align(off, 4)
        (flen,) = struct.unpack_from("<I", data, off)
        off += 4 + flen
        off = _align(off, 4)
        (dlen,) = struct.unpack_from("<I", data, off)
        off += 4
        buf = np.frombuffer(data, dtype=np.uint8, count=dlen, offset=off)
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except (struct.error, ValueError):
        return None


def open_bag(bag_dir: Path) -> sqlite3.Connection:
    dbs = sorted(bag_dir.glob("*.db3"))
    if not dbs:
        raise FileNotFoundError(f"no .db3 in {bag_dir}")
    return sqlite3.connect(f"file:{dbs[0]}?mode=ro", uri=True)


def topic_id(con: sqlite3.Connection, topic: str):
    row = con.execute("SELECT id FROM topics WHERE name=?", (topic,)).fetchone()
    return None if row is None else row[0]


def topic_type(con: sqlite3.Connection, topic: str):
    row = con.execute("SELECT type FROM topics WHERE name=?", (topic,)).fetchone()
    return None if row is None else row[0]


def read_series(con: sqlite3.Connection, topic: str, decode):
    tid = topic_id(con, topic)
    if tid is None:
        return []
    return [
        (int(ts), decode(data))
        for ts, data in con.execute(
            "SELECT timestamp, data FROM messages WHERE topic_id=? ORDER BY timestamp",
            (tid,),
        )
    ]


def audio_decoder_for(con: sqlite3.Connection):
    msg_type = topic_type(con, AUDIO_TOPIC)
    if msg_type is None:
        raise RuntimeError(f"missing topic {AUDIO_TOPIC}")
    if msg_type.endswith("AudioDataStamped"):
        return decode_audio_stamped
    if msg_type.endswith("AudioData"):
        return decode_audio_unstamped
    raise RuntimeError(f"unknown audio topic type: {msg_type}")


def discover_bags(data_root: Path) -> list[Path]:
    if not data_root.exists():
        return []
    if list(data_root.glob("*.db3")):
        return [data_root]
    return sorted({p.parent for p in data_root.rglob("*.db3")})


def latest_idx(ts: np.ndarray, t_ns: int) -> int:
    return int(np.searchsorted(ts, t_ns, side="right")) - 1


def percentile_minmax(raw_sm: np.ndarray, percentile: float) -> np.ndarray:
    x = raw_sm.astype(np.float64)
    x = np.clip(x - np.percentile(x, percentile), 0.0, None)
    hi = float(x.max())
    if hi <= 0:
        return np.zeros_like(x)
    return x / hi


def p99_minmax_scale(raw_sm: np.ndarray) -> np.ndarray:
    return percentile_minmax(raw_sm, 99.0)


FILTER_VARIANTS = (
    ("filter order 2", 2),
    ("filter order 4", 4),
    ("filter order 6", 6),
    ("filter order 8", 8),
)


def sm_to_color(sm01: np.ndarray, size: int) -> np.ndarray:
    plot_sm = cv2.resize(np.clip(sm01, 0.0, 1.0), (size, size), interpolation=cv2.INTER_LINEAR)
    plot_sm = (plot_sm * 255).astype(np.uint8)
    return np.stack([np.zeros_like(plot_sm), plot_sm, plot_sm], axis=-1)


def tile_panels(panels: list[np.ndarray], cols: int) -> np.ndarray:
    rows = []
    for start in range(0, len(panels), cols):
        row = panels[start:start + cols]
        if len(row) < cols:
            row.extend([np.zeros_like(panels[0]) for _ in range(cols - len(row))])
        rows.append(np.hstack(row))
    return np.vstack(rows)


def overlay_panel(
    frame_bgr: np.ndarray,
    raw_sm: np.ndarray,
    title: str,
    t_s: float,
) -> np.ndarray:
    cam = cv2.resize(frame_bgr, (PANEL_SIZE, PANEL_SIZE), interpolation=cv2.INTER_AREA)
    shown = p99_minmax_scale(raw_sm)
    sm_color = sm_to_color(shown, PANEL_SIZE)
    blend = cv2.addWeighted(sm_color, FIXED_SM_ALPHA, cam, CAM_ALPHA, 0)
    cv2.rectangle(blend, (0, 0), (PANEL_SIZE, 58), (0, 0, 0), -1)
    cv2.putText(blend, title, (12, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
    cv2.putText(
        blend,
        f"t={t_s:6.2f}s raw max={raw_sm.max():.3f} p99={np.percentile(raw_sm, 99):.3f} mean={raw_sm.mean():.3f}",
        (12, 49),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.43,
        (230, 230, 230),
        1,
    )
    return blend


def render_bag(bag_dir: Path, out_dir: Path, limit_frames: int | None = None) -> Path:
    con = open_bag(bag_dir)
    audio = read_series(con, AUDIO_TOPIC, audio_decoder_for(con))
    camera = read_series(con, CAMERA_TOPIC, lambda d: d)
    con.close()
    if not audio:
        raise RuntimeError(f"{bag_dir.name}: no audio messages")
    if not camera:
        raise RuntimeError(f"{bag_dir.name}: no camera messages")

    a_ts = np.asarray([t for t, _ in audio], dtype=np.int64)
    a_d = [d for _, d in audio]
    c_ts = np.asarray([t for t, _ in camera], dtype=np.int64)
    c_d = [d for _, d in camera]

    first_frame = None
    for raw_frame in c_d:
        first_frame = decode_compressed_image(raw_frame)
        if first_frame is not None:
            break
    if first_frame is None:
        raise RuntimeError(f"{bag_dir.name}: no decodable camera frames")

    t0 = int(a_ts[0]) + int(round(SKIP_S * 1e9))
    t_end = int(min(a_ts[-1], c_ts[-1]))
    frame_indices = np.flatnonzero((c_ts >= t0) & (c_ts <= t_end))
    if limit_frames is not None:
        frame_indices = frame_indices[:limit_frames]
        if frame_indices.size:
            t_end = int(c_ts[int(frame_indices[-1])])
    if frame_indices.size == 0:
        raise RuntimeError(f"{bag_dir.name}: no camera frames in render interval")

    video_duration_s = max((t_end - t0) / 1e9, 1e-6)
    video_fps = frame_indices.size / video_duration_s
    grid_rows = -(-len(FILTER_VARIANTS) // GRID_COLS)
    apis = [
        (
            title,
            api,
        )
        for title, order in FILTER_VARIANTS
        for api in [OneBitSoundMapAPI(
            fs=FS, channels=CHANNELS,
            filter_order=order,
            band_low=2000, band_high=8000,
        )]
    ]

    out_path = out_dir / f"{bag_dir.name}_1bit_params.mp4"
    tmp_video = out_dir / f"_{bag_dir.name}_1bit_params_video.mp4"
    tmp_audio = out_dir / f"_{bag_dir.name}_1bit_params_audio.wav"
    writer = cv2.VideoWriter(
        str(tmp_video),
        cv2.VideoWriter_fourcc(*"mp4v"),
        video_fps,
        (PANEL_SIZE * GRID_COLS, PANEL_SIZE * grid_rows),
    )
    if not writer.isOpened():
        raise RuntimeError(f"could not open video writer for {tmp_video}")

    last_frame = first_frame
    written = 0
    try:
        for frame_no, ci in enumerate(frame_indices, start=1):
            t = int(c_ts[ci])
            frame = decode_compressed_image(c_d[int(ci)])
            if frame is None:
                frame = last_frame
            last_frame = frame

            ja = latest_idx(a_ts, t) + 1
            if ja <= 0:
                raw_maps = [(title, np.zeros((64, 64), dtype=np.float64)) for title, _ in apis]
            else:
                chunks = a_d[max(0, ja - AUDIO_WIN):ja]
                raw_maps = [
                    (title, api.generate_raw_score(chunks))
                    for title, api in apis
                ]
            t_s = (t - t0) / 1e9
            rendered = [overlay_panel(frame, raw_sm, title, t_s) for title, raw_sm in raw_maps]
            writer.write(tile_panels(rendered, GRID_COLS))
            written += 1
            if frame_no == 1 or frame_no % 100 == 0:
                print(f"  {bag_dir.name}: frame+1bit {frame_no}/{len(frame_indices)}", flush=True)
    finally:
        writer.release()

    if written == 0:
        tmp_video.unlink(missing_ok=True)
        raise RuntimeError(f"{bag_dir.name}: no frames written")

    audio_chunks = [a_d[k] for k, ts in enumerate(a_ts) if t0 <= int(ts) <= t_end]
    if not audio_chunks:
        tmp_video.replace(out_path)
        return out_path

    audio_np = np.concatenate([
        np.frombuffer(chunk, np.int16).reshape(-1, CHANNELS)
        for chunk in audio_chunks
    ])
    mono = audio_np.astype(np.float32).mean(axis=1)
    target_samples = int(round((written / video_fps) * FS))
    if mono.shape[0] < target_samples:
        mono = np.pad(mono, (0, target_samples - mono.shape[0]))
    elif mono.shape[0] > target_samples:
        mono = mono[:target_samples]
    mono = np.clip(mono, -32768, 32767).astype(np.int16)
    wavfile.write(tmp_audio, FS, mono)

    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", str(tmp_video),
        "-i", str(tmp_audio),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        str(out_path),
    ], capture_output=True, text=True)
    tmp_video.unlink(missing_ok=True)
    tmp_audio.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {bag_dir.name}: {result.stderr[-500:]}")
    return out_path


def resolve_data_root(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    for candidate in DEFAULT_DATA_ROOTS:
        if candidate.exists():
            return candidate.resolve()
    return DEFAULT_DATA_ROOTS[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", help="Folder containing Demonstration_Data bag directories")
    parser.add_argument("--out-dir", default=str(WORK_DIR / "rgb-sm-videos"))
    parser.add_argument("--limit-bags", type=int, help="debug: render only the first N bags")
    parser.add_argument("--limit-frames", type=int, help="debug: render only the first N camera frames per bag")
    args = parser.parse_args()

    data_root = resolve_data_root(args.data_root)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    bags = discover_bags(data_root)
    if args.limit_bags is not None:
        bags = bags[:args.limit_bags]
    if not bags:
        print(f"No ROS2 .db3 bags found under {data_root}")
        return 2

    print(f"data root: {data_root}")
    print(f"output:    {out_dir}")
    print(f"bags:      {len(bags)}")
    failures = []
    for bag in bags:
        try:
            out = render_bag(bag, out_dir, limit_frames=args.limit_frames)
            print(f"wrote {out}")
        except Exception as exc:
            failures.append((bag, exc))
            print(f"FAILED {bag}: {exc}", file=sys.stderr)

    if failures:
        print("\nFailures:")
        for bag, exc in failures:
            print(f"  {bag}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
