#!/usr/bin/env python3
"""mode_B クライアント: 音図を重畳した画像を送り、話者を当てさせる。

提案書 2.4 の (B) 画像重畳。**可変なのは --sample と --alpha だけ**。プロンプトは
共通足場(common/prompt.py)+ 黄色オーバーレイの読み方(overlay_info.py)で、mode_A と
同じ足場・違うのは音源情報だけ。

前提: 3090 で server 起動。sample/ は gen_samples.py で α 別に生成済みであること。

用法(wolf venv):
  python client.py --sample sample_06 --alpha 0.6
"""
import argparse
import csv
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from common import tunnel, protocol, prompt            # noqa: E402
from overlay_info import SOUND_INFO                     # noqa: E402


def load_manifest():
    return list(csv.DictReader(open(HERE / "sample" / "manifest.csv")))


def main():
    ap = argparse.ArgumentParser(description="mode_B client (画像重畳)")
    ap.add_argument("--sample", required=True, help="sample_id 例 sample_06")
    ap.add_argument("--alpha", default="0.60", help="音図ブレンド α(例 0.6)")
    ap.add_argument("--quality", type=int, default=70)
    ap.add_argument("--ip", default=tunnel.DEFAULT_IP)
    ap.add_argument("--local-port", type=int, default=50017)
    ap.add_argument("--remote-port", type=int, default=50007)
    ap.add_argument("--show-prompt", action="store_true")
    args = ap.parse_args()

    a = "%.2f" % float(args.alpha)
    man = load_manifest()
    hit = [r for r in man if r["sample_id"] == args.sample and r["alpha"] == a]
    if not hit:
        avail = sorted({r["alpha"] for r in man if r["sample_id"] == args.sample})
        print("!! %s @ α=%s が無い(利用可能 α: %s)" % (args.sample, a, ", ".join(avail) or "none"),
              file=sys.stderr)
        return 1
    row = hit[0]
    img_path = HERE / "sample" / row["file"]
    full_prompt = prompt.build(SOUND_INFO)
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
        print("sample=%s  alpha=%s  gt=%s  pred=%s  %.0fms" % (
            args.sample, a, row["gt_label"], pred, dt))
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
