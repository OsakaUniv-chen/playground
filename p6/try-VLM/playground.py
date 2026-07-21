"""Interactive VLM playground — tweak the instruction, watch the output.

Model loads ONCE; then you edit `prompt.txt` in your editor, come back to the
terminal, press Enter, and see the new answer on the same image(s). No reload.

Quick start:
    source ~/.virtualenvs/wolf/bin/activate
    python playground.py                       # default sample + prompt.txt, 3B (cached)
    python playground.py --model Qwen/Qwen2.5-VL-7B-Instruct   # once 7B is downloaded
    python playground.py --image playground_samples/overlay_Left.png
    python playground.py --image a.png --image b.png           # feed 2 images (e.g. gray + soundmap)

At the prompt after each answer:
    <Enter>           re-run (re-reads prompt.txt + image, so your edits apply)
    img A.png [B.png] switch the input image(s)
    p                 print the current prompt
    q                 quit

Default inputs live in playground_samples/ (run prepare_samples.py to (re)make them);
each filename carries its ground-truth label so you can sanity-check the answer.

Note: needs ~3-6 GB free VRAM. Don't run it at the same time as a 7B probe job.
"""
from __future__ import annotations
import argparse
from pathlib import Path

import torch
from transformers import (Qwen2_5_VLForConditionalGeneration, AutoProcessor,
                          BitsAndBytesConfig)
from qwen_vl_utils import process_vision_info

HERE = Path(__file__).parent
PROMPT_FILE = HERE / "prompt.txt"
SAMPLES = HERE / "playground_samples"

DEFAULT_PROMPT = """This image is a fisheye camera view of a room with a sound-energy heatmap overlaid (jet colormap: blue = quiet, red = loudest). Two people sit on the left and right; the lower-centre region (over the table) is where a remote teleoperator's voice appears.

Where is the sound coming from right now — the left person, the right person, the teleoperator (lower-centre), or nowhere clear? Explain what you see in the heatmap, then give a one-word answer."""


def load_model(model_id: str, bf16: bool):
    kw = dict(device_map="cuda", torch_dtype=torch.bfloat16)
    if not bf16:
        kw["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
    print(f"loading {model_id} ({'bf16' if bf16 else '4-bit'}) … (~30 s)")
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(model_id, **kw).eval()
    processor = AutoProcessor.from_pretrained(model_id)
    return model, processor


def run(model, processor, images: list[str], prompt: str, max_new_tokens: int) -> str:
    content = [{"type": "image", "image": str(p)} for p in images]
    content.append({"type": "text", "text": prompt})
    messages = [{"role": "user", "content": content}]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = processor(text=[text], images=image_inputs, videos=video_inputs,
                       padding=True, return_tensors="pt").to("cuda")
    with torch.no_grad():
        gen = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
    trimmed = [o[len(i):] for i, o in zip(inputs.input_ids, gen)]
    return processor.batch_decode(trimmed, skip_special_tokens=True)[0].strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen2.5-VL-3B-Instruct")
    ap.add_argument("--bf16", action="store_true")
    ap.add_argument("--image", action="append", default=None,
                    help="input image (repeatable). default: a prepared sample")
    ap.add_argument("--prompt-file", default=str(PROMPT_FILE))
    ap.add_argument("--max-new-tokens", type=int, default=200)
    ap.add_argument("--once", action="store_true", help="run once and exit (no REPL)")
    args = ap.parse_args()

    pfile = Path(args.prompt_file)
    if not pfile.exists():
        pfile.write_text(DEFAULT_PROMPT)
        print(f"wrote default instruction -> {pfile}  (edit this file to change the prompt)")

    images = args.image or [str(SAMPLES / "overlay_Right.png")]
    for im in images:
        if not Path(im).exists():
            print(f"!! image not found: {im}  (run prepare_samples.py?)")

    model, processor = load_model(args.model, args.bf16)

    def one():
        prompt = Path(args.prompt_file).read_text().strip()
        print("\n" + "─" * 70)
        print("images :", ", ".join(images))
        print("prompt :", (prompt[:200] + ("…" if len(prompt) > 200 else "")))
        print("─" * 70)
        out = run(model, processor, images, prompt, args.max_new_tokens)
        print("VLM ►", out)
        print("─" * 70)

    one()
    if args.once:
        return
    print("\nEdit prompt.txt, then press Enter to re-run.  Commands: img <p...> | p | q")
    while True:
        try:
            cmd = input("\n[Enter=rerun | img <path...> | p | q] > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if cmd == "q":
            break
        if cmd == "p":
            print(Path(args.prompt_file).read_text())
            continue
        if cmd.startswith("img "):
            newimgs = cmd[4:].split()
            missing = [p for p in newimgs if not Path(p).exists()]
            if missing:
                print("!! not found:", missing); continue
            images[:] = newimgs
        one()


if __name__ == "__main__":
    main()
