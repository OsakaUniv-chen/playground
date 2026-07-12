"""ROS2 (sqlite3) bag reader + hand-written CDR decoders. No ROS2/rosbags dependency.

Adapted from Playground/generator-compare/bag_io.py (validated across 65 bags,
~50k ticks in that project's OLD-vs-NEW generator comparison). Extended here so
bag discovery also matches `G{n}_interview` folders, which generator-compare
excluded but this project's training data needs (see train-pssp/CONTEXT.md).

Extended again for PSSPData (train-pssp/train-data/SUITABILITY_REPORT.md):
older bag collections publish `/audio/audio_raw` as plain
`audio_common_msgs/msg/AudioData` (just `uint8[] data`, no header) instead of
WordWolfExp's `AudioDataStamped` (`header` + `uint8[] data`) -- same topic
name, different wire format, so the decoder can't be picked by topic name
alone anymore. `audio_decoder_for(con)` inspects the bag's own `topics` table
(which records each topic's message type string) to pick the right one
automatically, rather than hardcoding which collections use which format.
"""
from __future__ import annotations

import re
import sqlite3
import struct
from pathlib import Path

import cv2
import numpy as np

# G1_game4_PSSP -> group=1, game=4, mode=PSSP ; G1_interview -> group=1, mode=Interview (game=None)
DIR_RE = re.compile(
    r"^G(?P<group>\d+)_(?:game(?P<game>\d+)_(?P<mode>[A-Za-z]+)|(?P<interview>interview))$"
)

AUDIO_TOPIC = "/audio/audio_raw"
CAMERA_TOPIC = "/camera/image_raw/compressed"
ROOM2_CAMERA_TOPIC = "/room2_camera/image_raw/compressed"
VAD_TOPIC = "/room2_audio/vad"
MOTORS_TOPIC = "/boxie/boxie_motors"
# verification-only / not used by the training extractor yet
SM_TOPIC = "/sm_without_transform"
HEAD_TOPIC = "/head/head_box"
TARGET_POS_TOPIC = "/target/target_position"

BAG_ROOT_CANDIDATES = (
    "/media/chen/Extreme SSD/WordWolfExp/ROSbag",
    "/Volumes/Extreme SSD/WordWolfExp/ROSbag",
)


def resolve_bag_root(candidates=BAG_ROOT_CANDIDATES) -> str:
    """First candidate ROSbag root that exists and holds data, else the first candidate."""
    for c in candidates:
        p = Path(c)
        if p.is_dir() and any(p.iterdir()):
            return str(p)
    return str(candidates[0])


def parse_bag_name(name: str):
    """Return dict(group:int, game:int|None, mode:str) or None if name doesn't match."""
    m = DIR_RE.match(name)
    if not m:
        return None
    if m["interview"]:
        return {"group": int(m["group"]), "game": None, "mode": "Interview"}
    return {"group": int(m["group"]), "game": int(m["game"]), "mode": m["mode"]}


def _align(off: int, n: int, base: int = 4) -> int:
    return off + (-(off - base) % n)


def header_stamp_ns(data: bytes):
    """Extract header.stamp (sec int32 + nanosec uint32) as ns from any stamped msg."""
    try:
        sec, nsec = struct.unpack_from("<iI", data, 4)  # after 4-byte encapsulation
        return sec * 1_000_000_000 + nsec
    except struct.error:
        return None


# --------------------------------------------------------------------------
# CDR decoders
# --------------------------------------------------------------------------
def decode_audio(data: bytes) -> bytes:
    """AudioDataStamped -> raw uint8[] payload (int16, 16ch interleaved)."""
    off = 4 + 8
    off = _align(off, 4)
    (slen,) = struct.unpack_from("<I", data, off)
    off += 4 + slen
    off = _align(off, 4)
    (n,) = struct.unpack_from("<I", data, off)
    off += 4
    return data[off:off + n]


def decode_audio_unstamped(data: bytes) -> bytes:
    """AudioData (no header, just `uint8[] data`) -> raw uint8[] payload.
    Older PSSPData collections (pre-dating AudioDataStamped) use this."""
    off = _align(4, 4)
    (n,) = struct.unpack_from("<I", data, off)
    off += 4
    return data[off:off + n]


def decode_vad(data: bytes) -> bool:
    """VadStamped -> bool."""
    off = 4 + 8
    off = _align(off, 4)
    (slen,) = struct.unpack_from("<I", data, off)
    off += 4 + slen
    return bool(data[off])


