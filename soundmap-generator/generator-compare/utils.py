"""Shared comparison utilities for generator-compare.

Consolidates the former bag_io.py + labeling.py + head_box.py — the helpers the
per-comparison subfolders (acoular-vs-pytorch/, 1bit-vs-pytorch/) share:

  - ROS2 bag reader + CDR decoders            (was bag_io.py; use `import utils as B`)
  - 4-label sound-map pipeline                (was labeling.py)
  - MediaPipe head-box re-detection           (was head_box.py; mediapipe imported lazily)

Do not change the numeric constants or branch logic in the labeling section; they
define the labels.
"""
from __future__ import annotations

import bisect
import re
import sqlite3
import struct
from pathlib import Path

import cv2
import numpy as np


# ==========================================================================
# bag I/O  (former bag_io.py)
# ==========================================================================
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


# ==========================================================================
# 4-label pipeline  (former labeling.py)
# ==========================================================================
LABELS = ("Left", "Right", "Teleoperator", "Others")

# Fixed teleoperator "speaking box" used as a label region (check_doa.get_speaking_box).
SPEAKING_BOX = (377, 645, 330, 330)  # (x, y, w, h) in 1080x1080 coords


def get_speaking_box():
    return SPEAKING_BOX


# --------------------------------------------------------------------------
# Sound-map transforms (policy_utils.DoADetector)
# --------------------------------------------------------------------------
def transform_sm(sm: np.ndarray) -> np.ndarray:
    """exp(sm - sm.max()) if any positive, else unchanged (DoADetector.transform_sound_map)."""
    if np.max(sm) > 0:
        sm = np.exp(sm - sm.max())
    return sm


def sm_to_color(transformed_sm: np.ndarray, plot_size: int = 1080) -> np.ndarray:
    """Resize to plot_size, scale to uint8, yellow map [0, G, R] (DoADetector.visualize_sm)."""
    plot_sm = cv2.resize(transformed_sm, (plot_size, plot_size), interpolation=cv2.INTER_LINEAR)
    plot_sm = (plot_sm * 255).astype(np.uint8)
    return np.stack([np.zeros_like(plot_sm), plot_sm, plot_sm], axis=-1)


def mask_speaking_box(sm: np.ndarray) -> np.ndarray:
    """Zero the speaking-box region when local VAD is inactive
    (policy_utils.mask_speaking_box_in_sound_map). NOTE the mask box
    (277,645,530,435) differs from the labeling SPEAKING_BOX."""
    masked_sm = sm.copy()
    sm_h, sm_w = masked_sm.shape[:2]
    box_x, box_y, box_w, box_h = 277, 645, 530, 435
    scale_x = sm_w / 1080.0
    scale_y = sm_h / 1080.0

    x1 = max(0, min(sm_w, int(box_x * scale_x)))
    y1 = max(0, min(sm_h, int(box_y * scale_y)))
    x2 = max(0, min(sm_w, int((box_x + box_w) * scale_x)))
    y2 = max(0, min(sm_h, int((box_y + box_h) * scale_y)))

    if x1 < x2 and y1 < y2:
        masked_sm[y1:y2, x1:x2] = 0
    return masked_sm


# --------------------------------------------------------------------------
# Head-box carry-over (check_doa.HeadBoxProcessor)
# --------------------------------------------------------------------------
class HeadBoxProcessor:
    def __init__(self):
        self.last_known_boxes = [None, None]

    def process(self, head_boxes):
        if head_boxes is None:
            return self.last_known_boxes, False

        processed_boxes = []
        was_updated = False
        for i in range(min(len(head_boxes), 2)):
            current_box = head_boxes[i]
            is_valid = not all(coord == -99 for coord in current_box)
            if is_valid:
                self.last_known_boxes[i] = list(current_box)
                processed_boxes.append(list(current_box))
            else:
                if self.last_known_boxes[i] is not None:
                    processed_boxes.append(self.last_known_boxes[i])
                    was_updated = True
                else:
                    processed_boxes.append(list(current_box))
        return processed_boxes, was_updated


# --------------------------------------------------------------------------
# 4-label extraction (check_doa.py, method 7)
# --------------------------------------------------------------------------
def _get_box_bounds(box, w_img, h_img):
    if box is None or any(coord == -99 for coord in box):
        return None
    x, y, w, h = box
    x1 = max(0, min(x, w_img - 1))
    y1 = max(0, min(y, h_img - 1))
    x2 = min(x1 + w, w_img)
    y2 = min(y1 + h, h_img)
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


