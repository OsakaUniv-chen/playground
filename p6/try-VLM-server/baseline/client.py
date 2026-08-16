#!/usr/bin/env python3
"""baseline クライアント: 純 RGB + プロンプトのみ(音図情報なし)で話者を当てさせる。

mode_A / mode_B の対照(ablation)。提案書のコア主張「視覚のみでは対象選択を誤る →
音図で補う」を検証するため、**音図を一切与えず**、VLM が視覚だけで話者を当てられるかを見る。
入力画像は mode_A の**プレーン RGB を流用**(独自 sample を持たない)。共通足場は 3 条件で
同一、違いは音源情報ブロックだけ(baseline は common.prompt.BASELINE_INFO = 情報なし)。

前提: 3090 で server 起動。用法(wolf venv):
  python client.py --sample sample_06
"""
import argparse
import csv
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MODE_A_SAMPLE = ROOT / "mode_A" / "sample"          # プレーン RGB を流用
sys.path.insert(0, str(ROOT))
from common import tunnel, protocol, prompt           # noqa: E402


def load_manifest():
    return {r["sample_id"]: r for r in csv.DictReader(open(MODE_A_SAMPLE / "manifest.csv"))}


def main():
    ap = argparse.ArgumentParser(description="baseline client (音図なし・視覚のみ)")
    ap.add_argument("--sample", required=True, help="sample_id 例 sample_06")
    ap.add_argument("--quality", type=int, default=70)
    ap.add_argument("--ip", default=tunnel.DEFAULT_IP)
    ap.add_argument("--local-port", type=int, default=50017)
    ap.add_argument("--remote-port", type=int, default=50007)
    ap.add_argument("--show-prompt", action="store_true")
    args = ap.parse_args()

    man = load_manifest()
    if args.sample not in man:
        print("!! 未知の sample: %s (候補: %s)" % (args.sample, ", ".join(man)), file=sys.stderr)
        return 1
    row = man[args.sample]
    img_path = MODE_A_SAMPLE / row["file"]
    full_prompt = prompt.build(prompt.BASELINE_INFO)
    if args.show_prompt:
        print("---- PROMPT ----\n%s\n----------------\n" % full_prompt)

    print("SSH 端口転送 %d -> 3090:%d ..." % (args.local_port, args.remote_port))
    tun = tunnel.start_tunnel(args.ip, args.local_port, args.remote_port)
    try:
        if not tunnel.wait_port(args.local_port):
            print("!! 隧道未就绪。remote server を確認。", file=sys.stderr)
            return 1
        sock = protocol.connect(args.local_port)
        img = protocol.encode_jpeg(img_path, args.quality)
        t0 = time.perf_counter()
        protocol.send_request(sock, full_prompt, img)
        out = protocol.recv_response(sock)
        dt = (time.perf_counter() - t0) * 1000.0
        sock.close()
        if out is None:
            print("!! server が接続を閉じた", file=sys.stderr)
            return 1
        pred = prompt.parse_label(out)
        print("sample=%s  gt=%s  pred=%s  %.0fms" % (args.sample, row["gt_label"], pred, dt))
        print("---- VLM ----\n%s" % out)
    finally:
        tun.terminate()
        try:
            tun.wait(timeout=5)
        except Exception:
            tun.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
