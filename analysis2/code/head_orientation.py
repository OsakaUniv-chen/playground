"""Teleoperator head-orientation detection (MediaPipe FaceMesh + solvePnP).

Verbatim from policy_utils.HeadOrientationDetector, stripped of the ROS display
code. The internal cv2.flip and the pitch/yaw/roll sign conventions are kept
bit-identical to the live Tele node so `yaw_to_side` reproduces Tele decisions.

Stateful (FaceMesh tracking): one instance per room2 video stream, frames fed
in chronological order.
"""
from __future__ import annotations

import cv2
import numpy as np
import mediapipe as mp


class HeadOrientationAPI:
    def __init__(self, img_width: int = 640, img_height: int = 480):
        self.img_width = img_width
        self.img_height = img_height

        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self.landmark_indices = [1, 226, 446, 61, 291, 199]

        self.model_points = np.array([
            (0.0, 0.0, 0.0),          # Nose tip
            (-225.0, 170.0, -135.0),  # Left eye
            (225.0, 170.0, -135.0),   # Right eye
            (-150.0, -150.0, -125.0), # Left mouth
            (150.0, -150.0, -125.0),  # Right mouth
            (0.0, -330.0, -65.0),     # Chin
        ], dtype="double")

        focal_length = self.img_width
        center = (self.img_width / 2, self.img_height / 2)
        self.camera_matrix = np.array([
            [focal_length, 0, center[0]],
            [0, focal_length, center[1]],
            [0, 0, 1],
        ], dtype="double")
        self.dist_coeffs = np.zeros((4, 1))

    def detect(self, img):
        """Return (pitch, yaw, roll) in degrees (ints), or None if no face.

        Keeps the live flip/resize and the pitch remapping exactly.
        """
        img = cv2.flip(img, 1)
        img = cv2.resize(img, (self.img_width, self.img_height))

        image_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        results = self.face_mesh.process(image_rgb)

        if results.multi_face_landmarks:
            for face_landmarks in results.multi_face_landmarks:
                face_2d_points = []
                for idx in self.landmark_indices:
                    lm = face_landmarks.landmark[idx]
                    x = int(lm.x * self.img_width)
                    y = int(lm.y * self.img_height)
                    face_2d_points.append((x, y))
                face_2d_points = np.array(face_2d_points, dtype="double")

                success, rotation_vector, translation_vector = cv2.solvePnP(
                    self.model_points, face_2d_points,
                    self.camera_matrix, self.dist_coeffs,
                    flags=cv2.SOLVEPNP_ITERATIVE,
                )
                if success:
                    rmat, _ = cv2.Rodrigues(rotation_vector)
                    proj = np.hstack((rmat, translation_vector))
                    euler_angles = cv2.decomposeProjectionMatrix(proj)[6]
                    pitch, yaw, roll = [int(e) for e in euler_angles]
                    if pitch >= 0:
                        pitch = 180 - pitch
                    else:
                        pitch = -180 - pitch
                    return pitch, yaw, roll
        return None

    @staticmethod
    def yaw_to_side(yaw) -> str:
        """Tele decision rule (mode_tele): yaw > 0 -> 'left', else 'right'."""
        return "left" if yaw > 0 else "right"
