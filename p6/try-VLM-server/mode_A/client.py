#!/usr/bin/env python3
"""mode_A クライアント: プレーン RGB + テキスト化した音源情報を送り、話者を当てさせる。

提案書 2.4 の (A) テキスト化。**可変なのは --sample と --format だけ**。
場面説明・遠隔者=下中央の役割・回答フォーマットは共通足場(common/prompt.py)、
音源のテキストは textifier がピーク座標から自動生成(gt 非依存)。

前提: 3090 で server 起動(echo で配線確認 / qwen で本番)。

用法(wolf venv):
  python client.py --sample sample_06 --format coord
  python client.py --sample sample_11 --format profile
"""
import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                                   # try-VLM-server/
sys.path.insert(0, str(ROOT))
from common import tunnel, protocol, prompt           # noqa: E402
from textifier import make_sound_info, FORMATS        # noqa: E402

import csv                                            # noqa: E402


def load_manifest():
    return {r["sample_id"]: r for r in csv.DictReader(open(HERE / "sample" / "manifest.csv"))}


def main():
    ap = argparse.ArgumentParser(description="mode_A client (テキスト化)")
    ap.add_argument("--sample", required=True, help="sample_id 例 sample_06")
    ap.add_argument("--format", default="coord", choices=FORMATS)
    ap.add_argument("--quality", type=int, default=70)
    ap.add_argument("--ip", default=tunnel.DEFAULT_IP)
    ap.add_argument("--local-port", type=int, default=50017)
    ap.add_argument("--remote-port", type=int, default=50007)
    ap.add_argument("--show-prompt", action="store_true", help="送るプロンプト全文を表示")
    args = ap.parse_args()

    man = load_manifest()
    if args.sample not in man:
        print("!! 未知の sample: %s (候補: %s)" % (args.sample, ", ".join(man)), file=sys.stderr)
        return 1
    row = man[args.sample]
    img_path = HERE / "sample" / row["file"]

    full_prompt = prompt.build(make_sound_info(row, args.format))
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
        print("sample=%s  format=%s  gt=%s  pred=%s  %.0fms" % (
            args.sample, args.format, row["gt_label"], pred, dt))
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