def decode_boxie_yaw(data: bytes):
    """BoxieMotors -> yaw (data[1], int16) or None."""
    try:
        off = 4 + 8
        off = _align(off, 4)
        (slen,) = struct.unpack_from("<I", data, off)
        off += 4 + slen
        off = _align(off, 4)
        (alen,) = struct.unpack_from("<I", data, off)
        off += 4
        if alen < 2:
            return None
        off = _align(off, 2)
        vals = struct.unpack_from(f"<{alen}h", data, off)
        return vals[1]
    except struct.error:
        return None


def decode_compressed_image(data: bytes):
    """sensor_msgs/CompressedImage -> BGR ndarray (or None)."""
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


def decode_int32multiarray(data: bytes):
    """std_msgs/Int32MultiArray -> list[int] (or None)."""
    try:
        off = 4
        off = _align(off, 4)
        (ndim,) = struct.unpack_from("<I", data, off)
        off += 4
        for _ in range(ndim):
            off = _align(off, 4)
            (llen,) = struct.unpack_from("<I", data, off)
            off += 4 + llen
            off = _align(off, 4)
            off += 8
        off = _align(off, 4)
        off += 4
        off = _align(off, 4)
        (n,) = struct.unpack_from("<I", data, off)
        off += 4
        off = _align(off, 4)
        return list(struct.unpack_from(f"<{n}i", data, off))
    except struct.error:
        return None


def decode_target_pos(data: bytes):
    """TargetPos -> pos string ('left'/'right') or None. Verification-only for now."""
    try:
        off = 4 + 8
        off = _align(off, 4)
        (slen,) = struct.unpack_from("<I", data, off)
        off += 4 + slen
        off = _align(off, 4)
        (plen,) = struct.unpack_from("<I", data, off)
        off += 4
        return data[off:off + plen - 1].decode("utf-8", errors="replace")
    except struct.error:
        return None


TOPIC_DECODERS = {
    AUDIO_TOPIC: decode_audio,
    VAD_TOPIC: decode_vad,
    MOTORS_TOPIC: decode_boxie_yaw,
    CAMERA_TOPIC: decode_compressed_image,
    ROOM2_CAMERA_TOPIC: decode_compressed_image,
    SM_TOPIC: decode_compressed_image,
    HEAD_TOPIC: decode_int32multiarray,
    TARGET_POS_TOPIC: decode_target_pos,
}


# --------------------------------------------------------------------------
# sqlite helpers
# --------------------------------------------------------------------------
def find_db_files(bag_dir: Path) -> list[Path]:
    dbs = []
    for db in sorted(Path(bag_dir).glob("*.db3")):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if {"topics", "messages"} <= tables:
                dbs.append(db)
            con.close()
        except sqlite3.Error:
            continue
    return dbs


def topic_id(con: sqlite3.Connection, name: str):
    row = con.execute("SELECT id FROM topics WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def topic_type(con: sqlite3.Connection, name: str) -> str | None:
    """The message type string a bag itself declares for `name` (e.g.
    'audio_common_msgs/msg/AudioDataStamped'), or None if the topic isn't
    present in this bag."""
    row = con.execute("SELECT type FROM topics WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def audio_decoder_for(con: sqlite3.Connection):
    """Picks decode_audio (Stamped) or decode_audio_unstamped (plain
    AudioData) by checking what this specific bag declares for
    AUDIO_TOPIC -- some older PSSPData collections use the unstamped form,
    see this module's docstring. Raises if the topic is missing or an
    unrecognized type."""
    t = topic_type(con, AUDIO_TOPIC)
    if t is None:
        raise ValueError(f"bag has no {AUDIO_TOPIC} topic")
    if t.endswith("AudioDataStamped"):
        return decode_audio
    if t.endswith("AudioData"):
        return decode_audio_unstamped
    raise ValueError(f"unrecognized audio message type for {AUDIO_TOPIC}: {t!r}")


def read_series(con: sqlite3.Connection, topic: str, decode=None):
    """Return [(record_ts_ns, decoded), ...] ascending. Default decoder by topic."""
    tid = topic_id(con, topic)
    if tid is None:
        return []
    if decode is None:
        decode = TOPIC_DECODERS.get(topic, lambda d: d)
    out = []
    for ts, data in con.execute(
        "SELECT timestamp, data FROM messages WHERE topic_id = ? ORDER BY timestamp",
            (tid,)):
        out.append((ts, decode(data)))
    return out


def open_bag(bag_dir) -> sqlite3.Connection:
    dbs = find_db_files(Path(bag_dir))
    if not dbs:
        raise FileNotFoundError(f"no readable .db3 in {bag_dir}")
    return sqlite3.connect(f"file:{dbs[0]}?mode=ro", uri=True)
