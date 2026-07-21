#!/usr/bin/env python3
"""Transcribe a meeting recording with faster-whisper.

Downmixes the whole recording to mono and transcribes it once, writing a
single Markdown transcript.
"""

import argparse
import datetime as dt
import json
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import urllib3.util.connection as urllib3_connection

# This machine's IPv6 route to huggingface.co hangs until timeout; force IPv4
# so the first-time model download doesn't stall for minutes per attempt.
urllib3_connection.allowed_gai_family = lambda: socket.AF_INET

MODEL = "large-v3"
LANGUAGE = "ja"
DEVICE = "cuda"
COMPUTE_TYPE = "auto"
BEAM_SIZE = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe a meeting recording into a Markdown transcript."
    )
    parser.add_argument("audio", type=Path, help="Input audio/video file.")
    parser.add_argument(
        "--output-prefix",
        type=Path,
        help="Output prefix. Default: input path without extension.",
    )
    return parser.parse_args()


def require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"{name} is required but was not found in PATH.")


def run(cmd: List[str]) -> None:
    subprocess.run(cmd, check=True)


def ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    data = json.loads(result.stdout)
    return float((data.get("format") or {}).get("duration") or 0.0)


def seconds_to_timestamp(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def downmix(source: Path, target: Path) -> None:
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "flac",
            str(target),
        ]
    )


def load_model(model_name: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    kwargs = {}  # type: Dict[str, str]
    if device != "auto":
        kwargs["device"] = device
    if compute_type != "auto":
        kwargs["compute_type"] = compute_type
    return WhisperModel(model_name, **kwargs)


def transcribe(
    model: Any,
    path: Path,
    language: str,
    beam_size: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    language_arg = None if language == "auto" else language
    segments_iter, info = model.transcribe(
        str(path),
        language=language_arg,
        vad_filter=True,
        beam_size=beam_size,
        word_timestamps=False,
    )
    segments = []  # type: List[Dict[str, Any]]
    for segment in segments_iter:
        text = " ".join(segment.text.strip().split())
        if not text:
            continue
        segments.append(
            {
                "start": float(segment.start),
                "end": float(segment.end),
                "text": text,
            }
        )

    info_dict = {
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "duration": getattr(info, "duration", None),
    }
    return segments, info_dict


def write_transcript(
    prefix: Path,
    source: Path,
    metadata: Dict[str, Any],
    segments: List[Dict[str, Any]],
) -> Path:
    md_path = prefix.with_suffix(".transcript.md")

    generated_at = dt.datetime.now().isoformat(timespec="seconds")
    md_lines = [
        "# Meeting transcript",
        "",
        f"- Source: `{source}`",
        f"- Generated: {generated_at}",
        f"- Model: {metadata.get('model')}",
        f"- Language: {metadata.get('language_info')}",
        "",
        "## Transcript",
        "",
    ]

    for seg in segments:
        start = seconds_to_timestamp(seg["start"])
        end = seconds_to_timestamp(seg["end"])
        md_lines.append(f"[{start} - {end}] {seg['text']}")

    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return md_path


def main() -> int:
    args = parse_args()
    audio = args.audio.expanduser().resolve()
    if not audio.exists():
        raise SystemExit(f"Audio file not found: {audio}")

    require_command("ffmpeg")
    require_command("ffprobe")

    duration = ffprobe_duration(audio)
    prefix = args.output_prefix.expanduser().resolve() if args.output_prefix else audio.with_suffix("")
    prefix.parent.mkdir(parents=True, exist_ok=True)

    print(f"Audio: {audio}")
    print(f"Duration: {seconds_to_timestamp(duration)}")
    print(f"Loading model: {MODEL}")

    model = load_model(MODEL, DEVICE, COMPUTE_TYPE)

    with tempfile.TemporaryDirectory(prefix="meeting-transcribe-") as tmp:
        mixed = Path(tmp) / "mixed.flac"
        print("Downmixing audio...")
        downmix(audio, mixed)
        print("Transcribing...")
        segments, language_info = transcribe(model, mixed, LANGUAGE, BEAM_SIZE)

    metadata = {
        "source": str(audio),
        "model": MODEL,
        "beam_size": BEAM_SIZE,
        "language_info": language_info,
    }
    md_path = write_transcript(prefix, audio, metadata, segments)
    print(f"Wrote: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
