"""Head-box re-detection (MediaPipe FaceDetection), faithful to head_node.HeadDetector.

The live system runs HeadDetector in head_node (with its own last-valid persistence
and an x-jump reject) and publishes 8 ints; the mode nodes then apply
HeadBoxProcessor on top. Offline we reproduce BOTH layers here:
  HeadBoxAPI.detect(frame) -> [left_box, right_box]  (1080x1080 coords, -99 = invalid)
"""
from __future__ import annotations

import cv2
import numpy as np
import mediapipe as mp

from labeling import HeadBoxProcessor


class HeadDetector:
    """Verbatim from head_node.py."""

    def __init__(self, model_selection: int = 1, min_detection_confidence: float = 0.5,
                 history_max_count: int = 6):
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
