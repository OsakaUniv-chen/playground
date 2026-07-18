"""Render a QC video: room2 camera feed + head-orientation overlay (FaceMesh
key points, yaw/pitch/roll readout, a yaw needle) for a single bag. Used to
visually spot-check surprising head_orientation.py results, e.g. G5_game4_Tele
whose yaw barely leaves a narrow band around -12 deg (see report2/report.md
§2.1) -- confirm this is a genuinely still head, not a detection artifact.

    python render_tele_orientation_video.py G5_game4_Tele
"""
import os
import sys

import cv2
import numpy as np

_UTILS = "/home/chen/Documents/Playground/word-wolf-exp-eval/utils"
sys.path.insert(0, _UTILS)
import bag_io as B
from head_orientation import HeadOrientationAPI

BAG_ROOT = B.resolve_bag_root()
OUT_DIR = "/home/chen/Documents/Playground/word-wolf-exp-eval/behavior-analysis/results/qc_video"


def main(bag_name: str):
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"{bag_name}_head_orientation.mp4")

    con = B.open_bag(os.path.join(BAG_ROOT, bag_name))
    tid = B.topic_id(con, B.ROOM2_CAMERA_TOPIC)
    rows = con.execute(
        "SELECT timestamp, data FROM messages WHERE topic_id=? ORDER BY timestamp", (tid,)
    ).fetchall()
    con.close()
    print(f"{bag_name}: {len(rows)} room2 frames")

    api = HeadOrientationAPI()
    # reuse the same FaceMesh instance to also grab landmarks for drawing
    face_mesh = api.face_mesh
    W, H = api.img_width, api.img_height

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_path, fourcc, 30.0, (W, H))

    try:
        for i, (_, data) in enumerate(rows):
            frame = B.decode_compressed_image(data)
            if frame is None:
                continue
            img = cv2.flip(frame, 1)
            img = cv2.resize(img, (W, H))
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            res = face_mesh.process(rgb)

            vis = img.copy()
            yaw_txt = "no face"
            if res.multi_face_landmarks:
                lm = res.multi_face_landmarks[0].landmark
                for idx in api.landmark_indices:
                    x, y = int(lm[idx].x * W), int(lm[idx].y * H)
                    cv2.circle(vis, (x, y), 4, (0, 255, 0), -1)

            det = api.detect(frame)  # runs its own flip/resize/detect internally
            if det is not None:
                pitch, yaw, roll = det
                yaw_txt = f"yaw={yaw:+d} pitch={pitch:+d} roll={roll:+d}"
                # yaw needle: horizontal bar, deflection proportional to yaw
                cx, cy = W // 2, H - 40
                needle_len = 100
                ang = np.deg2rad(max(-90, min(90, yaw)))
                ex = int(cx - needle_len * np.sin(ang))
                ey = int(cy - needle_len * np.cos(ang))
                cv2.line(vis, (cx, cy), (ex, ey), (0, 0, 255), 3)
                cv2.circle(vis, (cx, cy), 5, (255, 255, 255), -1)

            cv2.putText(vis, yaw_txt, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (0, 0, 0), 4)
            cv2.putText(vis, yaw_txt, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (255, 255, 255), 2)
            writer.write(vis)

            if i % 1000 == 0:
                print(f"  {i}/{len(rows)}", flush=True)
    finally:
        writer.release()
        face_mesh.close()

    print(f"wrote {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "G5_game4_Tele")
