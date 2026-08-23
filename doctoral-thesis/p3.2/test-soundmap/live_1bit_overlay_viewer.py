#!/usr/bin/env python3
"""Live camera + 1-bit sound-map overlay viewer for parameter tuning.

This is deliberately local and simple:
  - camera: OpenCV VideoCapture
  - microphone array: arecord stdout pipe, usually miniDSP UMA16v2 S32_LE/16ch
  - sound map: soundmap-generator/generator-1bit/onebit_soundmap.py

Keys:
  q / Esc  quit
  [ / ]    decrease / increase temperature for soft exp
  - / =    decrease / increase overlay alpha
  1        exp(sm - max)
  2        exp((sm - max) / temperature)
  3        percentile display
  m        cycle view mode
"""
from __future__ import annotations

import argparse
import collections
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

WORK_DIR = Path(__file__).resolve().parent
PLAYGROUND = WORK_DIR.parents[2]
ONEBIT_DIR = PLAYGROUND / "soundmap-generator" / "generator-1bit"
sys.path.insert(0, str(ONEBIT_DIR))
from onebit_soundmap import OneBitSoundMapAPI  # noqa: E402

FS = 44100
CHANNELS = 16
DEFAULT_AUDIO_DEVICE = "hw:CARD=UMA16v2,DEV=0"
DEFAULT_CAMERA_NAME = "ELP,H264 USB Camera"


