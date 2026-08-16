# acoular-vs-1bit — the live generator vs the 1-bit generator, on an emulated N100

The other two comparisons in this folder ask *"do these generators decide the
same thing?"*. This one asks that too, but its real question is **"on the cheap
passively-cooled box we would actually bolt to the robot, does either of them
hold the 4 Hz tick?"** — so every number here is measured on an *emulated Intel
N100* rather than on the 22-thread workstation.

- **acoular** — [`../../generator-acoular/soundmap_api.py`](../../generator-acoular/soundmap_api.py)
  (`SoundMapAPI`, `BeamformerBase.synthetic(f=2000, num=3)`). This is what the
  robot runs today, so it is the baseline the field box has to beat.
- **1-bit** — [`../../generator-1bit/onebit_soundmap.py`](../../generator-1bit/onebit_soundmap.py)
  (`OneBitSoundMapAPI`, bit-shift + XOR + popcount, CPU-only). Its design and
  precision validation live in that folder's README.
- shared bag I/O and 4-label pipeline: [`../utils.py`](../utils.py).

The scripts prepend `../../generator-acoular`, `../../generator-1bit` and `..`
to `sys.path`, so run them from this folder, inside the `wolf` virtualenv.

## Headline

On the emulated N100 (4 E-cores locked at 3.4 GHz), over 300 ticks of
`G11_game4_DoA`:

| generator | ms/map (mean) | p50 | p95 | max | CPU ms/map | cores busy | of the 250 ms tick | ticks over budget |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| acoular | **161.8** | 156.1 | 163.8 | 319.0 | 472.7 | 2.92 | 69 % | 11/300 |
| 1-bit | **25.8** | 25.8 | 26.5 | 26.8 | 26.1 | 1.01 | 15 % | 0/300 |

**6.3× faster, and 18× cheaper in CPU time.** The wall-clock ratio undersells
it: acoular's numba kernels spread over ~2.9 of the 4 cores, the 1-bit generator
is single-threaded, so on a 6 W passive box acoular leaves essentially nothing
for the camera decode, MediaPipe head detection, policy and motor control that
share the same tick — while the 1-bit generator leaves three cores idle. The
shared labeling pipeline (mask → `exp(x−max)` → colorize → `extract_target7`)
costs another **10.7 ms/tick** on this machine, the same for both.

Run-to-run spread on these means is about ±2.5 % (two 300-tick runs of the same
configuration), so treat the third digit as noise; the shape of the result is
not close to that margin.

