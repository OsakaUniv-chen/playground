# generator-1bit

1-bit ("sign-bit XOR correlator") acoustic-camera sound-map generator, built
from the 4-step PoC handed down by the supervisor. Self-contained like
`../video-generator/`: everything needed to *generate* a sound map lives in
this folder. It's CPU-only by construction -- no FFT, no float
multiply-accumulate in the hot path, no torch dependency at all; the whole
appeal of a 1-bit acoustic camera is that it doesn't need a GPU.

(Originally created as `video-generator-1bit/`, renamed to `generator-1bit/`.)

| file | purpose |
|---|---|
| `onebit_soundmap.py` | the generator (`OneBitSoundMapGenerator` / `OneBitSoundMapAPI`), same call interface as `beamform_soundmap.SoundMapAPI` |
| `bag_io.py` / `labeling.py` | ROS2 bag reader + sound-map mask/transform/4-label extraction, copied from `../video-generator/` (self-contained convention) |
| `compare_video.py` | real-bag side-by-side video: left = FFT beamformer, right = 1-bit |
| `compare-generator.mp4` | rendered output of the above, on bag `G11_game4_DoA` |
| `analyze_disagreement.py` | label-only pass over a whole bag: agreement rate by VAD state / confusion matrix / decision margins (no video, much faster) |
| `save_disagreement_frames.py` | dumps one annotated PNG per disagreeing tick into `results/disagreement_frames/{vad_active,vad_silent}/`, for eyeballing |
| `validate_synthetic.py` | synthetic point-source correctness/precision check vs the FFT beamformer (no bag needed) |
| `make_comparison_figure.py` | renders `results/comparison.png` from the synthetic tests |

```bash
python compare_video.py --bag G11_game4_DoA   # real-data video: pytorch on GPU, 1-bit on CPU
python validate_synthetic.py                  # synthetic precision check
python make_comparison_figure.py
```
Run inside the `wolf` virtualenv (needs numpy/scipy/cv2; torch only because
`compare_video.py`/`validate_synthetic.py` import the *other* generator for
comparison -- `onebit_soundmap.py` itself never imports torch).
`compare_video.py` reads from `/media/chen/Extreme SSD/PSSPData/WordWolfExp`
(the post-2026-07 PSSPData reorg location -- see `train-pssp/CONTEXT.md`).

## The 4 steps, and where they live in the code

1. **手順1 (LUT)** — `OneBitSoundMapGenerator._prepare_lut`: for every grid
   point and every mic, the arrival-time difference vs. mic0 is computed from
   geometry and rounded to an integer sample count. Reuses the exact same
   merged polar grid / 16-mic UMA geometry as `beamform_soundmap.py`, so the
   two generators' 64x64 outputs sit on an identical pixel grid.
2. **手順2 (BPF & 1bit化)** — `_binarize`: zero-phase band-pass (2000-8000Hz,
   same band as the FFT beamformer) via `scipy.signal.sosfiltfilt`, then keep
   only the sign. This is the one floating-point step, done once per call
   (not per grid point).
3. **手順3 (ビットシフト & XOR)** — `_xor_correlate`: for each grid point and
   every one of the **C(16,2)=120 mic pairs** (not just the 15 pairs relative
   to a single reference mic -- see "sharper but shakier" below for why that
   changed), mic j is shifted by that pair's LUT delay relative to mic i and
   XOR'd against it; the mean match rate over all 120 pairs = agreement
   score. The ~13cm mic array means only a few dozen unique integer delays
   exist per pair, so this dedupes shifts across grid points (see docstring)
   instead of gathering a full `(n_grid, N)` array per pair -- also how the
   real hardware does it (a small bank of shift registers reused across
   steering directions).
4. **手順4 (2Dマップ)** — `generate`: scores are rescaled (`_score_to_db`) and
   pushed through the same Delaunay interpolation as the FFT beamformer, onto
   a 64x64 map.

## `compare-generator.mp4`: real-data side-by-side

