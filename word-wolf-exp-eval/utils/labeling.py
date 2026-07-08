"""Sound-map labeling pipeline (4-label: Left / Right / Teleoperator / Others).

Extracted verbatim from the live robot code so the offline analysis reproduces
the exact decision path:
  - check_doa.py : get_speaking_box, _get_box_bounds, _compute_metric,
                   _extract_by_metric, extract_target7, run_extract_target,
                   HeadBoxProcessor
  - policy_utils.py : mask_speaking_box_in_sound_map, transform_sound_map,
                      visualize_sm, was_vad_active_recently

Do not change the numeric constants or the branch logic; they define the labels.
"""
from __future__ import annotations

import bisect

import cv2
import numpy as np

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