Over 3060 ticks of four bags the two agree on the 4-label decision **90.0 %** of
the time — see [label agreement](#label-agreement).

`compare-generator-n100.mp4` is the 40 s side-by-side on `G11_game4_DoA` (same
bag and window as the other two comparisons), with each column's share of the
250 ms tick burned in as a budget bar.

## Scripts

| file | purpose |
|---|---|
| `n100.py` | the emulation itself: core pinning, thread caps, clock-lock verification, in-run frequency sampling. Imported first by every script here |
| `n100_lock.sh` | `lock` / `unlock` / `status` for the 3.4 GHz clamp (the one part that needs root) |
| `bench_n100.py` | the timing benchmark → `results/bench_n100{,_nogc}.json/.md`, `results/bench_host.*` |
| `analyze_agreement.py` | 4-label agreement over whole bags → `results/agreement.json/.md` |
| `compare_video.py` | the side-by-side video, rendered inside the emulation |

```bash
sudo bash n100_lock.sh lock       # once; sudo bash n100_lock.sh unlock afterwards
python3 bench_n100.py --ticks 300
python3 bench_n100.py --ticks 300 --gc off --out results/bench_n100_nogc
python3 compare_video.py
python3 analyze_agreement.py --host --bags G11_game4_DoA,G2_game3_PSSP
```

Every script takes `--host` to skip the emulation and run on the whole
workstation instead. `bench_n100.json` records `freq_locked`, the measured clock
under load, and the emulated core list, so a run made without the clamp can
never be mistaken later for one made with it.

## The emulated N100

An N100's compute die is four Gracemont cores with no SMT behind one shared
2 MB L2, at 3.4 GHz. This workstation's `cpu12-15` are four **Crestmont** E-cores
with no SMT behind one shared 2 MB L2 — Crestmont is Gracemont's direct
successor, about 5 % apart in IPC — so pinning to that cluster and clamping the
clock reproduces the target machine's shape rather than scaling a number on
paper.

| | N100 | emulation here |
|---|---|---|
| cores | 4× Gracemont, no SMT | 4× Crestmont E-core (`cpu12-15`), no SMT |
| L2 | 2 MB, shared by all 4 | 2 MB, shared by all 4 |
| clock | 3.4 GHz max turbo | `scaling_min` = `scaling_max` = 3.4 GHz |
| ISA | AVX2, no AVX-512 | E-cores are AVX2-only too |
| L3 | 6 MB | 24 MB, shared with the rest of the SoC |
| RAM | 1× DDR4-3200 / DDR5-4800 | LPDDR5x, wider |
| power | 6 W package, passive | no package limit on 4 otherwise-idle cores |

The clamp sets **both** ends of the range, not just `scaling_max_freq`: clamping
only the ceiling leaves the governor free to drop *below* 3.4 GHz under load,
which would make the emulated N100 look slower than the real part. Measured
clock during the benchmark above: 3379 MHz mean (3183–3433), sampled from
`scaling_cur_freq` throughout the run and recorded in the result JSON.

### How much to trust these numbers

The first four rows are close to exact; the last three are all optimistic, so
**these are lower bounds on real-N100 time, not unbiased estimates** — and the
bias is not equal between the two generators:

- acoular interpolates its ~800 grid values onto a full **1080×1080**
  `scipy.griddata` target before resizing to 64×64: the two int64 coordinate
  meshes are 9.3 MB each and the output another 9.3 MB, so ~28 MB streams
  through the cache hierarchy per map. That already overflows this host's 24 MB
  L3 and is ~5× an N100's 6 MB, on a part with narrower memory besides — expect
  the real N100 to be worse than 162 ms by more than the clock ratio.
- the 1-bit generator's hot loop is bit-packed: ~41 KB of packed sign bits and a
  ~197 KB precomputed interpolation table, comfortably inside the 2 MB L2 the
  emulation reproduces exactly. It is essentially insensitive to the L3 and
  memory rows, so 25.8 ms should transfer nearly unchanged.

In other words the gap on a real N100 is wider than 6.3×, not narrower. The
power row cuts the same way: acoular running ~2.9 cores flat out is exactly the
workload a 6 W passive box throttles, and a throttled measurement is not
something this emulation can produce.

## acoular's deadline misses are Python's garbage collector

The benchmark's `max` column above (319 ms, and 11 of 300 ticks over the 250 ms
budget) looked like an ordinary heavy tail until the over-budget ticks turned
out to be at indices 20, 45, 72, 99, 125, 150, 177, 204, 230, 255, 282 —
periodic, every ~27 ticks, all of them ~2× the median and all of them at
**2.0 cores busy instead of 2.9**. That is a stop-the-world pause, not
beamforming.

Cause: acoular rebuilds its entire traits object graph on every call —
`TimeSamples`, `PowerSpectra`, `SteeringVector`, `BeamformerBase`, three
`RectGrid`s and a `MergeGrid`, in `sound_map.py`'s `generate()` — so CPython's
gen-2 collector fires on a fixed allocation cadence and walks all of it.
`bench_n100.py --gc off` (`gc.collect()` + `gc.freeze()` + `gc.disable()` around
the timed loop) confirms it:

| | mean | p50 | p95 | max | over budget |
|---|---:|---:|---:|---:|---:|
| acoular, GC on (default) | 161.8 | 156.1 | 163.8 | **319.0** | **11/300** |
| acoular, GC off | 162.2 | 161.8 | 168.4 | **177.3** | **0/300** |
| 1-bit, GC on | 25.8 | 25.8 | 26.5 | 26.8 | 0/300 |
| 1-bit, GC off | 25.5 | 25.5 | 25.8 | 26.3 | 0/300 |

Read that table for the tail, not the mean: the two acoular rows come from
separate runs, and their means and p50s differ by less than the ±2.5 %
run-to-run spread — GC costs nothing measurable on a typical tick. What it does
is add ~160 ms to one tick in 27, which is exactly the 11 deadline misses (11 ×
160 ms / 300 ticks ≈ 5.9 ms, the whole of the gap between GC-on's mean and its
own p50).

So acoular's deadline misses are **fixable** (freeze the GC, or hoist the object
graph out of the per-call path) and should not be held against the algorithm.
What survives the fix is the part that matters: acoular still eats ~69 % of the
tick on 2.9 cores. The 1-bit generator is unaffected either way, because it
allocates almost nothing per call — the LUT, the packed-bit buffers and the
interpolation weights are all built once in `__init__`.

## The import-order trap

acoular's `configuration.py` checks, at import time, whether numpy is already
loaded against a threaded OpenBLAS; if it is, it pins numba to **one** thread to
avoid oversubscription and prints a warning. `soundmap_api.py` imports numpy at
module scope, so the natural `from soundmap_api import SoundMapAPI` springs that
trap and silently halves acoular's speed — **325 ms/map instead of 153 ms** on
the four emulated cores (measured before the clock clamp went on, hence faster
than the 162 ms above; the point is the 2.1× ratio, not the absolute). Every
script here therefore does:

