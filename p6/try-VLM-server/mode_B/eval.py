#!/usr/bin/env python3
"""mode_B の一括評測: α ごとに 9 枚を回し、4 クラス成績を出す。

これが §2.4 に載せる「重畳の α で VLM の読みが変わるか」の表を作る。1 本の SSH 接続を
使い回す。結果は results/ に保存。

用法(3090 で server 起動後、wolf venv):
  python eval.py                     # manifest にある全 α
  python eval.py --alphas 0.3,0.6,0.9
"""
import argparse
import csv
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from common import tunnel, protocol, prompt, grading    # noqa: E402
from overlay_info import SOUND_INFO                      # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="mode_B 一括評測")
    ap.add_argument("--alphas", default=None, help="比較する α のカンマ区切り(既定=manifest 全 α)")
    ap.add_argument("--quality", type=int, default=70)
    ap.add_argument("--ip", default=tunnel.DEFAULT_IP)
    ap.add_argument("--local-port", type=int, default=50017)
    ap.add_argument("--remote-port", type=int, default=50007)
    ap.add_argument("--save", default=None)
    args = ap.parse_args()

    man = list(csv.DictReader(open(HERE / "sample" / "manifest.csv")))
    all_alphas = sorted({r["alpha"] for r in man}, key=float)
    alphas = ([("%.2f" % float(a)) for a in args.alphas.split(",")] if args.alphas else all_alphas)

    report = []

    def emit(line=""):
        print(line)
        report.append(line)

    emit("mode_B (image overlay) — 9 samples x alphas: %s" % ", ".join(alphas))
    emit("model=Qwen2.5-VL-32B-AWQ  jpeg q%d  %s" % (args.quality, date.today().isoformat()))

    print("SSH 端口転送 %d -> 3090:%d ..." % (args.local_port, args.remote_port))
    tun = tunnel.start_tunnel(args.ip, args.local_port, args.remote_port)
    try:
        if not tunnel.wait_port(args.local_port):
            print("!! 隧道未就绪。remote server を確認。", file=sys.stderr)
            return 1
        sock = protocol.connect(args.local_port)
        full = prompt.build(SOUND_INFO)                  # α に依らず同一プロンプト

        for a in alphas:
            rows = [r for r in man if r["alpha"] == a]
            emit("\n================ alpha = %s ================" % a)
            tally = grading.new_tally()
            lat = []
            for r in rows:
                img = protocol.encode_jpeg(HERE / "sample" / r["file"], args.quality)
                t0 = time.perf_counter()
                protocol.send_request(sock, full, img)
                out = protocol.recv_response(sock)
                lat.append((time.perf_counter() - t0) * 1000.0)
                if out is None:
                    emit("!! server 断開")
                    sock = None
                    break
                pred = prompt.parse_label(out)
                grading.add(tally, r["gt_label"], pred)
                mark = "OK " if pred == r["gt_label"] else "   "
                emit("%s%-10s gt=%-12s pred=%-12s %5.0fms" % (
                    mark, r["sample_id"], r["gt_label"], pred, lat[-1]))
            if sock is None:
                break
            emit("")
            emit(grading.summary(tally))
            emit("mean latency %.0fms/frame" % (sum(lat) / len(lat) if lat else 0))
        if sock is not None:
            sock.close()
    finally:
        tun.terminate()
        try:
            tun.wait(timeout=5)
        except Exception:
            tun.kill()

    out_path = Path(args.save) if args.save else HERE / "results" / ("eval_modeB_%s.txt" % date.today().isoformat())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(report) + "\n")
    print("\nレポート -> %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
