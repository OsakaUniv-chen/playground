#!/usr/bin/env python3
"""mode_A のサンプル生成: プレーン RGB(重畳なし) + 音源の忠実な読み取り -> manifest。

音源のテキスト化は、gt_label を定義したのと**同一の前段** `label_current_sm`
(頭部ボックス + P87.5/P98 領域メトリクス + 領域代表点、labeling.py)から作る。こうすると:
  * gt と同じ数値なので再生成のブレが無く忠実(旧・大域ピーク方式は静かな tick で
    座標が不安定だった)、
  * 各方向の**絶対強度**(0=静か〜1=最大)が出るので、静か/Others の手がかりが数値から
    立ち(別途フラグ不要)、
  * textifier は「どの領域が最強か(=argmax)」と代表点/強度だけ使い、gt_label という
    "答えの単語"は一切渡さない。

../../selection.csv の 9 枚について、プレーン RGB(1080、重畳なし)を保存し、
Left/Right/Teleoperator/Others 各領域の 絶対強度 と 代表点(x,y) を manifest に書く。
頭部ボックスは word-wolf の tick 表(behavior-analysis/results/ticks/{bag}.parquet)から。

用法(wolf venv):
  /home/chen/.virtualenvs/wolf/bin/python gen_samples.py
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import pandas as pd

HERE = Path(__file__).resolve().parent
SELECTION = HERE.parents[1] / "selection.csv"
TRIAL2 = Path("/home/chen/Documents/Playground/p6/try-VLM/trial-2")
TICKS = Path("/home/chen/Documents/Playground/word-wolf-exp-eval/"
             "behavior-analysis/results/ticks")
REGIONS = ["Left", "Right", "Teleoperator", "Others"]
_TAG = {"Left": "L", "Right": "R", "Teleoperator": "T", "Others": "O"}

sys.path.insert(0, str(TRIAL2))
from common2 import gen_sm, frame_at, bag_dir, load_audio             # noqa: E402
import bag_io as B                                                    # noqa: E402
from soundmap_api import SoundMapAPI                                  # noqa: E402
import labeling as L                                                  # noqa: E402


def head_boxes_at(bag, ts):
    """tick 表から (left, right) 頭部ボックス [x,y,w,h] を取る。-99 は無効。"""
    df = pd.read_parquet(TICKS / ("%s.parquet" % bag))
    r = df.loc[df.tick_ts == ts]
    if r.empty:
        return None
    r = r.iloc[0]
    return [[int(r.hb_lx), int(r.hb_ly), int(r.hb_lw), int(r.hb_lh)],
            [int(r.hb_rx), int(r.hb_ry), int(r.hb_rw), int(r.hb_rh)]]


def main():
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
            hb = head_boxes_at(bag, ts)
            if sm is None or fr is None or hb is None:
                print("  skip (no sm/frame/hb):", bag, ts)
                continue
            # プレーン RGB(重畳なし) = mode_A の入力
            cam1080 = cv2.resize(fr, (1080, 1080), interpolation=cv2.INTER_AREA)
            name = "%s.png" % p["sample_id"]
            cv2.imwrite(str(HERE / name), cam1080)
            # gt と同一の前段: 領域メトリクス(0-255) + 代表点(1080座標)
            label, metrics, points = L.label_current_sm(sm, hb, vad)
            row = dict(sample_id=p["sample_id"], file=name, bag=bag, tick_ts=ts,
                       gt_label=p["gt_label"], vad_active=int(vad),
                       size="%dx%d" % (cam1080.shape[1], cam1080.shape[0]),
                       argmax=label)
            for reg in REGIONS:
                t = _TAG[reg]
                m = metrics.get(reg)
                pt = points.get(reg)
                row["e%s" % t] = "" if m is None else "%.3f" % (m / 255.0)
                row["%sx" % t.lower()] = "" if pt is None else int(pt[0])
                row["%sy" % t.lower()] = "" if pt is None else int(pt[1])
            manifest.append(row)
            flag = "" if label == p["gt_label"] else "  (argmax!=gt)"
            print("  wrote %s  gt=%-12s  eL/R/T/O=%s%s" % (
                name, p["gt_label"],
                "/".join(row["e%s" % _TAG[r]] or "-" for r in REGIONS), flag))
        con.close()

    cols = (["sample_id", "file", "bag", "tick_ts", "gt_label", "vad_active", "size", "argmax"]
            + [c for t in ("L", "R", "T", "O")
               for c in ("e%s" % t, "%sx" % t.lower(), "%sy" % t.lower())])
    with (HERE / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(manifest)
    print("\n完成: %d 枚 -> %s" % (len(manifest), HERE))


if __name__ == "__main__":
    main()
