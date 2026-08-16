#!/usr/bin/env python3
"""第3部データセット生成: Word Wolf G1_game3_Tele の全 756 tick を、VLM 実効入力
756x756・JPEG q100 で送信可能な形に用意する。

各 tick について:
  - RGB(重畳なし) 756 JPEG q100        …… baseline / mode_A で送る
  - 音図オーバーレイ 756 JPEG q100 × α∈{0.3,0.5,0.7}(凸結合) …… mode_B で送る
  - gt を定義したのと同一前段(label_current_sm)の 領域絶対強度＋代表点(1080座標) …… mode_A textifier 用

出力: report/g1data/{idx:03d}_rgb.jpg, _a30/_a50/_a70.jpg + manifest.csv
生成と長時間ネット処理を分離し、collect 側はファイルを読むだけにする(堅牢)。

用法(wolf venv): python build_g1_dataset.py
"""
import csv
import sys
from pathlib import Path

import cv2
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "g1data"
TRIAL2 = Path("/home/chen/Documents/Playground/p6/try-VLM/trial-2")
TICKS = Path("/home/chen/Documents/Playground/word-wolf-exp-eval/"
             "behavior-analysis/results/ticks")
BAG = "G1_game3_Tele"
R = 756
ALPHAS = [0.3, 0.5, 0.7]
JQ = [cv2.IMWRITE_JPEG_QUALITY, 100]
REGIONS = ["Left", "Right", "Teleoperator", "Others"]
_TAG = {"Left": "L", "Right": "R", "Teleoperator": "T", "Others": "O"}

sys.path.insert(0, str(TRIAL2))
from common2 import (gen_sm, frame_at, label_input_sm, bag_dir,       # noqa: E402
                     load_audio)
import bag_io as B                                                    # noqa: E402
from soundmap_api import SoundMapAPI                                  # noqa: E402
import labeling as L                                                  # noqa: E402


def head_boxes(r):
    return [[int(r.hb_lx), int(r.hb_ly), int(r.hb_lw), int(r.hb_lh)],
            [int(r.hb_rx), int(r.hb_ry), int(r.hb_rw), int(r.hb_rh)]]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(TICKS / ("%s.parquet" % BAG))
    con = B.open_bag(bag_dir(BAG))
    a_ts, a_d = load_audio(con)
    cam_tid = B.topic_id(con, B.CAMERA_TOPIC)
    sm_api = SoundMapAPI(device="cpu")

    cols = (["tick_idx", "tick_ts", "gt_label", "vad_active", "rgb"]
            + ["a%02d" % round(a * 100) for a in ALPHAS]
            + [c for t in ("L", "R", "T", "O")
               for c in ("e%s" % t, "%sx" % t.lower(), "%sy" % t.lower())])
    manifest, n_skip = [], 0
    for i, r in enumerate(df.itertuples(index=False)):
        idx = int(r.tick_idx)
        ts, vad = int(r.tick_ts), bool(int(r.vad_active))
        sm = gen_sm(sm_api, a_ts, a_d, ts)
        fr = frame_at(con, cam_tid, ts)
        if sm is None or fr is None:
            n_skip += 1
            continue
        cam1080 = cv2.resize(fr, (1080, 1080), interpolation=cv2.INTER_AREA)
        cam756 = cv2.resize(cam1080, (R, R), interpolation=cv2.INTER_AREA)
        rgb_name = "%03d_rgb.jpg" % idx
        cv2.imwrite(str(OUT / rgb_name), cam756, JQ)
        sm_color = L.sm_to_color(label_input_sm(sm, vad), plot_size=1080)
        _, metrics, points = L.label_current_sm(sm, head_boxes(r), vad)
        row = dict(tick_idx=idx, tick_ts=ts, gt_label=r.gt_label,
                   vad_active=int(vad), rgb=rgb_name)
        for a in ALPHAS:
            blend = cv2.addWeighted(sm_color, a, cam1080, 1.0 - a, 0)
            blend = cv2.resize(blend, (R, R), interpolation=cv2.INTER_AREA)
            nm = "%03d_a%02d.jpg" % (idx, round(a * 100))
            cv2.imwrite(str(OUT / nm), blend, JQ)
            row["a%02d" % round(a * 100)] = nm
        for reg in REGIONS:
            t = _TAG[reg]
            m, pt = metrics.get(reg), points.get(reg)
            row["e%s" % t] = "" if m is None else "%.3f" % (m / 255.0)
            row["%sx" % t.lower()] = "" if pt is None else int(pt[0])
            row["%sy" % t.lower()] = "" if pt is None else int(pt[1])
        manifest.append(row)
        if (i + 1) % 50 == 0:
            print("  %d/%d ticks done (skip %d)" % (i + 1, len(df), n_skip), flush=True)
    con.close()

    with (OUT / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(manifest)
    print("完成: %d tick (skip %d) -> %s" % (len(manifest), n_skip, OUT))


if __name__ == "__main__":
    main()
