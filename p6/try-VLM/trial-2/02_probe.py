"""Trial-2 probe: VLM names the sound source among the 4-label.

Input per tick = one image (gray fisheye + jet sound-map overlay). The prompt
gives the scene layout and the user's region hint ("when the red peak sits in
the lower-centre region, it's the teleoperator"). The VLM must output one of
Left / Right / Teleoperator / Others. We score 4-label accuracy vs gt_label.

Model: Qwen2.5-VL-3B-Instruct, 4-bit (fits 8GB; --bf16 / --model to change).
Random baseline for 4 balanced classes = 25%.
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

from common2 import LABELS

HERE = Path(__file__).parent
IMG_DIR = HERE / "images"
MANIFEST = HERE / "manifest2.csv"
RESULTS = HERE / "results"
MODEL_ID = "Qwen/Qwen2.5-VL-3B-Instruct"

PROMPT = """This is a fisheye camera view of a Word Wolf game room, with a sound-energy heatmap overlaid on it (jet colormap: blue = quiet, green/yellow = medium, red = loudest). The red region shows where sound is coming from RIGHT NOW.

The scene has three possible sound sources:
- Two local players: one seated on the LEFT side, one seated on the RIGHT side of the view.
- A remote Teleoperator, whose voice comes out at the LOWER-CENTRE region of the image (over the table / robot in the middle-bottom). So when the red peak sits in that lower-centre region, the teleoperator is the one speaking.

Question: at this instant, which of these 4 is the sound source?
- Left        : the left player is speaking (red peak on the left person)
- Right       : the right player is speaking (red peak on the right person)
- Teleoperator: the remote operator is speaking (red peak in the lower-centre region)
- Others      : none of the above (no clear source / elsewhere / quiet)

Give one short reason, then on the final line write only the answer word,
one of these four: Left, Right, Teleoperator, Others.
Final line format -> ANSWER: word"""


def parse_label(text: str):
    m = re.findall(r"ANSWER:\s*([A-Za-z]+)", text)
    # only fall back to a standalone label token, never to option-list echoes
    fallback = [w for w in re.findall(r"\b(Left|Right|Teleoperator|Others)\b", text)]
    cands = ([m[-1]] if m else []) + fallback[::-1]
    for c in cands:
        cl = c.lower()
        for lab in LABELS:
            if cl == lab.lower() or (cl in ("tele", "teleop", "operator") and lab == "Teleoperator"):
                return lab
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model", default=MODEL_ID)
    ap.add_argument("--bf16", action="store_true")
    ap.add_argument("--out", default=str(RESULTS / "probe_results.csv"))
    args = ap.parse_args()

    RESULTS.mkdir(exist_ok=True)
    rows = list(csv.DictReader(MANIFEST.open()))
    if args.limit:
        rows = rows[:args.limit]

    load_kw = dict(device_map="cuda", torch_dtype=torch.bfloat16)
    if not args.bf16:
        load_kw["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
    print(f"loading {args.model} ({'bf16' if args.bf16 else '4-bit'}) ...")
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(args.model, **load_kw)
    processor = AutoProcessor.from_pretrained(args.model)
    model.eval()

    out_rows = []
    for r in rows:
        messages = [{"role": "user", "content": [
            {"type": "image", "image": str(IMG_DIR / f"{r['id']}.png")},
            {"type": "text", "text": PROMPT}]}]
        text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = processor(text=[text], images=image_inputs, videos=video_inputs,
                           padding=True, return_tensors="pt").to("cuda")
        with torch.no_grad():
            gen = model.generate(**inputs, max_new_tokens=96, do_sample=False)
        trimmed = [o[len(i):] for i, o in zip(inputs.input_ids, gen)]
        resp = processor.batch_decode(trimmed, skip_special_tokens=True)[0].strip()
        pred = parse_label(resp)
        ok = int(pred == r["gt_label"])
        out_rows.append(dict(id=r["id"], gt_label=r["gt_label"], pred=pred or "",
                             correct=ok, vad_active=r["vad_active"],
                             response=resp.replace("\n", " ")))
        print(f"{r['id']}  gt={r['gt_label']:<12} pred={str(pred):<12} {'OK' if ok else ''}")

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader(); w.writerows(out_rows)

    n = len(out_rows)
    acc = sum(o["correct"] for o in out_rows) / n
    parsed = sum(1 for o in out_rows if o["pred"])
    print("\n" + "=" * 40)
    print(f"model: {args.model}")
    print(f"n={n} parsed={parsed}/{n}")
    print(f"4-label accuracy: {acc:.1%}  (random 25%)")
    # per-class recall
    from collections import Counter
    tot = Counter(o["gt_label"] for o in out_rows)
    hit = Counter(o["gt_label"] for o in out_rows if o["correct"])
    for lab in LABELS:
        if tot[lab]:
            print(f"  {lab:<12} recall {hit[lab]}/{tot[lab]} = {hit[lab]/tot[lab]:.0%}")
    print("=" * 40)


if __name__ == "__main__":
    main()
