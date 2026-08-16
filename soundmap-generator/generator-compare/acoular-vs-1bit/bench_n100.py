"""Can acoular / 1-bit hold the 4 Hz tick on an N100? -- timing benchmark.

Replays real ticks from a bag and feeds the IDENTICAL 160-msg audio window to
both generators, inside the emulated N100 (`n100.py`: 4 pinned E-cores, clock
clamped to 3.4 GHz). Reports, per generator:

  - wall ms/map: mean / p50 / p95 / max, and how many ticks blew the 250 ms budget
  - CPU ms/map:  summed over threads. The interesting number on a 6 W passive
                 box is not just "does it fit in 250 ms" but "how much of the
                 machine does it eat while fitting" -- acoular's numba kernels
                 use all four cores, the 1-bit generator is single-threaded, and
                 that ratio is invisible in wall time alone.
  - the shared downstream labeling cost (mask -> exp -> colorize -> extract_target7),
    measured here too because it comes out of the same 250 ms and is NOT small.

    python3 bench_n100.py                     # emulated N100, default bag
    python3 bench_n100.py --host              # same measurement on the full workstation
    python3 bench_n100.py --ticks 400 --bag G2_game3_PSSP

Writes results/bench_n100.json (machine description + every per-tick sample) and
results/bench_n100.md. Both record whether the 3.4 GHz clock lock was actually in
place, so an unlocked run can't be mistaken for a locked one later.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# 1. The N100 emulation caps thread pools, which numpy/numba read at import time.
import n100
n100.activate_from_argv()

# 2. acoular must be imported BEFORE numpy: its configuration.py pins numba to a
#    single thread if it finds numpy already loaded against a threaded OpenBLAS,
#    which costs the acoular generator ~2x here (see README, "the import-order
#    trap"). soundmap_api.py imports numpy at module scope, so importing it
#    first would spring exactly that trap.
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "generator-acoular", "soundmap")))
import acoular  # noqa: F401,E402  (import for its side effects on numba/OpenBLAS)

import argparse  # noqa: E402
import gc  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402

import numpy as np  # noqa: E402

for _p in (os.path.join(_HERE, "..", "..", "generator-acoular"),
           os.path.join(_HERE, "..", "..", "generator-1bit"),
           os.path.join(_HERE, "..")):
    _p = os.path.normpath(_p)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import utils as B  # noqa: E402
from utils import get_speaking_box, label_current_sm, vad_active_at  # noqa: E402
from soundmap_api import SoundMapAPI  # noqa: E402
from onebit_soundmap import OneBitSoundMapAPI  # noqa: E402

DEFAULT_ROOT = "/media/chen/Extreme SSD/PSSPData/WordWolfExp"
BAG_NAME = "G11_game4_DoA"
TICK = 0.25            # 4 Hz -> the 250 ms budget every number here is judged against
AUDIO_WIN = 160
RESULTS = os.path.join(_HERE, "results")


def stats(xs):
    a = np.asarray(xs, dtype=np.float64)
    return {"n": int(a.size), "mean": float(a.mean()), "p50": float(np.percentile(a, 50)),
            "p95": float(np.percentile(a, 95)), "max": float(a.max()), "min": float(a.min())}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rosbag-root", default=DEFAULT_ROOT)
    ap.add_argument("--bag", default=BAG_NAME)
    ap.add_argument("--start", type=float, default=40.0)
    ap.add_argument("--ticks", type=int, default=200, help="number of 4 Hz ticks to time")
    ap.add_argument("--warmup", type=int, default=3,
                    help="untimed calls per generator (numba JIT, Delaunay/LUT caches)")
    ap.add_argument("--gc", choices=("on", "off"), default="on",
                    help="cyclic GC during the timed loop (default on = what a deployed "
                         "node does). 'off' isolates acoular's periodic gen-2 pause, which "
                         "is what puts it over the tick budget -- see README")
    ap.add_argument("--out", default=os.path.join(RESULTS, "bench_n100"))
    n100.add_arguments(ap)
    args = ap.parse_args()

    bag = os.path.join(args.rosbag_root, args.bag)
    if not os.path.isdir(bag):
        raise SystemExit(f"bag not found: {bag}")
    con = B.open_bag(bag)
    audio = B.read_series(con, B.AUDIO_TOPIC)
    head = [(t, v) for t, v in B.read_series(con, B.HEAD_TOPIC) if v and len(v) >= 8]
    vad = B.read_series(con, B.VAD_TOPIC)
    con.close()
    a_ts = np.array([t for t, _ in audio]); a_d = [d for _, d in audio]
    h_ts = np.array([t for t, _ in head])
    vts = [t / 1e9 for t, _ in vad]; vval = [bool(v) for _, v in vad]

    t0 = int(a_ts[0] + args.start * 1e9)
    ticks = np.arange(t0, int(a_ts[-1]), int(TICK * 1e9))[:args.ticks]
    if len(ticks) == 0:
        raise SystemExit(f"no ticks after --start {args.start}s in {args.bag}")
    print(f"{args.bag}: timing {len(ticks)} ticks from t={args.start:.0f}s "
          f"({TICK * 1e3:.0f} ms budget each)")

    ac_api = SoundMapAPI()
    ob_api = OneBitSoundMapAPI(device="cpu")
    speaking_box = get_speaking_box()

    ja0 = int(np.searchsorted(a_ts, ticks[0], side="right"))
    warm = a_d[max(0, ja0 - AUDIO_WIN):ja0]
    print(f"warming up ({args.warmup}x each)...", flush=True)
    for _ in range(args.warmup):
        ac_api.generate(warm)
        ob_api.generate(warm)

    # acoular rebuilds its whole traits object graph (TimeSamples, PowerSpectra,
    # SteeringVector, BeamformerBase, 3x RectGrid, MergeGrid) on every generate(),
    # so CPython's gen-2 collector fires every ~27 ticks and roughly doubles that
    # tick's latency. --gc off measures the generator without that pause; the
    # difference is the whole of acoular's deadline-miss rate here.
    if args.gc == "off":
        gc.collect()
        gc.freeze()
        gc.disable()

    rows = []
    sampler = n100.FreqSampler(n100.STATE["cores"] or (0,)).start()
    wall0, cpu0 = time.perf_counter(), time.process_time()
    for i, t in enumerate(ticks):
        ja = int(np.searchsorted(a_ts, t, side="right"))
        if ja < AUDIO_WIN:
            continue
        window = a_d[ja - AUDIO_WIN:ja]
        jh = int(np.searchsorted(h_ts, t, side="right")) - 1
        hb = ([list(head[jh][1][0:4]), list(head[jh][1][4:8])] if jh >= 0
              else [[-99] * 4, [-99] * 4])
        va = vad_active_at(vts, vval, t / 1e9)

        w, c = time.perf_counter(), time.process_time()
        sm_ac = ac_api.generate(window)
        ac_ms, ac_cpu = 1e3 * (time.perf_counter() - w), 1e3 * (time.process_time() - c)

        w, c = time.perf_counter(), time.process_time()
        sm_ob = ob_api.generate(window)
        ob_ms, ob_cpu = 1e3 * (time.perf_counter() - w), 1e3 * (time.process_time() - c)

        w = time.perf_counter()
        lab_ac, _, _ = label_current_sm(sm_ac, hb, va, speaking_box=speaking_box)
        lab_ac_ms = 1e3 * (time.perf_counter() - w)
        w = time.perf_counter()
        lab_ob, _, _ = label_current_sm(sm_ob, hb, va, speaking_box=speaking_box)
        lab_ob_ms = 1e3 * (time.perf_counter() - w)

        rows.append(dict(t_s=(t - t0) / 1e9, vad=va,
                         acoular_ms=ac_ms, acoular_cpu_ms=ac_cpu,
                         onebit_ms=ob_ms, onebit_cpu_ms=ob_cpu,
                         label_ms=0.5 * (lab_ac_ms + lab_ob_ms),
                         lab_acoular=lab_ac, lab_onebit=lab_ob,
                         agree=lab_ac == lab_ob))
        if i % 25 == 0:
            print(f"  tick {i:4d}/{len(ticks)}  acoular {ac_ms:7.1f} ms   "
                  f"1-bit {ob_ms:6.1f} ms   label {0.5*(lab_ac_ms+lab_ob_ms):5.1f} ms",
                  flush=True)
    wall_total, cpu_total = time.perf_counter() - wall0, time.process_time() - cpu0
    clock = sampler.stop()
    if args.gc == "off":
        gc.unfreeze()
        gc.enable()

    budget = TICK * 1e3
    label_mean = float(np.mean([r["label_ms"] for r in rows]))
    summary = {}
    for name, key, cpu_key in (("acoular", "acoular_ms", "acoular_cpu_ms"),
                               ("1bit", "onebit_ms", "onebit_cpu_ms")):
        wall = stats([r[key] for r in rows])
        cpu = stats([r[cpu_key] for r in rows])
        total = [r[key] + r["label_ms"] for r in rows]
        summary[name] = {
            "wall_ms": wall, "cpu_ms": cpu,
            "cores_busy": cpu["mean"] / wall["mean"] if wall["mean"] else None,
            "tick_total_ms": stats(total),
            "over_budget_ticks": int(sum(x > budget for x in total)),
            "budget_used_pct": 100.0 * float(np.mean(total)) / budget,
            "max_rate_hz": 1000.0 / float(np.mean(total)),
        }

    n_agree = sum(r["agree"] for r in rows)
    out = {
        "machine": n100.describe(),
        "clock_under_load_mhz": clock,
        "bag": args.bag, "start_s": args.start, "ticks": len(rows),
        "gc": args.gc,
        "tick_budget_ms": budget,
        "label_pipeline_ms": label_mean,
        "generators": summary,
        "label_agreement": {"agree": n_agree, "n": len(rows),
                            "pct": 100.0 * n_agree / max(len(rows), 1)},
        "harness_wall_s": wall_total, "harness_cpu_s": cpu_total,
        "rows": rows,
    }

    os.makedirs(RESULTS, exist_ok=True)
    with open(args.out + ".json", "w") as fh:
        json.dump(out, fh, indent=1)
    with open(args.out + ".md", "w") as fh:
        fh.write(render_md(out))
    print("\n" + render_md(out))
    print(f"wrote {args.out}.json / {args.out}.md")


def render_md(out) -> str:
    m = out["machine"]
    clk = out["clock_under_load_mhz"]
    budget = out["tick_budget_ms"]
    lab = out["label_pipeline_ms"]
    L = []
    L.append(f"# bench_n100 -- {m['label']}\n")
    L.append(f"- host CPU: `{m['host_cpu']}`")
    if m["mode"] == "n100-sim":
        L.append(f"- emulated cores: `{m['cores']}`, thread cap {m['threads']}, "
                 f"clock lock **{'ON' if m['freq_locked'] else 'OFF'}** "
                 f"(target {m['target_khz'] / 1e6:.1f} GHz)")
    # Only meaningful when the process is pinned: in host mode the sampler watches
    # one arbitrary cpu while the work floats across all 22 threads.
    if m["mode"] == "n100-sim" and clk and clk.get("mean_mhz"):
        L.append(f"- measured clock under load: {clk['mean_mhz']:.0f} MHz mean "
                 f"({clk['min_mhz']:.0f}-{clk['max_mhz']:.0f}, n={clk['n']})")
    L.append(f"- bag `{out['bag']}`, {out['ticks']} ticks from t={out['start_s']:.0f}s, "
             f"budget {budget:.0f} ms/tick (4 Hz)")
    L.append(f"- cyclic GC during the timed loop: **{out.get('gc', 'on')}**")
    L.append(f"- shared labeling pipeline: {lab:.1f} ms/tick "
             f"({100 * lab / budget:.0f}% of the budget, same for both generators)\n")
    L.append("| generator | wall mean | p50 | p95 | max | CPU ms/map | cores busy | "
             "+label = tick | budget used | over budget | max rate |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name in ("acoular", "1bit"):
        s = out["generators"][name]
        w, c = s["wall_ms"], s["cpu_ms"]
        L.append(f"| {name} | {w['mean']:.1f} | {w['p50']:.1f} | {w['p95']:.1f} | "
                 f"{w['max']:.1f} | {c['mean']:.1f} | {s['cores_busy']:.2f} | "
                 f"{s['tick_total_ms']['mean']:.1f} ms | {s['budget_used_pct']:.0f}% | "
                 f"{s['over_budget_ticks']}/{out['ticks']} | {s['max_rate_hz']:.1f} Hz |")
    ag = out["label_agreement"]
    L.append(f"\n4-label agreement over these ticks: {ag['agree']}/{ag['n']} "
             f"({ag['pct']:.1f}%) -- see `analyze_agreement.py` for the breakdown.")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