def _compute_metric(values, mode, percentile_q=None):
    if values.size == 0:
        return None
    if mode == "peak":
        return float(np.max(values))
    if mode == "mean":
        return float(np.mean(values))
    if mode == "percentile":
        return float(np.percentile(values, percentile_q))
    if mode == "tail_mean_from_percentile":
        p = float(np.percentile(values, percentile_q))
        tail_values = values[values >= p]
        if tail_values.size == 0:
            return p
        return float(np.mean(tail_values))
    raise ValueError(f"Unsupported mode: {mode}")


def _extract_by_metric(sm_color, head_boxes, mode, percentile_q=None,
                       speaking_box=SPEAKING_BOX, others_percentile_q=None):
    data = sm_color[:, :, 1]
    h_img, w_img = data.shape
    data_flat = data.reshape(-1)

    left_bounds = _get_box_bounds(head_boxes[0], w_img, h_img) if head_boxes is not None and len(head_boxes) > 0 else None
    right_bounds = _get_box_bounds(head_boxes[1], w_img, h_img) if head_boxes is not None and len(head_boxes) > 1 else None
    tele_bounds = _get_box_bounds(speaking_box, w_img, h_img)

    region_metrics = {"Left": None, "Right": None, "Teleoperator": None, "Others": None}
    region_points = {"Left": None, "Right": None, "Teleoperator": None, "Others": None}

    def eval_box(bounds):
        if bounds is None:
            return None, None
        x1, y1, x2, y2 = bounds
        roi = data[y1:y2, x1:x2]
        if roi.size == 0:
            return None, None
        if mode == "peak":
            _, max_val, _, max_loc = cv2.minMaxLoc(roi)
            point = (x1 + max_loc[0], y1 + max_loc[1])
            return float(max_val), point
        values = roi.reshape(-1)
        metric = _compute_metric(values, mode, percentile_q=percentile_q)
        idx = int(np.argmin(np.abs(values.astype(np.float32) - metric)))
        yy, xx = np.unravel_index(idx, roi.shape)
        return metric, (x1 + int(xx), y1 + int(yy))

    region_metrics["Left"], region_points["Left"] = eval_box(left_bounds)
    region_metrics["Right"], region_points["Right"] = eval_box(right_bounds)
    region_metrics["Teleoperator"], region_points["Teleoperator"] = eval_box(tele_bounds)

    others_mask = np.ones((h_img, w_img), dtype=bool)
    for bounds in (left_bounds, right_bounds, tele_bounds):
        if bounds is None:
            continue
        x1, y1, x2, y2 = bounds
        others_mask[y1:y2, x1:x2] = False

    others_linear = np.flatnonzero(others_mask.reshape(-1))
    if others_linear.size > 0:
        others_values = data_flat[others_linear]
        if mode == "peak":
            idx = int(np.argmax(others_values))
            metric = float(others_values[idx])
        else:
            others_q = others_percentile_q if (mode == "percentile" and others_percentile_q is not None) else percentile_q
            metric = _compute_metric(others_values, mode, percentile_q=others_q)
            idx = int(np.argmin(np.abs(others_values.astype(np.float32) - metric)))
        region_metrics["Others"] = metric
        y_o, x_o = np.unravel_index(int(others_linear[idx]), data.shape)
        region_points["Others"] = (int(x_o), int(y_o))

    valid = {k: v for k, v in region_metrics.items() if v is not None}
    if valid:
        max_val = max(valid.values())
        tied_labels = [k for k, v in valid.items() if np.isclose(v, max_val, rtol=1e-9, atol=1e-9)]
        all_zero = all(np.isclose(v, 0.0, rtol=1e-9, atol=1e-9) for v in valid.values())
        if all_zero:
            label = str(np.random.choice(tied_labels))
        else:
            priority = ["Left", "Right", "Teleoperator", "Others"]
            label = next((name for name in priority if name in tied_labels), tied_labels[0])
    else:
        label = "Others"
    return label, region_metrics, region_points


def extract_target7(sm_color, head_boxes, speaking_box=SPEAKING_BOX):
    """P87.5 for Left/Right/Teleoperator, P98 for Others (the live method)."""
    return _extract_by_metric(sm_color, head_boxes, mode="percentile", percentile_q=87.5,
                              speaking_box=speaking_box, others_percentile_q=98.0)


