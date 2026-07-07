"""WP1 (bag audit) + WP2 (reproduction gate).

    OPENBLAS_NUM_THREADS=1 python validate.py audit        # WP1, all robot bags
    OPENBLAS_NUM_THREADS=1 python validate.py reproduce    # WP2, 1 DoA + 1 PSSP + 1 Tele

Writes/updates ../docs/validation-report.md. Time axis = bag record timestamp (ns).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bag_io as B
from labeling import HeadBoxProcessor, label_current_sm, vad_active_at

ROSBAG_ROOT = Path(B.resolve_bag_root())    # /media/chen/... (Linux) else /Volumes/... (Mac)
MODES = ("Tele", "PSSP", "DoA", "Random")
REPORT = Path(__file__).resolve().parent.parent / "docs" / "validation-report.md"
PREV_EXCLUDED = {"G6_game3_Tele", "G12_game4_PSSP"}


def discover_bags():
    bags = []
    for d in sorted(ROSBAG_ROOT.iterdir()):
        m = B.DIR_RE.match(d.name)
        if d.is_dir() and m and m["mode"] in MODES:
            bags.append(d)
    return bags


def _series_ts(con, topic):
    tid = B.topic_id(con, topic)
    if tid is None:
        return []
    return [r[0] for r in con.execute(
        "SELECT timestamp FROM messages WHERE topic_id=? ORDER BY timestamp", (tid,))]


def _interval_stats(ts_ns):
    if len(ts_ns) < 2:
        return None
    ts = np.asarray(ts_ns, dtype=np.float64)
    dur = (ts[-1] - ts[0]) / 1e9
    d = np.diff(ts) / 1e9
    return {
        "n": len(ts), "dur": dur, "rate": len(ts) / dur if dur > 0 else 0.0,
        "p50": float(np.percentile(d, 50)), "p95": float(np.percentile(d, 95)),
        "max": float(d.max()),
    }


def _pearson(a, b):
    a = a.astype(np.float64).ravel(); b = b.astype(np.float64).ravel()
    if a.std() < 1e-9 or b.std() < 1e-9:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


# ==========================================================================
# WP1 — audit
# ==========================================================================
def audit_bag(bag_dir):
    con = B.open_bag(bag_dir)
    present = {r[1] for r in con.execute("SELECT id, name FROM topics")}
    out = {"name": bag_dir.name}

    # rates for the topics we care about
    rates = {}
    for topic in (B.AUDIO_TOPIC, B.CAMERA_TOPIC, B.ROOM2_CAMERA_TOPIC, B.VAD_TOPIC,
                  B.MOTORS_TOPIC, B.SM_TOPIC, B.HEAD_TOPIC, B.TELE_ORIENT_TOPIC):
        st = _interval_stats(_series_ts(con, topic)) if topic in present else None
        rates[topic] = st
    out["rates"] = rates

    # audio gap detection
    out["audio"] = rates[B.AUDIO_TOPIC]
    # tick period proxy = /sm_without_transform interval (DoA/PSSP only)
    out["sm"] = rates[B.SM_TOPIC]

    # room2 vs room1 clock offset: header.stamp - record_ts (seconds)
    def clock_offset(topic):
        tid = B.topic_id(con, topic)
        if tid is None:
            return None
        offs = []
        for ts, data in con.execute(
            "SELECT timestamp, data FROM messages WHERE topic_id=? ORDER BY timestamp LIMIT 2000", (tid,)):
            hs = B.header_stamp_ns(data)
            if hs is not None:
                offs.append((hs - ts) / 1e9)
        if not offs:
            return None
        offs = np.asarray(offs)
        return {"median": float(np.median(offs)), "p05": float(np.percentile(offs, 5)),
                "p95": float(np.percentile(offs, 95))}
    out["clk_room2_vad"] = clock_offset(B.VAD_TOPIC)
    out["clk_room2_cam"] = clock_offset(B.ROOM2_CAMERA_TOPIC)
    out["clk_room1_cam"] = clock_offset(B.CAMERA_TOPIC)
    con.close()
    return out


def cmd_audit(args):
    bags = discover_bags()
    print(f"auditing {len(bags)} bags...")
    rows = []
    for i, bag in enumerate(bags, 1):
        try:
            rows.append(audit_bag(bag))
        except Exception as e:
            rows.append({"name": bag.name, "error": str(e)})
        print(f"  [{i}/{len(bags)}] {bag.name}", flush=True)

    lines = ["# analysis2 — Validation Report", "",
             f"Generated {time.strftime('%Y-%m-%d %H:%M')} from `validate.py`. "
             f"Time axis = bag record timestamp.", "",
             "## WP1 — bag audit", "",
             f"{len(bags)} robot-condition bags. Rate = msgs/duration (Hz). "
             "Audio p95/max = inter-message gap (s) → gap detection. "
             "SM interval = /sm_without_transform period (tick proxy, DoA/PSSP).", "",
             "| bag | dur(s) | audio Hz | audio p95/max gap | cam Hz | vad Hz | motors Hz | sm Hz | sm p50/p95(s) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['name']} | ERROR: {r['error']} | | | | | | | |")
            continue
        a = r["audio"]; sm = r["sm"]; ra = r["rates"]
        cam = ra[B.CAMERA_TOPIC]; vad = ra[B.VAD_TOPIC]; mot = ra[B.MOTORS_TOPIC]
        def f(x, k, nd=1):
            return f"{x[k]:.{nd}f}" if x else "–"
        sm_iv = f"{sm['p50']:.3f}/{sm['p95']:.3f}" if sm else "–"
        lines.append(
            f"| {r['name']} | {a['dur']:.0f} | {f(a,'rate')} | "
            f"{f(a,'p95',3)}/{f(a,'max',2)} | {f(cam,'rate')} | {f(vad,'rate')} | "
            f"{f(mot,'rate')} | {f(sm,'rate')} | {sm_iv} |")

    # clock offsets
    lines += ["", "### Cross-machine clock offset (header.stamp − record_ts, seconds)",
              "room2 topics originate on the teleoperate PC. Large/again-varying offset ⇒ "
              "unsynced clocks → confirms using record_ts (not header.stamp).", "",
              "| bag | room2 vad median[p05,p95] | room2 cam median | room1 cam median |",
              "|---|---|---|---|"]
    for r in rows:
        if "error" in r:
            continue
        def c(x):
            return f"{x['median']:+.3f}[{x['p05']:+.2f},{x['p95']:+.2f}]" if x else "–"
        def cm(x):
            return f"{x['median']:+.3f}" if x else "–"
        lines.append(f"| {r['name']} | {c(r['clk_room2_vad'])} | {cm(r['clk_room2_cam'])} | {cm(r['clk_room1_cam'])} |")

    # excluded-bag re-check
    lines += ["", "### Previously-excluded bags (now usable, head box re-detected)"]
    for name in sorted(PREV_EXCLUDED):
        r = next((x for x in rows if x["name"] == name), None)
        if r and "error" not in r:
            lines.append(f"- `{name}`: present, audio {r['audio']['rate']:.0f} Hz, "
                         f"dur {r['audio']['dur']:.0f}s → usable.")
        else:
            lines.append(f"- `{name}`: {'ERROR' if r else 'not found'}.")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")
    print(f"wrote {REPORT}")


# ==========================================================================
# WP2 — reproduction gate
# ==========================================================================
def _headbox_timeline(con):
    hb_proc = HeadBoxProcessor()
    h_ts, h_box = [], []
    for ts, vals in B.read_series(con, B.HEAD_TOPIC):
        if vals is None or len(vals) < 8:
            continue
        proc, _ = hb_proc.process([vals[0:4], vals[4:8]])
        h_ts.append(ts)
        h_box.append([list(proc[0]), list(proc[1])])
    return h_ts, h_box


def reproduce_sm(bag_dir, n_sample=40, offsets=(0.1, 0.2, 0.3)):
    from soundmap_api import SoundMapAPI
    con = B.open_bag(bag_dir)
    audio = B.read_series(con, B.AUDIO_TOPIC)
    a_ts = np.array([t for t, _ in audio]); a_d = [d for _, d in audio]
    sm_msgs = [(t, d) for t, d in B.read_series(con, B.SM_TOPIC) if d is not None]
    if not sm_msgs:
        con.close(); return {"error": "no /sm_without_transform"}
    vad = B.read_series(con, B.VAD_TOPIC)
    vts = [t / 1e9 for t, _ in vad]; vval = [bool(v) for _, v in vad]
    h_ts, h_box = _headbox_timeline(con)
    api = SoundMapAPI()

    # sample sm messages, skipping first 10s (buffer fill)
    t0 = sm_msgs[0][0] + int(10e9)
    cand = [(t, d) for t, d in sm_msgs if t >= t0]
    idx = np.linspace(0, len(cand) - 1, min(n_sample, len(cand))).astype(int)
    sample = [cand[i] for i in idx]

    def win_end(t):  # latest 160 audio with ts <= t
        j = int(np.searchsorted(a_ts, t, side="right"))
        return a_d[j - 160:j] if j >= 160 else None

    def hb_at(t):
        import bisect
        k = bisect.bisect_right(h_ts, t) - 1
        return h_box[k] if k >= 0 else [[-99] * 4, [-99] * 4]

    # find best generation-delay offset by mean Pearson r
    off_r = {}
    for off in offsets:
        rs = []
        for T_sm, d in sample:
            w = win_end(T_sm - int(off * 1e9))
            if w is None:
                continue
            my = api.generate(w)
            rec = d[:, :, 0].astype(np.float64)
            rs.append(_pearson(my, rec))
        off_r[off] = float(np.nanmean(rs)) if rs else float("nan")
    best_off = max(off_r, key=lambda k: (off_r[k] if off_r[k] == off_r[k] else -9))

    # reproduction quality + label agreement at best offset
    rs, dpix, agree, n = [], [], 0, 0
    for T_sm, d in sample:
        w = win_end(T_sm - int(best_off * 1e9))
        if w is None:
            continue
        my = api.generate(w)
        rec = d[:, :, 0].astype(np.float64)
        rs.append(_pearson(my, rec))
        dpix.append(float(np.hypot(*(np.array(np.unravel_index(my.argmax(), my.shape)) -
                                     np.array(np.unravel_index(rec.argmax(), rec.shape))))))
        hb = hb_at(T_sm); va = vad_active_at(vts, vval, T_sm / 1e9)
        lab_my, _, _ = label_current_sm(my, hb, va)
        lab_rec, _, _ = label_current_sm(rec, hb, va)
        agree += (lab_my == lab_rec); n += 1
    con.close()
    return {"n": n, "best_offset_s": best_off, "offset_meanR": off_r,
            "meanR": float(np.nanmean(rs)), "medR": float(np.nanmedian(rs)),
            "med_peak_dist_px": float(np.median(dpix)),
            "label_agree": agree / n if n else float("nan")}


def reproduce_tele(bag_dir, frame_stride=3):
    import bisect
    from head_orientation import HeadOrientationAPI
    con = B.open_bag(bag_dir)
    orient = [(t, v) for t, v in B.read_series(con, B.TELE_ORIENT_TOPIC) if v is not None]
    if not orient:
        con.close(); return {"error": "no /tele/head_orientation"}
    # process room2 frames chronologically (subsampled) -> my_yaw per frame ts
    ho = HeadOrientationAPI()
    f_ts, my_yaw = [], []
    frames = B.read_series(con, B.ROOM2_CAMERA_TOPIC)
    for i in range(0, len(frames), frame_stride):
        ts, img = frames[i]
        if img is None:
            continue
        res = ho.detect(img)
        f_ts.append(ts)
        my_yaw.append(None if res is None else res[1])
    con.close()
    # match each recorded orientation to nearest processed frame ts <= orient ts
    maes, n_side, agree = [], 0, 0
    t0 = orient[0][0] + int(10e9)
    for T, v in orient:
        if T < t0:
            continue
        rec_yaw = v[1]
        k = bisect.bisect_right(f_ts, T) - 1
        if k < 0 or my_yaw[k] is None:
            continue
        m = my_yaw[k]
        maes.append(abs(m - rec_yaw))
        my_side = "left" if m > 0 else "right"
        rec_side = "left" if rec_yaw > 0 else "right"
        agree += (my_side == rec_side); n_side += 1
    return {"n": n_side, "yaw_MAE_deg": float(np.mean(maes)) if maes else float("nan"),
            "yaw_med_abs_err": float(np.median(maes)) if maes else float("nan"),
            "side_agree": agree / n_side if n_side else float("nan")}


def _iou(a, b):
    if a is None or b is None or a[0] == -99 or b[0] == -99:
        return None
    ax1, ay1, aw, ah = a; bx1, by1, bw, bh = b
    ax2, ay2, bx2, by2 = ax1 + aw, ay1 + ah, bx1 + bw, by1 + bh
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def reproduce_headbox(bag_dir, n_sample=60):
    import bisect
    from head_box import HeadDetector
    con = B.open_bag(bag_dir)
    head = [(t, v) for t, v in B.read_series(con, B.HEAD_TOPIC) if v and len(v) >= 8]
    cam_ts = _series_ts(con, B.CAMERA_TOPIC)
    if not head or not cam_ts:
        con.close(); return {"error": "missing head/camera"}
    tid = B.topic_id(con, B.CAMERA_TOPIC)
    t0 = head[0][0] + int(10e9)
    hh = [(t, v) for t, v in head if t >= t0]
    idx = np.linspace(0, len(hh) - 1, min(n_sample, len(hh))).astype(int)

    det = HeadDetector()
    ious, valid_match, n = [], 0, 0
    for i in idx:
        T, vals = hh[i]
        rec = [list(vals[0:4]), list(vals[4:8])]
        k = bisect.bisect_right(cam_ts, T) - 1
        if k < 0:
            continue
        row = con.execute("SELECT data FROM messages WHERE topic_id=? AND timestamp=? LIMIT 1",
                          (tid, cam_ts[k])).fetchone()
        if not row:
            continue
        frame = B.decode_compressed_image(row[0])
        if frame is None:
            continue
        my = det.detect_heads(frame)  # fresh detector (no cross-frame persistence)
        n += 1
        for side in (0, 1):
            rec_valid = rec[side][0] != -99
            my_valid = my[side][0] != -99
            valid_match += (rec_valid == my_valid)
            io = _iou(my[side], rec[side])
            if io is not None:
                ious.append(io)
    con.close()
    return {"n": n, "median_IoU": float(np.median(ious)) if ious else float("nan"),
            "n_covalid": len(ious), "validity_match": valid_match / (2 * n) if n else float("nan")}


def _pick(mode):
    for d in discover_bags():
        if B.DIR_RE.match(d.name)["mode"] == mode:
            return d
    return None


def cmd_reproduce(args):
    doa = Path(args.doa) if args.doa else _pick("DoA")
    pssp = Path(args.pssp) if args.pssp else _pick("PSSP")
    tele = Path(args.tele) if args.tele else _pick("Tele")

    out = ["", "## WP2 — reproduction gate", "",
           f"Generated {time.strftime('%Y-%m-%d %H:%M')}. First 10 s of each bag discarded.", ""]

    print(f"[SM] DoA={doa.name}"); t = time.time()
    r_doa = reproduce_sm(doa); print(f"  {r_doa} ({time.time()-t:.0f}s)")
    print(f"[SM] PSSP={pssp.name}"); t = time.time()
    r_pssp = reproduce_sm(pssp); print(f"  {r_pssp} ({time.time()-t:.0f}s)")
    print(f"[Tele] {tele.name}"); t = time.time()
    r_tel = reproduce_tele(tele); print(f"  {r_tel} ({time.time()-t:.0f}s)")
    print(f"[HeadBox] {doa.name}"); t = time.time()
    r_hb = reproduce_headbox(doa); print(f"  {r_hb} ({time.time()-t:.0f}s)")

    out += ["### 1. Sound-map reproduction (regenerated vs recorded /sm_without_transform)",
            "Gate: 4-label agreement ≥ 95%. best_offset = generation delay that maximizes correlation.", "",
            "| bag | n | best offset(s) | mean Pearson r | med peak dist(px) | **label agree** |",
            "|---|---|---|---|---|---|"]
    for name, r in ((doa.name, r_doa), (pssp.name, r_pssp)):
        if "error" in r:
            out.append(f"| {name} | ERROR: {r['error']} | | | | |"); continue
        out.append(f"| {name} | {r['n']} | {r['best_offset_s']} | {r['meanR']:.3f} | "
                   f"{r['med_peak_dist_px']:.1f} | **{r['label_agree']*100:.1f}%** |")
    out += ["", f"offset scan (mean r) DoA: "
            + ", ".join(f"{k}s={v:.3f}" for k, v in r_doa.get('offset_meanR', {}).items())]

    out += ["", "### 2. Tele re-derivation (vs recorded /tele/head_orientation)",
            "Gate: side agreement ≥ 95%.", "",
            "| bag | n | yaw MAE(deg) | yaw med|err| | **side agree** |", "|---|---|---|---|---|"]
    if "error" in r_tel:
        out.append(f"| {tele.name} | ERROR: {r_tel['error']} | | | |")
    else:
        out.append(f"| {tele.name} | {r_tel['n']} | {r_tel['yaw_MAE_deg']:.2f} | "
                   f"{r_tel['yaw_med_abs_err']:.2f} | **{r_tel['side_agree']*100:.1f}%** |")

    out += ["", "### 3. Head-box re-detection (vs recorded /head/head_box)",
            "Fresh detector per sampled frame (no cross-frame persistence); IoU on co-valid boxes.", "",
            "| bag | n | median IoU | co-valid | validity match |", "|---|---|---|---|---|"]
    if "error" in r_hb:
        out.append(f"| {doa.name} | ERROR: {r_hb['error']} | | | |")
    else:
        out.append(f"| {doa.name} | {r_hb['n']} | {r_hb['median_IoU']:.3f} | "
                   f"{r_hb['n_covalid']} | {r_hb['validity_match']*100:.1f}% |")

    with open(REPORT, "a") as f:
        f.write("\n".join(out) + "\n")
    print(f"appended WP2 to {REPORT}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("audit")
    rp = sub.add_parser("reproduce")
    rp.add_argument("--doa"); rp.add_argument("--pssp"); rp.add_argument("--tele")
    args = ap.parse_args()
    {"audit": cmd_audit, "reproduce": cmd_reproduce}[args.cmd](args)


if __name__ == "__main__":
    main()
