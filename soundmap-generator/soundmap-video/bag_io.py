"""ROS2 (sqlite3) bag reader + hand-written CDR decoders.

Adapted from analysis1 (_targeting_env.py, p_switch_analysis.py, head_box_check.py)
so analysis2 needs no ROS/rosbags dependency. Time axis = bag record timestamp (ns).

Decoders return the payload we actually use; message type is inferred by topic
(see TOPIC_DECODERS). All strings/arrays are CDR-aligned by `_align`.
"""
from __future__ import annotations

import re
import sqlite3
import struct
from pathlib import Path

import cv2
import numpy as np

DIR_RE = re.compile(r"^G(?P<group>\d+)_game(?P<game>\d+)_(?P<mode>[A-Za-z]+)$")

# Topics used by analysis2 (see docs/ros-topics.md).
AUDIO_TOPIC = "/audio/audio_raw"
CAMERA_TOPIC = "/camera/image_raw/compressed"
ROOM2_CAMERA_TOPIC = "/room2_camera/image_raw/compressed"
VAD_TOPIC = "/room2_audio/vad"
MOTORS_TOPIC = "/boxie/boxie_motors"
# verification-only
SM_TOPIC = "/sm_without_transform"
HEAD_TOPIC = "/head/head_box"
TELE_ORIENT_TOPIC = "/tele/head_orientation"

# ROSbag root candidates: Linux (high-perf PC) first, macOS SSD mount as fallback.
BAG_ROOT_CANDIDATES = (
    "/media/chen/Extreme SSD/WordWolfExp/ROSbag",
    "/Volumes/Extreme SSD/WordWolfExp/ROSbag",
)


def resolve_bag_root(candidates=BAG_ROOT_CANDIDATES) -> str:
    """First candidate ROSbag root that exists and holds data, else the first candidate.

    Lets the same scripts run on the Linux PC (/media/chen/...) and the Mac
    (/Volumes/...) with no path edits: if /media/... has no data we switch to
    /Volumes/... . Returns the first candidate when none are populated so error
    messages stay stable.
    """
    for c in candidates:
        p = Path(c)
        if p.is_dir() and any(p.iterdir()):
            return str(p)
    return str(candidates[0])


def _align(off: int, n: int, base: int = 4) -> int:
    return off + (-(off - base) % n)


def header_stamp_ns(data: bytes):
    """Extract header.stamp (sec int32 + nanosec uint32) as ns from any stamped msg.
    Returns None if the payload has no room for a header."""
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


def decode_vector3stamped(data: bytes):
    """geometry_msgs/Vector3Stamped -> (x, y, z) float64 (verification only)."""
    try:
        off = 4 + 8
        off = _align(off, 4)
        (slen,) = struct.unpack_from("<I", data, off)
        off += 4 + slen
        off = _align(off, 8)
        return struct.unpack_from("<3d", data, off)
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
    TELE_ORIENT_TOPIC: decode_vector3stamped,
}


# --------------------------------------------------------------------------
# sqlite helpers
# --------------------------------------------------------------------------
def find_db_files(game_dir: Path) -> list[Path]:
    dbs = []
    for db in sorted(Path(game_dir).glob("*.db3")):
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


def iter_messages(con: sqlite3.Connection, topic: str):
    """Yield (record_ts_ns, raw_bytes) ascending for streaming cursors."""
    tid = topic_id(con, topic)
    if tid is None:
        return
    yield from con.execute(
        "SELECT timestamp, data FROM messages WHERE topic_id = ? ORDER BY timestamp",
        (tid,))


def open_bag(bag_dir) -> sqlite3.Connection:
    dbs = find_db_files(Path(bag_dir))
    if not dbs:
        raise FileNotFoundError(f"no readable .db3 in {bag_dir}")
    return sqlite3.connect(f"file:{dbs[0]}?mode=ro", uri=True)