`compare_video.py` mirrors `generator-compare/compare_video.py`: feeds the
IDENTICAL 160-message audio window to both generators at each 4Hz tick, and
renders them side by side over the room1 camera with head boxes / speaking
box / 4-label decision / per-map timing burned in. Left = pytorch FFT
beamformer **on GPU** (`--pytorch-device cuda`, its normal deployment target
when one's available), right = 1-bit XOR **on CPU** (always -- not needing a
GPU is the point of the architecture, not a benchmarking constraint; pass
`--pytorch-device cpu` to instead compare both on CPU only).
`compare-generator.mp4` in this folder is 40s of bag `G11_game4_DoA` starting
at t=40s (same bag/window as `../video-generator/`'s own benchmark).

Over that clip: **142/160 ticks (88.75%) label agreement**, PYTORCH (CUDA)
5.6ms/map vs 1-BIT (CPU) 13.6ms/map. CPU-vs-CPU (`--pytorch-device cpu`),
1-BIT (15.7ms/map) is actually *faster* than PYTORCH's own CPU path
(18.4ms/map) -- see "putting the CPU speed back" below for how, after the
120-pair precision fix briefly made this 6x slower (~149ms/map).

### Two debugging detours from getting this video right (worth knowing about)

**GPU vs CPU-threading confusion.** An earlier CPU-only version of this
script (`SoundMapAPI(device="cpu")` for both) showed PYTORCH at ~60ms/map,
much slower than the ~18-21ms/map `../video-generator/README.md` CPU
benchmark on the same bag/window. GPU contention was a reasonable first
guess, but `nvidia-smi` showed 0% GPU utilization throughout and load average
stayed ~2.2 on 22 cores the whole run -- no external contention, and
irrelevant anyway since `device="cpu"` never touches the GPU. The actual
cause: the script originally copied `os.environ.setdefault
("OPENBLAS_NUM_THREADS", "1")` / `os.environ.setdefault("OMP_NUM_THREADS",
"1")` from `generator-compare/compare_video.py`'s convention, where the cap
stops numba/acoular from oversubscribing when `compare_generators.py
--workers N` runs several bags in *parallel processes*. This script is
single-process, so the cap did nothing useful -- except also throttle
torch's own CPU `einsum` calls (the FFT beamformer's cross-spectral-matrix
step) down to a single thread (confirmed: `torch.get_num_threads()` was `1`
with the env var set, `16` without). Removed here; the 1-bit generator (plain
numpy indexing/boolean ops, not BLAS/OpenMP-threaded) was unaffected either
way.

### A real calibration bug this surfaced (worth knowing about)

The first render of this video came out with ~15-40% label agreement --
much worse than the synthetic tests suggested. Cause: `_score_to_db`
originally mapped raw match-rate `score` linearly, assuming the achievable
ceiling was near 1.0 (`0.5->0, 1.0->160`). On real reverberant/noisy audio
the achievable peak match rate is only ever ~0.6-0.7, so that mapping wasted
almost the entire `[0,160]` range on scores that never occur in practice,
and packed all the real signal into a tiny sliver near 0. The shared
downstream display/label pipeline (`labeling.transform_sm` = `exp(sm -
sm.max())`, used identically for both generators) then crushed that sliver
down to a near-single visible pixel -- both in the video overlay AND in the
percentile-based 4-label decision itself (`extract_target7` runs on the
post-`exp()` map), so it wasn't just a cosmetic issue.

Fixed by calibrating the gain against real data instead of the idealized
0.5-1.0 range: `GAIN = 40` (`_score_to_db` = `clip(score-0.5, 0, None) *
GAIN`) was picked so the 1-bit map's fraction of "visible" (>0.05 after
`exp(sm-sm.max())`) pixels lands in the same range as the FFT beamformer's
on real ticks (both ~0.02-0.07, checked at 7 timestamps across the bag).
This only rescales `generate()`'s output display magnitude -- it's a
monotonic positive linear transform, so it doesn't affect
`validate_synthetic.py`'s peak-location/ratio-based precision numbers below,
which were already scale-invariant.

## Does 1-bit quantization degrade precision?

Short answer: **barely, for a single dominant speaker; yes, measurably, once
a second, quieter speaker is present** -- which matters here because the
actual use case (word-wolf) is multi-speaker. The real-data video above
(88.75% label agreement) is consistent with this: most ticks have one clear
dominant talker. (Numbers below are post all-pairs-correlation fix --
see "Sharper but shakier" further down; that change also measurably improved
both tables here, since more mic pairs averaged per grid point means lower
variance regardless of whether the disagreement shows up as a boundary flip
or a low-SNR localization error.)

This was validated with a controlled simulation (`validate_synthetic.py`):
synthesize band-limited (2-8kHz) noise "speech" at a known grid location,
propagate it to all 16 mics with exact fractional-sample delay (FFT phase
shift), add mic self-noise at a chosen SNR, quantize to int16 -- then feed
the byte-identical chunk to both generators.

**Single dominant source, localization error vs. per-sample SNR** (mean over
5 grid points, error in 64x64-map pixels; map diagonal is ~90px):

| SNR (dB) | FFT beamformer err | 1-bit err | pearson(fft, 1bit) |
|---:|---:|---:|---:|
| 20 | 0.40 | 0.68 | 0.45 |
| 10 | 0.40 | 0.68 | 0.43 |
| 0 | 0.40 | 0.68 | 0.37 |
| -10 | 0.40 | 0.68 | 0.30 |
| -15 | 0.20 | 0.57 | 0.28 |
| -20 | 0.40 | 1.17 | 0.28 |
| -25 | 0.88 | 1.25 | 0.35 |
| -30 | 15.84 | **12.96** | 0.48 |

