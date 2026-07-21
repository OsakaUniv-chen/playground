"""Stage-1 probe: can the VLM read the dominant sound direction off the heatmap?

For each rendered pure-jet heatmap, ask the VLM which clock direction (1-12) the
loudest blob is in, and compare to GT (derived from the exact map shown).

Model: Qwen2.5-VL-3B-Instruct (bf16, ~7GB) -- fits a 4070 laptop (8GB) and is the
local deployment target for P6. Swap MODEL_ID to try the 7B/4-bit variant.

Scores reported:
  clock-exact     pred == gt
  clock +-1       circular clock distance <= 1  (i.e. within 30 deg)
  quadrant        up/right/down/left agree
Random baselines: exact 1/12=8.3%, +-1 3/12=25%, quadrant 25%.

Outputs: results/localize_results.csv  and a printed summary.
"""
from __future__ import annotations
import argparse
import csv
import re
from pathlib import Path

import torch
from transformers import (Qwen2_5_VLForConditionalGeneration, AutoProcessor,
                          BitsAndBytesConfig)
from qwen_vl_utils import process_vision_info

from common import clock_circ_dist, azimuth_to_quadrant, QUAD_NAMES

HERE = Path(__file__).parent
IMG_DIR = HERE / "images"
MANIFEST = HERE / "manifest.csv"
RESULTS = HERE / "results"
MODEL_ID = "Qwen/Qwen2.5-VL-3B-Instruct"

PROMPT = """You are given a 2D acoustic heatmap produced by a microphone array. It shows where sound energy is coming from in a room. The view is an omnidirectional (fisheye) camera looking over the whole room: the CENTER of the image is directly under the sensor, and directions radiate outward toward the edges.

Color encodes sound energy: dark blue = quiet, cyan/green = medium, yellow/red = loud. There is one dominant sound source: the main yellow/red blob.

Read the image as a clock face centered on the middle of the image:
- top edge = 12 o'clock
- right edge = 3 o'clock
- bottom edge = 6 o'clock
- left edge = 9 o'clock

Question: In which clock direction (an integer from 1 to 12) is the center of the dominant (reddest) blob located, as seen from the center of the image?

Reason in one short sentence, then end with exactly one line:
ANSWER: <a single integer 1-12>"""


def parse_clock(text: str) -> int | None:
    m = re.findall(r"ANSWER:\s*(\d{1,2})", text, re.IGNORECASE)
    if m:
        v = int(m[-1])
        return v if 1 <= v <= 12 else None
    nums = [int(n) for n in re.findall(r"\b(\d{1,2})\b", text) if 1 <= int(n) <= 12]
    return nums[-1] if nums else None


def clock_to_quadrant(c: int) -> str:
    return azimuth_to_quadrant((c % 12) * 30.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="probe only first N (0=all)")
    ap.add_argument("--model", default=MODEL_ID)
    ap.add_argument("--bf16", action="store_true",
                    help="full bf16 (~7.5GB, needs a mostly-idle 8GB GPU); "
                         "default is 4-bit (~3GB), the safe fit for a 4070 laptop")
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--imgdir", default=str(IMG_DIR))
    ap.add_argument("--out", default=str(RESULTS / "localize_results.csv"))
    args = ap.parse_args()

    img_dir = Path(args.imgdir)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(open(args.manifest)))
    if args.limit:
        rows = rows[:args.limit]

    load_kw = dict(device_map="cuda", torch_dtype=torch.bfloat16)
    if not args.bf16:
        load_kw["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
    print(f"loading {args.model} ({'bf16' if args.bf16 else '4-bit nf4'}) ...")
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(args.model, **load_kw)
    processor = AutoProcessor.from_pretrained(args.model)
    model.eval()

    out_rows = []
    for r in rows:
        img = str(img_dir / f"{r['id']}.png")
        messages = [{"role": "user", "content": [
            {"type": "image", "image": img},
            {"type": "text", "text": PROMPT}]}]
        text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = processor(text=[text], images=image_inputs, videos=video_inputs,
                           padding=True, return_tensors="pt").to("cuda")
        with torch.no_grad():
            gen = model.generate(**inputs, max_new_tokens=96, do_sample=False)
        trimmed = [o[len(i):] for i, o in zip(inputs.input_ids, gen)]
        resp = processor.batch_decode(trimmed, skip_special_tokens=True,
                                      clean_up_tokenization_spaces=False)[0].strip()
        pred = parse_clock(resp)
        gt = int(r["gt_clock"])
        exact = pred == gt
        near = pred is not None and clock_circ_dist(pred, gt) <= 1
        quad_ok = pred is not None and clock_to_quadrant(pred) == r["gt_quadrant"]
        out_rows.append(dict(id=r["id"], gt_clock=gt, pred_clock=pred if pred else "",
                             exact=int(exact), within1=int(near), quad_ok=int(quad_ok),
                             gt_quadrant=r["gt_quadrant"],
                             response=resp.replace("\n", " ")))
        print(f"{r['id']}  gt={gt:2d}  pred={str(pred):>4}  "
              f"{'EXACT' if exact else ('~1' if near else '  ')}")

    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader(); w.writerows(out_rows)

    n = len(out_rows)
    parsed = sum(1 for o in out_rows if o["pred_clock"] != "")
    ex = sum(o["exact"] for o in out_rows)
    w1 = sum(o["within1"] for o in out_rows)
    qd = sum(o["quad_ok"] for o in out_rows)
    print("\n" + "=" * 44)
    print(f"model: {args.model}")
    print(f"n={n}  parsed={parsed}/{n}")
    print(f"clock-exact : {ex}/{n} = {ex/n:.1%}   (random 8.3%)")
    print(f"clock +-1   : {w1}/{n} = {w1/n:.1%}   (random 25%)")
    print(f"quadrant    : {qd}/{n} = {qd/n:.1%}   (random 25%)")
    print("=" * 44)


if __name__ == "__main__":
    main()
