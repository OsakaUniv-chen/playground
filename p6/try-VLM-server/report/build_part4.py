#!/usr/bin/env python3
"""part4_data.json -> 第4部の Markdown(例シーンごとの手法別 実入出力)。標準出力に出す。"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREFIX = "A sound-localization front-end has located the current strongest sound. "
FIG = {"例1": "fig/ex1_alpha_756.png", "例2": "fig/ex2_alpha_756.png",
       "例3": "fig/ex3_alpha_756.png", "例4": "fig/ex4_alpha_756.png"}
MNAME = {("baseline", "-"): "baseline", ("mode_A", "coord"): "mode_A: coord",
         ("mode_A", "grid"): "mode_A: grid(3×3)", ("mode_A", "nl"): "mode_A: nl",
         ("mode_B", "0.3"): "mode_B: α=0.3", ("mode_B", "0.5"): "mode_B: α=0.5",
         ("mode_B", "0.7"): "mode_B: α=0.7",
         ("combo", "coord+a50"): "combo: coord + α=0.5"}


def info_cell(r):
    if r["condition"] == "mode_B":
        return "§2.4 の黄色重畳プロンプト（全 α 共通）"
    if r["condition"] == "combo":
        return "α=0.5 重畳 ＋ ピーク座標を両方提示（§2.5）"
    t = r["sound_info"]
    if t.startswith(PREFIX):
        return "… " + t[len(PREFIX):]
    return t


def input_cell(r):
    if r["condition"] == "mode_B":
        return "α=%s 重畳" % r["method"]
    if r["condition"] == "combo":
        return "α=0.5 重畳"
    return "RGB"


def main():
    recs = json.loads((HERE / "part4_data.json").read_text())
    scenes = {}
    for r in recs:
        scenes.setdefault(r["scene"], []).append(r)

    print("## 第4部　個別例")
    print()
    print("4 つの例シーンで各手法の実入出力を示す（precision/recall・混同行列は載せない）。入力は 756×756 q100。")
    print("baseline / mode_A は素の **RGB** を入力（画像は再掲しない）。mode_B は各シーンの **α 重畳画像**（下図）を入力。")
    for scene, rs in scenes.items():
        gt = rs[0]["gt"]
        print()
        print("### %s（gt = %s）" % (scene, gt))
        print()
        print('<img src="%s" width="100%%">' % FIG[scene])
        print()
        print("この例のシーン（α=0.3/0.5/0.7 の重畳＝mode_B の入力。素の RGB が baseline / mode_A の入力）")
        print()
        print("| 手法 | 入力 | 音源情報（プロンプト該当部） | VLM 出力 | 正誤 |")
        print("|---|---|---|---|---|")
        for r in rs:
            mark = "✓" if r["correct"] else "✗"
            print("| %s | %s | %s | **%s** | %s |" % (
                MNAME[(r["condition"], r["method"])], input_cell(r), info_cell(r),
                r["output"], mark))


if __name__ == "__main__":
    main()