Both track the true source to sub-pixel accuracy down to about -20dB (before
the all-pairs fix, 1-bit's error already jumped to 8px at -25dB; now it
holds to ~1px all the way to -25dB, and is actually *more* robust than the
FFT beamformer at -30dB). Theory predicts 1-bit sign correlation still costs
~2/pi, i.e. -1.96dB, of matched-filter SNR relative to a linear correlator
per *pair*, but averaging 120 pairs instead of 15 buys back roughly
`sqrt(120/15) ≈ 2.8x` in effective SNR, which is in the right ballpark for
the ~5dB the breakdown threshold moved. With ~20000 samples integrated per
tick (~0.45s @ 44.1kHz) either threshold is far below any speech-level SNR
this rig will actually see.

**Two unequal-loudness sources, secondary-source visibility** (fixed
geometry, secondary source `rel_dB` quieter than the primary):

| secondary level | FFT: value-at-secondary / peak | 1-bit: value-at-secondary / peak | FFT resolves it as a local peak | 1-bit resolves it as a local peak |
|---:|---:|---:|:---:|:---:|
| 0dB (equal) | 1.000 | 1.000 | yes | yes |
| -6dB | 0.952 | 0.193 | yes | **yes** (was no, pre-fix) |
| -12dB | 0.873 | 0.012 | no | no |
| -18dB | 0.791 | 0.000 | no | no |

The all-pairs fix pushed the "still resolvable" boundary from between
0dB/-6dB out to between -6dB/-12dB, but the underlying nonlinearity is
unchanged -- this is the same averaging effect as the SNR table above, not a
different mechanism. 1-bit hard-limiting is a nonlinearity (sign of the
sum, not a sum of signs), so a stronger co-channel source "captures" a
weaker one much more aggressively than the linear FFT beamformer does -- the
weaker speaker stops being separable about 6dB earlier. For a single active
talker (the common case, gated by VAD) this doesn't matter; for two people
talking over each other it means the 1-bit map is more likely to just show
the louder one.

**`results/comparison.png`** shows the raw shape: for a single source the
1-bit map's main lobe is actually *narrower* than the FFT beamformer's
(full broadband time-domain correlation across ~20000 samples is more
spatially discriminating than a segmented-FFT power sum on this compact
13cm array) -- each panel is independently min-max normalized purely to show
each generator's own spatial shape, since the two scales are display
conventions, not comparable physical units (see `_score_to_db`'s docstring,
and the calibration story above for why that matters in practice).

## Sharper but shakier: fixing boundary jitter with all-pairs correlation