def run_extract_target(method_id, sm_color, head_boxes, speaking_box=SPEAKING_BOX):
    if method_id != 7:
        raise ValueError(f"Only method 7 is vendored for analysis2 (got {method_id})")
    return extract_target7(sm_color, head_boxes, speaking_box=speaking_box)


# --------------------------------------------------------------------------
# VAD gate (offline analog of policy_utils.was_vad_active_recently)
# --------------------------------------------------------------------------
def vad_active_at(vad_ts, vad_val, t, window=0.25):
    """True if any VAD sample was active in [ref - window, ref], ref = latest vad <= t.
    `vad_ts` seconds ascending, `vad_val` bools. Mirrors the live semantics."""
    j = bisect.bisect_right(vad_ts, t)
    if j == 0:
        return False
    ref = vad_ts[j - 1]
    cutoff = ref - window
    i = bisect.bisect_left(vad_ts, cutoff)
    return any(vad_val[k] for k in range(i, j))


# --------------------------------------------------------------------------
# One-call label helpers (mode_doa / mode_pssp exact sequences)
# --------------------------------------------------------------------------
def label_current_sm(sm_64, head_boxes, vad_active, speaking_box=SPEAKING_BOX):
    """DoA / GT path: (mask if silent) -> transform -> color -> extract7.
    Returns (label, region_metrics, region_points)."""
    sm = sm_64 if vad_active else mask_speaking_box(sm_64)
    color = sm_to_color(transform_sm(sm), plot_size=1080)
    return run_extract_target(7, color, head_boxes, speaking_box=speaking_box)


def label_prediction_sm(pred_sm_64, head_boxes, speaking_box=SPEAKING_BOX):
    """PSSP path: visualize the predicted SM directly (no transform, no mask) -> extract7."""
    color = sm_to_color(pred_sm_64, plot_size=1080)
    return run_extract_target(7, color, head_boxes, speaking_box=speaking_box)


