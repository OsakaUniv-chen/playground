#!/usr/bin/env python3
"""第4部 個別例: 2 つの例シーン × 7 手法の実入出力を取る(14 推論)。
画像は 756/q100 で送信。各手法の音源情報テキストと VLM 出力を part4_data.json に保存。"""
import csv
import io
import json
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mode_A"))
sys.path.insert(0, str(ROOT / "mode_B"))
from common import tunnel, protocol, prompt              # noqa: E402
from textifier import make_sound_info                    # noqa: E402
from overlay_info import SOUND_INFO                       # noqa: E402
from combo import make_combo_info                         # noqa: E402

MA = ROOT / "mode_A" / "sample"
MB = ROOT / "mode_B" / "sample"
# 例4(Others)は G1_game3 tick734（綺麗な環境音 Others）を g1_data.json から別途構築するため除外
SCENES = [("例1", "sample_05"), ("例2", "sample_14"), ("例3", "sample_01")]
R = 756


def jpeg756(path):
    im = Image.open(path).convert("RGB").resize((R, R), Image.LANCZOS)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=100)
    return b.getvalue()


def main():
    man = {r["sample_id"]: r for r in csv.DictReader(open(MA / "manifest.csv"))}
    tun = tunnel.start_tunnel(tunnel.DEFAULT_IP, 50017, 50007)
    recs = []
    try:
        if not tunnel.wait_port(50017):
            print("no tunnel", file=sys.stderr)
            return 1
        s = protocol.connect(50017)
        for label, sid in SCENES:
            row = man[sid]
            gt = row["gt_label"]
            rgb = jpeg756(MA / row["file"])
            jobs = [("baseline", "-", rgb, prompt.BASELINE_INFO)]
            for fmt in ("coord", "grid", "nl"):
                jobs.append(("mode_A", fmt, rgb, make_sound_info(row, fmt)))
            for a in (30, 50, 70):
                ov = jpeg756(MB / ("%s_a%02d.png" % (sid, a)))
                jobs.append(("mode_B", "0.%d" % (a // 10), ov, SOUND_INFO))
            jobs.append(("combo", "coord+a50", jpeg756(MB / ("%s_a50.png" % sid)),
                         make_combo_info(row)))
            for cond, method, img, info in jobs:
                protocol.send_request(s, prompt.build(info), img)
                out = protocol.recv_response(s)
                recs.append(dict(scene=label, sample_id=sid, gt=gt, condition=cond,
                                 method=method, sound_info=info, output=out,
                                 correct=bool(prompt.parse_label(out) == gt)))
                print("%s %s %-8s %-6s gt=%-12s -> %s" % (
                    label, sid, cond, method, gt, out))
        s.close()
    finally:
        tun.terminate()
    (HERE / "part4_data.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1))
    print("\n%d recs -> part4_data.json" % len(recs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
