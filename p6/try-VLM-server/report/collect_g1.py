#!/usr/bin/env python3
"""第3部 本試験の収集: G1_game3 全 tick × 7 手法(baseline + mode_A coord/grid/nl +
mode_B α0.3/0.5/0.7)を実測して g1_data.json に貯める。

3.5h 級の長時間ネット処理なので **堅牢化**:
  - 逐次保存(SAVE_EVERY ごと + 終了時)。
  - resume(既存 g1_data.json を読み、済みジョブ (tick,cond,method) は飛ばす)。
  - 再接続(接続断で隧道を張り直し、同ジョブを最大 RETRY 回まで再試行)。
画像は g1data/ の JPEG(q100@756)をそのまま送る(再エンコードなし)。

用法(wolf venv, 3090 で server 稼働・VPN 接続下):
  python collect_g1.py                # 続きから
  python collect_g1.py --restart      # 最初から(既存 g1_data.json 無視)
"""
import argparse
import csv
import json
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mode_A"))
sys.path.insert(0, str(ROOT / "mode_B"))
from common import tunnel, protocol, prompt              # noqa: E402
from textifier import make_sound_info                    # noqa: E402
from overlay_info import SOUND_INFO                       # noqa: E402
from combo import make_combo_info                         # noqa: E402

DATA = HERE / "g1data"
SAVE_EVERY = 100
RETRY = 4


def build_jobs():
    rows = list(csv.DictReader(open(DATA / "manifest.csv")))
    jobs = []
    for r in rows:
        idx, gt = r["tick_idx"], r["gt_label"]
        jobs.append((idx, gt, "baseline", "-", r["rgb"], prompt.build(prompt.BASELINE_INFO)))
        for fmt in ("coord", "grid", "nl"):
            jobs.append((idx, gt, "mode_A", fmt, r["rgb"],
                         prompt.build(make_sound_info(r, fmt))))
        b = prompt.build(SOUND_INFO)
        for a, col in (("0.30", "a30"), ("0.50", "a50"), ("0.70", "a70")):
            jobs.append((idx, gt, "mode_B", a, r[col], b))
        # combo: mode_A coord(座標テキスト) + mode_B α=0.5(重畳画像)
        jobs.append((idx, gt, "combo", "coord+a50", r["a50"],
                     prompt.build(make_combo_info(r))))
    return jobs


def reconnect(args, old_tun):
    if old_tun is not None:
        try:
            old_tun.terminate()
        except Exception:
            pass
    for _ in range(RETRY):
        tun = tunnel.start_tunnel(args.ip, args.local_port, args.remote_port)
        if tunnel.wait_port(args.local_port, timeout=40):
            try:
                return tun, protocol.connect(args.local_port)
            except OSError:
                pass
        try:
            tun.terminate()
        except Exception:
            pass
        time.sleep(3)
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default=tunnel.DEFAULT_IP)
    ap.add_argument("--local-port", type=int, default=50017)
    ap.add_argument("--remote-port", type=int, default=50007)
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--out", default=str(HERE / "g1_data.json"))
    args = ap.parse_args()
    out_path = Path(args.out)

    jobs = build_jobs()
    records, done = [], set()
    if out_path.exists() and not args.restart:
        prev = json.loads(out_path.read_text())
        records = prev.get("records", [])
        done = {"%s:%s:%s" % (r["tick_idx"], r["condition"], r["method"]) for r in records}
        print("resume: %d 済み" % len(done), flush=True)
    todo = [j for j in jobs if "%s:%s:%s" % (j[0], j[2], j[3]) not in done]
    print("総ジョブ %d / 残り %d" % (len(jobs), len(todo)), flush=True)

    def save():
        out_path.write_text(json.dumps(dict(
            meta=dict(model="Qwen2.5-VL-32B-Instruct-AWQ", bag="G1_game3_Tele",
                      resolution=756, jpeg_quality=100, date=date.today().isoformat(),
                      n_records=len(records)),
            records=records), ensure_ascii=False))

    tun, sock = reconnect(args, None)
    if sock is None:
        print("!! 初回接続失敗。server/VPN を確認。", file=sys.stderr)
        return 1
    t_start = time.time()
    try:
        for k, (idx, gt, cond, method, img_file, full) in enumerate(todo, 1):
            img = (DATA / img_file).read_bytes()
            out = None
            for attempt in range(RETRY):
                try:
                    t0 = time.perf_counter()
                    protocol.send_request(sock, full, img)
                    out = protocol.recv_response(sock)
                    dt = (time.perf_counter() - t0) * 1000.0
                except Exception:
                    out = None
                if out is not None:
                    break
                print("  再接続(job %s:%s:%s, 試行%d)" % (idx, cond, method, attempt + 1),
                      flush=True)
                tun, sock = reconnect(args, tun)
                if sock is None:
                    save()
                    print("!! 再接続失敗。%d 件保存して中断。" % len(records), file=sys.stderr)
                    return 1
            pred = prompt.parse_label(out) if out is not None else None
            records.append(dict(tick_idx=idx, gt=gt, condition=cond, method=method,
                                pred=pred, correct=bool(pred == gt),
                                latency_ms=round(dt, 1), output=out))
            if k % SAVE_EVERY == 0:
                save()
                el = time.time() - t_start
                eta = el / k * (len(todo) - k)
                print("  %d/%d  経過%.0fmin ETA%.0fmin  (計%d件)" % (
                    k, len(todo), el / 60, eta / 60, len(records)), flush=True)
        save()
        print("完了: %d 件 -> %s" % (len(records), out_path), flush=True)
    finally:
        save()
        if tun is not None:
            try:
                tun.terminate()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
