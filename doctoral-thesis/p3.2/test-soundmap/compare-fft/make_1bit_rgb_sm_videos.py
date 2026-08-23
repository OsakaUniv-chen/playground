#!/usr/bin/env python3
"""Render RGB + 1-bit sound-map comparison videos for Demonstration_Data.

Each output frame is split in two:
  left  = current display transform, exp(sm - sm.max())
  right = min-max scaled sound map, (sm - sm.min()) / (sm.max() - sm.min())

The sound map itself is regenerated from the raw 16-channel audio using
soundmap-generator/generator-1bit/onebit_soundmap.py.
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
PYTORCH_DIR = PLAYGROUND / "soundmap-generator" / "generator-pytorch"
DEFAULT_DATA_ROOTS = (
    Path.home() / "ros2_ws" / "xyz_data" / "Demonstration_Data",
    WORK_DIR / "xyz_data" / "Demonstration_Data",
    WORK_DIR.parent / "system" / "archive" / "minipc" / "ros2_ws" / "xyz_data" / "Demonstration_Data",
    WORK_DIR.parent / "system" / "archive" / "minipc" / "ros2_ws" / "xyz_data",
    WORK_DIR.parent / "system" / "archive" / "minipc" / "ros2_ws" / "Demonstration_Data",
)

sys.path.insert(0, str(ONEBIT_DIR))
sys.path.insert(0, str(PYTORCH_DIR))
from new_soundmap_api import NewSoundMapAPI  # noqa: E402
from onebit_soundmap import OneBitSoundMapAPI  # noqa: E402

AUDIO_TOPIC = "/audio/audio_raw"
CAMERA_TOPIC = "/camera/image_raw/compressed"
CHANNELS = 16
AUDIO_WIN = 160
SKIP_S = 0.0
PLOT_SIZE = 720
FS = 44100


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


def exp_scale(sm: np.ndarray) -> np.ndarray:
    x = sm.astype(np.float64)
    return np.exp(x - x.max()) if x.max() > 0 else x


def minmax_scale(sm: np.ndarray) -> np.ndarray:
    x = sm.astype(np.float64)
    lo = float(x.min())
    hi = float(x.max())
    if hi <= lo:
        return np.zeros_like(x)
    return (x - lo) / (hi - lo)


def sm_to_color(sm01: np.ndarray, size: int) -> np.ndarray:
    plot_sm = cv2.resize(np.clip(sm01, 0.0, 1.0), (size, size), interpolation=cv2.INTER_LINEAR)
    plot_sm = (plot_sm * 255).astype(np.uint8)
    return np.stack([np.zeros_like(plot_sm), plot_sm, plot_sm], axis=-1)


def overlay_panel(frame_bgr: np.ndarray, sm: np.ndarray, scaler, title: str, t_s: float) -> np.ndarray:
    cam = cv2.resize(frame_bgr, (PLOT_SIZE, PLOT_SIZE), interpolation=cv2.INTER_AREA)
    sm_color = sm_to_color(scaler(sm), PLOT_SIZE)
    blend = cv2.addWeighted(sm_color, 0.6, cam, 0.8, 0)
    cv2.rectangle(blend, (0, 0), (PLOT_SIZE, 48), (0, 0, 0), -1)
    cv2.putText(blend, title, (14, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255, 255, 255), 2)
    cv2.putText(blend, f"t={t_s:6.2f}s", (PLOT_SIZE - 150, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (230, 230, 230), 2)
    return blend


def blank_panel() -> np.ndarray:
    return np.zeros((PLOT_SIZE, PLOT_SIZE, 3), dtype=np.uint8)


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

    onebit_api = OneBitSoundMapAPI(fs=FS, channels=CHANNELS)
    fft_api = NewSoundMapAPI(fs=FS, channels=CHANNELS, device="cpu")
    out_path = out_dir / f"{bag_dir.name}_1bit_fft_4panel.mp4"
    tmp_video = out_dir / f"_{bag_dir.name}_1bit_fft_4panel_video.mp4"
    tmp_audio = out_dir / f"_{bag_dir.name}_1bit_fft_4panel_audio.wav"
    writer = cv2.VideoWriter(
        str(tmp_video),
        cv2.VideoWriter_fourcc(*"mp4v"),
        video_fps,
        (PLOT_SIZE * 2, PLOT_SIZE * 2),
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
                onebit_sm = np.zeros((64, 64), dtype=np.float64)
                fft_sm = np.zeros((64, 64), dtype=np.float64)
            else:
                audio_window = a_d[max(0, ja - AUDIO_WIN):ja]
                onebit_sm = onebit_api.generate(audio_window)
                fft_sm = fft_api.generate(audio_window)
            t_s = (t - t0) / 1e9
            top_left = overlay_panel(frame, onebit_sm, exp_scale, "1-bit exp(sm - sm.max())", t_s)
            top_right = overlay_panel(frame, onebit_sm, minmax_scale, "1-bit minmax(sm)", t_s)
            bottom_left = overlay_panel(frame, fft_sm, exp_scale, "PyTorch FFT exp(sm - sm.max())", t_s)
            writer.write(np.vstack([
                np.hstack([top_left, top_right]),
                np.hstack([bottom_left, blank_panel()]),
            ]))
            written += 1
            if frame_no == 1 or frame_no % 100 == 0:
                print(f"  {bag_dir.name}: frame+sound-map {frame_no}/{len(frame_indices)}", flush=True)
    finally:
        writer.release()

    if written == 0:
        tmp_video.unlink(missing_ok=True)
        raise RuntimeError(f"{bag_dir.name}: no frames written")

    audio_chunks = [
        a_d[k]
        for k, ts in enumerate(a_ts)
        if t0 <= int(ts) <= t_end
    ]
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

    cmd = [
        "ffmpeg", "-y",
        "-i", str(tmp_video),
        "-i", str(tmp_audio),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
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
        print("Pass the real path with: python make_1bit_rgb_sm_videos.py --data-root /path/to/Demonstration_Data")
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
