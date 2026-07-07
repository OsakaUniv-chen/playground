# generator_compare — OLD vs NEW sound-map generator

Direct, apples-to-apples comparison of the two sound-map generators over **all 65
experiment bags** (13 groups × {DoA, PSSP, Random, Tele, Video}; interviews
excluded). Answers: *do the two generators produce the same 4-label
(L/R/Tele/Others) decision, and how do they differ?*

## Why this exists

`analysis1_until_0702/doa_kappa_debug_0705.md` blamed a low DoA/PSSP gaze-on-speaker
κ on a **generator mismatch**: the live robot ran the OLD (acoular) generator, the
offline replay recomputed "ground truth" with the NEW (PyTorch) generator, and
their *live-target-vs-offline-gt* agreement was only ~71–75%. But that number
conflated the generator with timing/window misalignment, the Tele/Others
random-walk, motor smoothing and live-vs-recomputed head boxes. The doc itself
notes **no one had actually compared the two generators head-to-head.** This does.

## The two generators

| | OLD (`soundmap_api.SoundMapAPI`) | NEW (`new_soundmap_api.NewSoundMapAPI`) |
|---|---|---|
| impl | vendored **acoular** `BeamformerBase.synthetic(f=2000, num=3)` | vendored **PyTorch** FFT-power sum, band 2000–8000 Hz |
| ran in | **live robot** (`mode_doa`/`mode_pssp`, `generator='old'`) | **offline analysis1** (`_targeting_env`, `generator='new'`) |
| shared | same 16-mic xml, fs=44100, blocksize=4096, 3-level merged grid, z=1.5, c=345, r_diag, +30 dB gain, Blackman-Harris, 66.1% overlap, output 64×64 in [0,160] | ← identical |

`new_sound_map.py` is vendored verbatim from the robot-PC source tree (SPEC rule:
no SSD imports at run time).

## Method (`compare_generators.py`)

Everything is **recreated from the raw signals** — no recorded `/head/head_box`,
`/sm_without_transform`, etc. (Video bags don't even have them):

- 4 Hz output-time tick grid, first 10 s discarded (audio-window fill).
- Per tick: the **same** 160-msg audio window ending at `t` is fed to **both**
  generators.
- Head boxes are **re-detected with MediaPipe** from `/camera/image_raw/compressed`
  (`head_box.HeadBoxAPI`, faithful to `head_node`); VAD from `/room2_audio/vad`.
  Head box + VAD are **shared** between the generators, so the labeling path is
  identical and any label disagreement is attributable to the generator alone.
- Each 64×64 map → **identical** labeling path (`labeling.label_current_sm`: mask if
  silent → `exp(x−max)` → colorize → `extract_target7` P87.5/P98) → one of
  L/R/Tele/Others.
- Per tick we log both labels, both region metrics, raw-map Pearson r, argmax
  displacement, peaks, VAD and head-box validity → `results/ticks/{bag}.parquet`.

## Run

```bash
cd analysis2/generator_compare
# uses the analysis2 venv (numpy<2 for acoular): ../code/venv
OPENBLAS_NUM_THREADS=1 ../code/venv/bin/python compare_generators.py \
    --bags all --workers 6 --frame-stride 2          # ~8 min/bag; resumable
../code/venv/bin/python aggregate.py                 # report + plots
```

Single bag smoke: `--bags G1_game5_DoA`. `--save-sm` also dumps the raw map stacks.

## Outputs (`results/`)

- `ticks/{bag}.parquet` + `ticks/index.csv` — per-tick table + per-bag metadata/timing
- `report.md`, `metrics.json`, `confusion.csv` — aggregate comparison
- `confusion_matrix.png`, `agreement_by_mode.png`, `label_distribution.png`,
  `raw_sm_similarity.png`
- `disagreements.png` + `disagreement_maps.npz` — from `inspect_disagreements.py`,
  which re-scans the parquet for the (3) ticks where the 4-label differs, recomputes
  both maps, asserts the labels reproduce, and renders OLD | NEW | raw-diff. All 3
  are sub-1-unit ties in the uint8 region metric during VAD-off ticks (peaks coincide).

## Headline finding

The NEW generator is a **faithful reimplementation** of the OLD one: raw maps
correlate at r≈0.99999 and, after the `exp(x−max)` labeling transform (which
suppresses the low-energy cells where the 2000–8000 Hz band vs the 1/3-octave band
differ), the 4-label decisions agree almost perfectly. The generator swap is
therefore **not** the primary source of the analysis1 κ shortfall — that came from
the other differences bundled into the live-vs-offline comparison. See `report.md`
for the exact numbers.