def plot_annotations(blend_img, label, region_averages, head_boxes, speaking_box=SPEAKING_BOX,
                     counts=None, marker_points=None):
    """Overlay boxes + label (check_doa.plot_annotations, for bag2video QC)."""
    COLOR_BLUE = (255, 0, 0)
    COLOR_GREEN = (0, 255, 0)
    COLOR_WHITE = (255, 255, 255)
    COLOR_BLACK = (0, 0, 0)

    def draw_text(img, text, pos, font_scale, thickness, text_color):
        cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, font_scale, COLOR_BLACK, thickness + 2)
        cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, font_scale, text_color, thickness)

    if head_boxes is not None:
        for idx, box in enumerate(head_boxes):
            if any(coord != -99 for coord in box):
                x, y, w, h = box
                target_name = "Left" if idx == 0 else "Right" if idx == 1 else None
                box_color = COLOR_GREEN if label == target_name else COLOR_BLUE
                cv2.rectangle(blend_img, (x, y), (x + w, y + h), box_color, 2)
                cv2.circle(blend_img, (x + w // 2, y + h // 2), 5, box_color, -1)

    tele_target_color = COLOR_GREEN if label == "Teleoperator" else COLOR_BLUE
    if speaking_box is not None and not any(coord == -99 for coord in speaking_box):
        sx, sy, sw, sh = speaking_box
        cv2.rectangle(blend_img, (sx, sy), (sx + sw, sy + sh), tele_target_color, 2)
        cv2.circle(blend_img, (sx + sw // 2, sy + sh // 2), 5, tele_target_color, -1)

    draw_text(blend_img, f"Target: {label}", (10, 72), 1.8, 4, COLOR_WHITE)
    if region_averages is not None or counts is not None:
        y_offset = 138
        for name in ("Left", "Right", "Teleoperator", "Others"):
            val_str = ""
            if region_averages and region_averages.get(name) is not None:
                val_str += f" {region_averages[name]:.2f}"
            if counts and counts.get(name) is not None:
                val_str += f" ({counts[name]})"
            if val_str:
                draw_text(blend_img, f"{name}:{val_str}", (10, y_offset), 1.44, 3, COLOR_WHITE)
            y_offset += 60

    if marker_points is not None:
        for pt in marker_points.values():
            if pt is None:
                continue
            cv2.circle(blend_img, pt, 10, (0, 0, 255), -1)
            cv2.circle(blend_img, pt, 11, (255, 255, 255), 1)


def frame_to_gray64(frame_bgr, sm_size=64):
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) / 255.0
    return cv2.resize(gray, (sm_size, sm_size), interpolation=cv2.INTER_AREA)


def build_clip_frame_from_gray(sm_64, vad_active, gray64):
    """PSSP clip frame from a precomputed gray64 (mode_pssp.update_clip)."""
    sm = sm_64 if vad_active else mask_speaking_box(sm_64)
    transformed = transform_sm(sm).astype(np.float64)
    fused = cv2.addWeighted(transformed, 0.5, gray64, 0.5, 0)
    return np.stack([fused, gray64], axis=0)


def build_clip_frame(sm_64, vad_active, frame_bgr, sm_size=64):
    """PSSP clip frame (mode_pssp.update_clip): [fused, gray], each sm_size x sm_size."""
    return build_clip_frame_from_gray(sm_64, vad_active, frame_to_gray64(frame_bgr, sm_size))


# ==========================================================================
# Head-box re-detection  (former head_box.py; HeadBoxProcessor is defined above)
# ==========================================================================
class HeadDetector:
    """Verbatim from head_node.py."""

    def __init__(self, model_selection: int = 1, min_detection_confidence: float = 0.5,
                 history_max_count: int = 6):
        import mediapipe as mp  # lazy: only head-box re-detection needs it
        self.mp_face_detection = mp.solutions.face_detection
        self.face_detection = self.mp_face_detection.FaceDetection(
            model_selection=model_selection,
            min_detection_confidence=min_detection_confidence,
        )
        self.last_valid_left_box = [-99, -99, -99, -99]
        self.last_valid_right_box = [-99, -99, -99, -99]
        self.max_x_jump_px = 100

    def is_reasonable_update(self, new_box, last_box):
        if last_box[0] == -99:
            return True
        return abs(new_box[0] - last_box[0]) <= self.max_x_jump_px

    def detect_heads(self, img, img_sz=1080):
        rgb_image = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        results = self.face_detection.process(rgb_image)

        head_bounding_boxes = []
        if results.detections:
            image_height, image_width, _ = img.shape
            for detection in results.detections:
                bbox_data = detection.location_data.relative_bounding_box
                x_min = int(bbox_data.xmin * image_width)
                y_min = int(bbox_data.ymin * image_height)
                width = int(bbox_data.width * image_width)
                height = int(bbox_data.height * image_height)

                center_x = x_min + width / 2
                center_y = y_min + height / 2
                new_width = width * 2
                new_height = height * 2
                x_min = int(center_x - new_width / 2)
                y_min = int(center_y - new_height / 2)
                width = int(new_width)
                height = int(new_height)

                x_min = max(0, x_min)
                y_min = max(0, y_min)
                width = min(image_width - x_min, width)
                height = min(image_height - y_min, height)
                head_bounding_boxes.append([x_min, y_min, width, height])

            mid_x = image_width / 2
            left_boxes = []
            right_boxes = []
            for box in head_bounding_boxes:
                box_center_x = box[0] + box[2] / 2
                if box_center_x < mid_x:
                    left_boxes.append(box)
                else:
                    right_boxes.append(box)

            if left_boxes:
                candidate_left = max(left_boxes, key=lambda box: box[2] * box[3])
                if self.is_reasonable_update(candidate_left, self.last_valid_left_box):
                    self.last_valid_left_box = candidate_left
            if right_boxes:
                candidate_right = max(right_boxes, key=lambda box: box[2] * box[3])
                if self.is_reasonable_update(candidate_right, self.last_valid_right_box):
                    self.last_valid_right_box = candidate_right

            head_bounding_boxes = [
                self.last_valid_left_box.copy(),
                self.last_valid_right_box.copy(),
            ]
        else:
            head_bounding_boxes = [
                self.last_valid_left_box.copy(),
                self.last_valid_right_box.copy(),
            ]
        return head_bounding_boxes


class HeadBoxAPI:
    """Stateful: feed room1 frames in chronological order.

    detect(frame_bgr) -> [left_box, right_box] after HeadDetector persistence
    AND HeadBoxProcessor carry-over, matching the live two-stage pipeline.
    `carried` in the return of detect_with_flag() marks a HeadBoxProcessor fallback.
    """

    def __init__(self):
        self.detector = HeadDetector()
        self.hb_processor = HeadBoxProcessor()

    def detect(self, frame_bgr):
        boxes, _ = self.detect_with_flag(frame_bgr)
        return boxes

    def detect_with_flag(self, frame_bgr):
        left, right = self.detector.detect_heads(frame_bgr)
        processed, carried = self.hb_processor.process([left, right])
        return processed, carried