class AudioPipe:
    def __init__(self, device: str, rate: int, channels: int, chunk_ms: int,
                 sample_format: str, window_ms: int):
        self.device = device
        self.rate = rate
        self.channels = channels
        self.chunk_ms = chunk_ms
        self.sample_format = sample_format
        self.bytes_per_sample = 4 if sample_format == "S32_LE" else 2
        self.chunk_samples = max(1, int(round(rate * chunk_ms / 1000)))
        self.chunk_bytes = self.chunk_samples * channels * self.bytes_per_sample
        self.window_chunks = max(1, int(round(window_ms / chunk_ms)))
        self.chunks = collections.deque(maxlen=self.window_chunks)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.n_chunks = 0
        self.last_chunk_time = 0.0
        self.error = ""

    def start(self) -> None:
        cmd = [
            "arecord",
            "-q",
            "-D", self.device,
            "-f", self.sample_format,
            "-r", str(self.rate),
            "-c", str(self.channels),
            "-t", "raw",
        ]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop_event.set()
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def _reader(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        while not self.stop_event.is_set():
            raw = self.proc.stdout.read(self.chunk_bytes)
            if not raw:
                if self.proc.stderr is not None:
                    self.error = self.proc.stderr.read().decode("utf-8", errors="replace").strip()
                break
            if len(raw) != self.chunk_bytes:
                continue
            if self.sample_format == "S32_LE":
                x32 = np.frombuffer(raw, dtype="<i4").reshape(-1, self.channels)
                x16 = np.clip(x32 >> 16, -32768, 32767).astype("<i2")
                chunk = x16.tobytes()
            else:
                chunk = raw
            with self.lock:
                self.chunks.append(chunk)
                self.n_chunks += 1
                self.last_chunk_time = time.monotonic()

    def window(self) -> list[bytes]:
        with self.lock:
            return list(self.chunks)

    def status(self) -> tuple[int, float]:
        with self.lock:
            age = time.monotonic() - self.last_chunk_time if self.last_chunk_time else float("inf")
            return len(self.chunks), age

    def error_text(self) -> str:
        return self.error


def exp_scale(sm: np.ndarray) -> np.ndarray:
    x = sm.astype(np.float64)
    return np.exp(x - x.max()) if x.max() > 0 else np.zeros_like(x)


def soft_exp_scale(sm: np.ndarray, temperature: float) -> np.ndarray:
    x = sm.astype(np.float64)
    if x.max() <= 0:
        return np.zeros_like(x)
    return np.exp((x - x.max()) / max(temperature, 1e-6))


def percentile_scale(sm: np.ndarray, lo_q: float, hi_q: float) -> np.ndarray:
    x = sm.astype(np.float64)
    lo = float(np.percentile(x, lo_q))
    hi = float(np.percentile(x, hi_q))
    if hi <= lo:
        return np.zeros_like(x)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def sm_to_color(sm01: np.ndarray, size: int) -> np.ndarray:
    plot_sm = cv2.resize(np.clip(sm01, 0.0, 1.0), (size, size), interpolation=cv2.INTER_LINEAR)
    plot_sm = (plot_sm * 255).astype(np.uint8)
    return np.stack([np.zeros_like(plot_sm), plot_sm, plot_sm], axis=-1)


def resolve_camera(camera: str, camera_name: str) -> int | str:
    if camera:
        return int(camera) if camera.isdigit() else camera
    needles = [s.strip().lower() for s in camera_name.split(",") if s.strip()]
    matches = []
    for dev in sorted(Path("/sys/class/video4linux").glob("video*")):
        try:
            name = (dev / "name").read_text(errors="replace").strip()
        except OSError:
            continue
        if any(needle in name.lower() for needle in needles):
            video_path = f"/dev/{dev.name}"
            if os.path.exists(video_path):
                matches.append((video_path, name))
    if matches:
        print(f"using camera {matches[0][0]} ({matches[0][1]})", file=sys.stderr)
        return matches[0][0]
    print(f"no camera name matching {camera_name!r}; falling back to camera index 0", file=sys.stderr)
    return 0


def make_overlay(frame: np.ndarray, sm: np.ndarray, sm01: np.ndarray,
                 alpha: float, title: str, stats: str, output_size: int) -> np.ndarray:
    out = cv2.resize(frame, (output_size, output_size), interpolation=cv2.INTER_AREA)
    sm_color = sm_to_color(sm01, output_size)
    out = cv2.addWeighted(sm_color, alpha, out, 0.85, 0)
    cv2.rectangle(out, (8, 8), (min(output_size - 8, 900), 86), (0, 0, 0), -1)
    cv2.putText(out, title, (18, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.putText(out, stats, (18, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (230, 230, 230), 2)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", default="", help="OpenCV camera index or video path")
    parser.add_argument("--camera-name", default=DEFAULT_CAMERA_NAME,
                        help="auto-select /dev/video* whose V4L2 name contains this text")
    parser.add_argument("--audio-device", default=DEFAULT_AUDIO_DEVICE)
    parser.add_argument("--audio-format", choices=("S32_LE", "S16_LE"), default="S32_LE")
    parser.add_argument("--rate", type=int, default=FS)
    parser.add_argument("--channels", type=int, default=CHANNELS)
    parser.add_argument("--chunk-ms", type=int, default=10)
    parser.add_argument("--window-ms", type=int, default=464)
    parser.add_argument("--sm-fps", type=float, default=15.0)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--output-size", type=int, default=1080,
                        help="resize camera frame to this square size before sound-map overlay")
    parser.add_argument("--display-width", type=int, default=1080)
    parser.add_argument("--temperature", type=float, default=5.0)
    parser.add_argument("--alpha", type=float, default=0.6)
    parser.add_argument("--percentile-lo", type=float, default=50.0)
    parser.add_argument("--percentile-hi", type=float, default=99.0)
    args = parser.parse_args()

    camera_arg = resolve_camera(args.camera, args.camera_name)
    cap = cv2.VideoCapture(camera_arg)
    if args.width:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    if args.height:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        raise SystemExit(f"could not open camera: {args.camera}")

    audio = AudioPipe(args.audio_device, args.rate, args.channels, args.chunk_ms,
                      args.audio_format, args.window_ms)
    audio.start()
    api = OneBitSoundMapAPI(fs=args.rate, channels=args.channels)

    mode = 3
    mode_names = {
        1: "1-bit exp(sm - sm.max())",
        2: "1-bit soft exp",
        3: "1-bit percentile",
    }
    sm = np.zeros((64, 64), dtype=np.float64)
    sm01 = np.zeros_like(sm)
    next_sm_time = 0.0
    sm_ms = 0.0
    frame_count = 0
    t_start = time.monotonic()

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("camera frame read failed", file=sys.stderr)
                break

            now = time.monotonic()
            if now >= next_sm_time:
                win = audio.window()
                if win:
                    t0 = time.perf_counter()
                    sm = api.generate(win)
                    sm_ms = (time.perf_counter() - t0) * 1000.0
                next_sm_time = now + 1.0 / max(args.sm_fps, 0.1)

            if mode == 1:
                sm01 = exp_scale(sm)
            elif mode == 2:
                sm01 = soft_exp_scale(sm, args.temperature)
            else:
                sm01 = percentile_scale(sm, args.percentile_lo, args.percentile_hi)

            n_audio, audio_age = audio.status()
            frame_count += 1
            fps = frame_count / max(time.monotonic() - t_start, 1e-6)
            stats = (
                f"max={sm.max():.2f} p99={np.percentile(sm, 99):.2f} "
                f"mean={sm.mean():.3f} sm={sm_ms:.1f}ms "
                f"audio={n_audio} age={audio_age:.2f}s fps={fps:.1f}"
            )
            if audio.error_text():
                stats = "AUDIO ERROR: " + audio.error_text()[-100:]
            title = mode_names[mode]
            if mode == 2:
                title += f" T={args.temperature:.2f}"
            if mode == 3:
                title += f" p{args.percentile_lo:.0f}-p{args.percentile_hi:.0f}"
            out = make_overlay(frame, sm, sm01, args.alpha, title, stats, args.output_size)
            if args.display_width and out.shape[1] > args.display_width:
                scale = args.display_width / out.shape[1]
                out = cv2.resize(out, (args.display_width, int(out.shape[0] * scale)))
            cv2.imshow("live 1-bit sound map overlay", out)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                break
            if key == ord("m"):
                mode = 1 + (mode % 3)
            elif key == ord("1"):
                mode = 1
            elif key == ord("2"):
                mode = 2
            elif key == ord("3"):
                mode = 3
            elif key == ord("["):
                args.temperature = max(0.5, args.temperature - 0.5)
            elif key == ord("]"):
                args.temperature += 0.5
            elif key == ord("-"):
                args.alpha = max(0.0, args.alpha - 0.05)
            elif key in (ord("="), ord("+")):
                args.alpha = min(1.5, args.alpha + 0.05)
    finally:
        audio.close()
        cap.release()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
