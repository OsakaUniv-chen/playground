#!/usr/bin/env python3
"""mode_B のサンプル生成: 音図を彩色相機に**重畳**した画像を、複数の α で再レンダリング。

../../selection.csv の 9 枚(手挑)それぞれを、音図ブレンド α の格子で描き分け、
sample_XX_aNN.png(NN = α*100)として保存する。α 感度(重畳の濃さで VLM の読みが
変わるか)を見るのが mode_B の主眼。

重畳式: addWeighted(sm_color, α, cam1080, CAM_BETA, 0)。実験映像(build_dataset.py /
bag2video.py)と同じ黄色重畳。CAM_BETA は 0.8 固定、α だけ振る。α=0.6 が現行基準。
音図は gt と同じ masked+transformed(label_input_sm)。

用法(wolf venv):
  /home/chen/.virtualenvs/wolf/bin/python gen_samples.py
  /home/chen/.virtualenvs/wolf/bin/python gen_samples.py --alphas 0.3,0.6,0.9
"""
import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
SELECTION = HERE.parents[1] / "selection.csv"
TRIAL2 = Path("/home/chen/Documents/Playground/p6/try-VLM/trial-2")
CAM_BETA = 0.8
DEFAULT_ALPHAS = [0.3, 0.45, 0.6, 0.75, 0.9]

sys.path.insert(0, str(TRIAL2))
from common2 import (gen_sm, frame_at, label_input_sm, bag_dir,       # noqa: E402
                     load_audio)
import bag_io as B                                                    # noqa: E402
from soundmap_api import SoundMapAPI                                  # noqa: E402
import labeling as L                                                  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alphas", default=",".join(str(a) for a in DEFAULT_ALPHAS),
                    help="音図ブレンド α のカンマ区切り(既定 0.3,0.45,0.6,0.75,0.9)")
    args = ap.parse_args()
    alphas = [float(a) for a in args.alphas.split(",") if a.strip()]

    picks = list(csv.DictReader(open(SELECTION)))
    sm_api = SoundMapAPI(device="cpu")
    by_bag = defaultdict(list)
    for p in picks:
        by_bag[p["bag"]].append(p)

    manifest = []
    for bag, items in by_bag.items():
        con = B.open_bag(bag_dir(bag))
        a_ts, a_d = load_audio(con)
        cam_tid = B.topic_id(con, B.CAMERA_TOPIC)
        for p in items:
            ts, vad = int(p["tick_ts"]), bool(int(p["vad_active"]))
            sm = gen_sm(sm_api, a_ts, a_d, ts)
            fr = frame_at(con, cam_tid, ts)
            if sm is None or fr is None:
                print("  skip (no sm/frame):", bag, ts)
                continue
            cam1080 = cv2.resize(fr, (1080, 1080), interpolation=cv2.INTER_AREA)
            sm_color = L.sm_to_color(label_input_sm(sm, vad), plot_size=1080)
            for a in alphas:
                blend = cv2.addWeighted(sm_color, a, cam1080, CAM_BETA, 0)
                name = "%s_a%02d.png" % (p["sample_id"], round(a * 100))
                cv2.imwrite(str(HERE / name), blend)
                manifest.append(dict(sample_id=p["sample_id"], alpha="%.2f" % a,
                                     file=name, bag=bag, tick_ts=ts,
                                     gt_label=p["gt_label"], vad_active=int(vad),
                                     size="%dx%d" % (blend.shape[1], blend.shape[0])))
            print("  %s  gt=%-12s x%d alphas" % (p["sample_id"], p["gt_label"], len(alphas)))
        con.close()

    cols = ["sample_id", "alpha", "file", "bag", "tick_ts", "gt_label", "vad_active", "size"]
    with (HERE / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(manifest)
    print("\n完成: %d 枚(%d 枚 x %d α) -> %s" % (
        len(manifest), len(manifest) // max(1, len(alphas)), len(alphas), HERE))


if __name__ == "__main__":
    main()
