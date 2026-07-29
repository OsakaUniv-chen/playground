#!/usr/bin/env python3
"""mode_A の一括評測: 9 枚 x 選んだ形式群を回し、形式ごとに 4 クラス成績を出す。

これが §2.4 に載せる「どのテキスト形式が最も効くか」の表を作る。1 本の SSH 接続を
使い回して全リクエストを流す。結果は results/ に保存。

用法(3090 で server 起動後、wolf venv):
  python eval.py                       # 5 形式すべて
  python eval.py --formats coord,nl    # 一部だけ
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
from textifier import make_sound_info, FORMATS          # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="mode_A 一括評測")
    ap.add_argument("--formats", default=",".join(FORMATS),
                    help="比較する形式のカンマ区切り(既定=全 5 形式)")
    ap.add_argument("--quality", type=int, default=70)
    ap.add_argument("--ip", default=tunnel.DEFAULT_IP)
    ap.add_argument("--local-port", type=int, default=50017)
    ap.add_argument("--remote-port", type=int, default=50007)
    ap.add_argument("--save", default=None, help="レポート保存先(既定=results/eval_modeA_<date>.txt)")
    args = ap.parse_args()

    fmts = [f.strip() for f in args.formats.split(",") if f.strip()]
    bad = [f for f in fmts if f not in FORMATS]
    if bad:
        print("!! 未知の形式: %s (候補 %s)" % (bad, ", ".join(FORMATS)), file=sys.stderr)
        return 1
    rows = list(csv.DictReader(open(HERE / "sample" / "manifest.csv")))

    report = []

    def emit(line=""):
        print(line)
        report.append(line)

    emit("mode_A (text-ified sound) — %d samples x formats: %s" % (len(rows), ", ".join(fmts)))
    emit("model=Qwen2.5-VL-32B-AWQ  jpeg q%d  %s" % (args.quality, date.today().isoformat()))

    print("SSH 端口転送 %d -> 3090:%d ..." % (args.local_port, args.remote_port))
    tun = tunnel.start_tunnel(args.ip, args.local_port, args.remote_port)
    try:
        if not tunnel.wait_port(args.local_port):
            print("!! 隧道未就绪。remote server を確認。", file=sys.stderr)
            return 1
        sock = protocol.connect(args.local_port)

        for fmt in fmts:
            emit("\n================ format = %s ================" % fmt)
            tally = grading.new_tally()
            lat = []
            for r in rows:
                img = protocol.encode_jpeg(HERE / "sample" / r["file"], args.quality)
                full = prompt.build(make_sound_info(r, fmt))
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

    out_path = Path(args.save) if args.save else HERE / "results" / ("eval_modeA_%s.txt" % date.today().isoformat())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(report) + "\n")
    print("\nレポート -> %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
