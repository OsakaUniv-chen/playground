#!/usr/bin/env python3
"""baseline の一括評測: 純 RGB(音図なし)で 9 枚を回し、4 クラス成績を出す。

mode_A / mode_B との対照。視覚のみで話者をどこまで当てられるか(＝音図追加の効果の下限)。
入力は mode_A のプレーン RGB を流用。結果は results/ に保存。

用法(3090 で server 起動後、wolf venv):
  python eval.py
"""
import argparse
import csv
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MODE_A_SAMPLE = ROOT / "mode_A" / "sample"
sys.path.insert(0, str(ROOT))
from common import tunnel, protocol, prompt, grading    # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="baseline 一括評測(音図なし)")
    ap.add_argument("--quality", type=int, default=70)
    ap.add_argument("--ip", default=tunnel.DEFAULT_IP)
    ap.add_argument("--local-port", type=int, default=50017)
    ap.add_argument("--remote-port", type=int, default=50007)
    ap.add_argument("--save", default=None)
    args = ap.parse_args()

    rows = list(csv.DictReader(open(MODE_A_SAMPLE / "manifest.csv")))
    report = []

    def emit(line=""):
        print(line)
        report.append(line)

    emit("baseline (RGB only, no sound info) — %d samples" % len(rows))
    emit("model=Qwen2.5-VL-32B-AWQ  jpeg q%d  %s" % (args.quality, date.today().isoformat()))

    print("SSH 端口転送 %d -> 3090:%d ..." % (args.local_port, args.remote_port))
    tun = tunnel.start_tunnel(args.ip, args.local_port, args.remote_port)
    try:
        if not tunnel.wait_port(args.local_port):
            print("!! 隧道未就绪。remote server を確認。", file=sys.stderr)
            return 1
        sock = protocol.connect(args.local_port)
        full = prompt.build(prompt.BASELINE_INFO)          # 全 sample 同一プロンプト
        tally = grading.new_tally()
        lat = []
        emit("")
        for r in rows:
            img = protocol.encode_jpeg(MODE_A_SAMPLE / r["file"], args.quality)
            t0 = time.perf_counter()
            protocol.send_request(sock, full, img)
            out = protocol.recv_response(sock)
            lat.append((time.perf_counter() - t0) * 1000.0)
            if out is None:
                emit("!! server 断開")
                break
            pred = prompt.parse_label(out)
            grading.add(tally, r["gt_label"], pred)
            mark = "OK " if pred == r["gt_label"] else "   "
            emit("%s%-10s gt=%-12s pred=%-12s %5.0fms" % (
                mark, r["sample_id"], r["gt_label"], pred, lat[-1]))
        sock.close()
        emit("")
        emit(grading.summary(tally))
        emit("mean latency %.0fms/frame" % (sum(lat) / len(lat) if lat else 0))
    finally:
        tun.terminate()
        try:
            tun.wait(timeout=5)
        except Exception:
            tun.kill()

    out_path = Path(args.save) if args.save else HERE / "results" / ("eval_baseline_%s.txt" % date.today().isoformat())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(report) + "\n")
    print("\nレポート -> %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