```python
import n100; n100.activate_from_argv()   # thread caps, before any of them are imported
sys.path.insert(0, ".../generator-acoular/soundmap")
import acoular                            # BEFORE numpy
```

`n100.activate()` raises rather than warns if numpy/numba/torch/acoular are
already in `sys.modules`, because at that point the thread-pool caps would be a
no-op that quietly produced wrong numbers.

## The workstation is *less* predictable than the emulated N100

`results/bench_host.*` is a `--host` reference run, and it is not the clean
"same thing but faster" it was meant to be:

| | mean | p50 | p95 | max | cores busy | over budget |
|---|---:|---:|---:|---:|---:|---:|
| acoular, host (22 threads) | 287.2 | **104.2** | 1651.0 | 3459.0 | 18.4 | 37/200 |
| 1-bit, host | 14.4 | 13.7 | 16.2 | 39.3 | 1.26 | 0/200 |

Only the p50 (~104 ms) is meaningful as a "faster machine" number. The tail is
numba spreading an ~800-grid-point problem over 22 *heterogeneous* threads — six
SMT P-cores at 4.8 GHz, eight E-cores at 3.8, two LP-E at 2.5 — where every
parallel region waits on whichever slice landed on the slowest core, and the
scheduler keeps migrating them. Four homogeneous cores at a locked clock are a
much better-behaved machine for this workload than the whole laptop is, which is
worth remembering before reading any host-side timing in the sibling
comparisons as an upper bound.

## Label agreement

`analyze_agreement.py` over four bags — one per experiment mode except Video —
3060 ticks, head boxes and VAD shared so every disagreement is the beamformer's:

| | agreement | n |
|---|---:|---:|
| **overall** | **90.0 %** | 3060 |
| VAD active | 93.3 % | 1168 |
| VAD silent | 87.9 % | 1892 |
| `G11_game4_DoA` | 88.0 % | 756 |
| `G2_game3_PSSP` | 91.9 % | 786 |
| `G12_game3_Tele` | 92.0 % | 766 |
| `G13_game4_Random` | 87.9 % | 752 |

(The head-box breakdown `analyze_agreement.py` also prints is degenerate on
these four bags — both boxes were valid on all 3060 ticks — so it says nothing
here. It is kept because bags with dropouts exist.)

The 307 disagreements split into two quite different populations:

- **229 on VAD-silent ticks**, dominated by `Others → Right` (103) and
  `Others → Left` (35). With no one talking, both generators are ranking room
  noise, acoular's flatter map puts the P98 `Others` metric on top and the
  1-bit map's sharper lobe lands inside a head box. These ticks are gated out
  of the live policy anyway.
- **78 on VAD-active ticks**, of which **66 (85 %) are the 1-bit generator
  saying `Teleoperator`** when acoular says `Left`/`Right`/`Others`. That is
  precisely the boundary-flip failure mode documented in
  [`../../generator-1bit/README.md`](../../generator-1bit/README.md) ("sharper
  but shakier"): the head boxes sit adjacent to the fixed speaking box on this
  rig, and the 1-bit map's narrower main lobe spills across the edge. The
  all-pairs correlation fix halved it; it did not remove it.

Both generators are markedly less confident when they disagree, which is the
reassuring shape for a disagreement population — these are close calls, not
confident errors:

| generator | mean margin on agreeing ticks | on disagreeing ticks |
|---|---:|---:|
| acoular | 124.3 | 74.5 |
| 1-bit | 82.8 | 38.9 |

For reference, over the 40 s `compare-generator-n100.mp4` clip specifically the
agreement is 142/160 (88.75 %) — bit-for-bit the same count the FFT beamformer
gets against the 1-bit generator on that clip in
[`../1bit-vs-pytorch/`](../1bit-vs-pytorch/), which is what you would expect
given acoular and the pytorch beamformer correlate at r≈0.99999.

## Caveats

- Timings are the `generate()` calls only. Bag reading, JPEG decode and the
  video compositing run on the same four cores but are not counted; neither is
  MediaPipe head detection, which the field pipeline also owes out of the same
  250 ms and which is not cheap on an N100.
- One bag (`G11_game4_DoA`, 300 ticks from t=40 s) for the timing numbers. The
  per-map cost has no data dependence worth speaking of — the p50/p95 spread is
  under 5 % once the GC pauses are removed — so more bags would not move it.
  The agreement numbers do use four bags.
- The 1-bit generator's `GAIN` is calibrated for this rig's mic gain, room and
  distance (see `../../generator-1bit/README.md`); the agreement numbers inherit
  that calibration.
- The agreement scan runs with `--host` (labels do not depend on which cores the
  process gets), so its wall time is not an N100 number and is not reported as
  one.