`save_disagreement_frames.py` + eyeballing the PNGs surfaced a concrete
failure mode: when a talker's head box sits right next to the Teleoperator
speaking box (they're adjacent on-screen for this rig), the 1-bit map's
narrower main lobe would occasionally spill across the boundary and flip the
label to the neighboring region, even with VAD active (a real talker, not
noise). `analyze_disagreement.py --bag G11_game4_DoA` on the full 189s bag:

| | before (15 pairs, ch0-relative) | after (120 pairs, all mics) |
|---|---:|---:|
| overall agreement | 81.3% (615/756) | **88.0%** (665/756) |
| VAD-active agreement | 94.5% (259/274) | **96.4%** (264/274) |
| VAD-active "Right -> Teleoperator" boundary flips | 10 | **5** |

The original `_xor_correlate` only compared each mic against mic0 (15
pairs) -- a GCC-style single-reference-channel correlator. Switched it to
every one of the `C(16,2)=120` mic pairs instead (SRP-PHAT-style full
pairwise correlation, `_prepare_lut`/`_xor_correlate` in `onebit_soundmap.py`):
each grid point's score now averages over ~8x more independent bit-agreement
observations, which lowers its variance without changing what's being
computed (still pure integer-shift + XOR, no multiply-accumulate). Also
re-tightened decision-margin calibration: the 1-bit generator's mean margin
on ticks it still gets wrong dropped from 62 (before) to 18 (after) -- it's
much more honestly *unsure* when it disagrees now, rather than confidently
wrong. `_score_to_db`'s `GAIN` was re-calibrated for the new score
distribution (`50`, was `40` -- see its docstring).

**This initially cost ~6x the runtime** (this numpy reference implementation
went from ~25ms to ~149ms per map: 120 gather-and-reduce passes instead of
15) -- which defeated the entire point of a 1-bit architecture: the reason
to build this instead of just using the FFT beamformer on CPU is CPU speed.
See "putting the CPU speed back" right below for the actual fix.

**Other ideas considered but not implemented** (the all-pairs fix was tried
first since it was pure upside for solving *this specific* boundary-flip
symptom without giving up anything about the "just integer shifts + XOR"
architecture):
- **Spatial (Gaussian) smoothing of the score map** before interpolation --
  cheap, would also widen the main lobe, but treats the symptom (jitter) not
  the cause (high per-grid-point variance from too few pairs), and would
  blur away the narrower/sharper localization `results/comparison.png`
  showed as a genuine advantage.
- **Sub-sample delay interpolation** in the LUT (currently rounds to the
  nearest integer sample) -- would smooth out the delay-quantization "steps"
  in the score field, but needs real-valued interpolation between two
  candidate integer shifts, which gives up the pure-integer-shift property
  that makes this architecture cheap in the first place.
- **A smaller hand-picked subset of pairs** (e.g. ~30-40 chosen for baseline
  diversity instead of exhaustively all 120) -- a reasonable middle ground,
  but made moot by the bit-packing fix below, which removed the tradeoff
  entirely rather than splitting the difference.

## Putting the CPU speed back: bit-packing + popcount

The ~6x slowdown above wasn't actually a fundamental property of using 120
pairs -- it was because the numpy reference implementation stored one
sign-bit per **byte** (`uint8`), so "bit-shift + XOR" was really doing one
comparison per CPU instruction, wasting 7 of every 8 bits of memory bandwidth
and getting none of the word-level parallelism that makes 1-bit correlation
cheap on real hardware in the first place. Fixed that instead of trading
away precision for speed:

- **`_pack_bits`/`_pack_channel`**: pack 64 sign-bits per `uint64` word
  (bit *i* of word *w* = sample `64w+i`), zero-padded so any in-range shift
  is a plain array slice.
- **`_xor_correlate`**: for each pair's unique delay-difference values,
  build the shifted word array via a `>> r` / `<< (64-r)` combine of two
  neighboring words (vectorized across all unique shifts at once, same
  "reduce in unique-shift-space, broadcast to grid points last" trick as
  before -- just at word granularity, ~64x fewer elements per pair), XOR
  against the unshifted reference word array, then **`_popcount64`**: a
  branchless SWAR (SIMD-within-a-register) bit-population-count in ~5
  vectorized ops, replacing a per-sample equality check with one op per 64
  samples.
- Verified correct against the old per-sample implementation on both a
  synthetic hand-built case (exact match to within the documented
  tail-padding rounding, see `_xor_correlate`'s docstring) and real bag data
  (score arrays numerically identical to 4+ significant figures).

**Result: 120-pair correlation at ~15ms/map, CPU-only** -- faster than the
*original* 15-pair non-packed scheme (~25ms), let alone the 120-pair
non-packed one (~149ms). CPU-vs-CPU against the FFT beamformer
(`--pytorch-device cpu`), the 1-bit generator (15.7ms/map) is now actually
*faster* than PYTORCH's own CPU path (18.4ms/map) -- see
`compare-generator.mp4`'s numbers above. `GAIN` did not need re-calibrating
(the packed score distribution matched the unpacked one to observed
precision), since the packed/unpacked score computations are the same
quantity, just computed 64 samples at a time instead of one at a time.

## Caveats

- The synthetic tests still model a free-field point source: no mic preamp
  noise floor, no directivity, and (for the SNR-sweep table) no reverberation.
  The real-data video above is the actual reverberant/multi-talker check;
  the synthetic tests are for isolating *why* differences happen.
- Timing is device-for-device, not a controlled benchmark: PYTORCH gets a GPU
  (its normal deployment target, ~6ms/map) and the 1-bit generator never does
  (the point of the architecture, ~15ms/map with the current 120-pair
  bit-packed implementation -- see "putting the CPU speed back" above).
  `_popcount64`'s SWAR trick is still a general-purpose-CPU numpy
  implementation, not the actual bit-packed-XOR-plus-popcount instruction a
  real FPGA/embedded 1-bit camera would run in dedicated hardware, which
  would be faster and lower-power still (and is where this architecture's
  GPU-free-ness -- useful on hardware with no GPU to fall back on at all --
  fully pays off), but the numpy version already demonstrates the CPU-speed
  case this PoC exists to make.
- `GAIN=50` was calibrated on one bag (`G11_game4_DoA`) at 7 sampled
  timestamps (re-calibrated when the pair scheme changed from 15 to 120; the
  later bit-packing change didn't need a further re-calibration since it's
  numerically the same score computation, just vectorized differently). It's
  a fixed constant (not per-tick auto-calibrated, which wouldn't be usable in
  a real deployed pipeline), so it should be re-checked if this generator is
  ever pointed at a very different mic gain/room/distance setup.
