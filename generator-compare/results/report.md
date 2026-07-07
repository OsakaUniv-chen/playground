# OLD vs NEW sound-map generator — comparison

Full head-to-head over **65** experiment bags (13 groups × DoA/PSSP/Random/Tele/Video; interviews excluded), **49,786** ticks at 4 Hz. This file is the consolidated record; regenerate it with `python aggregate.py`.

## The two generators

- **OLD** (`soundmap_api.SoundMapAPI`): vendored **acoular** `BeamformerBase.synthetic(f=2000, num=3)` — the generator that ran on the **live robot** (`mode_doa`/`mode_pssp`, `generator='old'`).
- **NEW** (`new_soundmap_api.NewSoundMapAPI`): vendored **PyTorch** FFT-power sum over the 2000–8000 Hz band — the generator the **offline analysis1** used (`_targeting_env`, `generator='new'`).
- Both share the same 16-mic xml, fs=44100, blocksize=4096, 3-level merged grid, z=1.5, c=345, r_diag, +30 dB gain, Blackman-Harris, 66.1% overlap, and emit a 64×64 map in [0,160].

## Method

Everything is recreated from the **raw** signals — no recorded `/head/head_box` or `/sm_without_transform` (Video bags don't even have them). Per 4 Hz tick (first 10 s discarded for buffer fill): the **same** 160-msg audio window ending at `t` is fed to **both** generators; head boxes are re-detected with MediaPipe from `/camera/image_raw/compressed` and the VAD gate comes from `/room2_audio/vad`. The head box + VAD are **shared**, and each 64×64 map goes through the **identical** labeling path (`label_current_sm`: mask-if-silent → `exp(x−max)` → colorize → `extract_target7`, P87.5/P98). So the only thing that can differ is the beamformer, and any label disagreement is attributable to it alone.

## Headline

- **4-label agreement**: 1.000  (Cohen's κ = 1.000)
- **L/R side agreement** (both generators call a side): 1.000 on 24,072 ticks
- new==old among ticks OLD called L/R: 1.000 (24,074 ticks)
- old==new among ticks NEW called L/R: 1.000 (24,072 ticks)
- speed: OLD 804.2 ms/map vs NEW 104.9 ms/map (**7.7× faster**)
- raw map Pearson r: median 1.00, mean 1.00; peak in same cell 1.00, within 2 cells 1.00

## Agreement by mode

| mode | n ticks | 4-label agree | κ | L/R side agree | n both-L/R |
|---|--:|--:|--:|--:|--:|
| DoA | 9,969 | 1.000 | 1.000 | 1.000 | 4,703 |
| PSSP | 10,140 | 1.000 | 1.000 | 1.000 | 4,668 |
| Random | 9,826 | 1.000 | 1.000 | 1.000 | 5,140 |
| Tele | 9,859 | 1.000 | 1.000 | 1.000 | 5,187 |
| Video | 9,992 | 1.000 | 1.000 | 1.000 | 4,374 |

## Confusion (rows = OLD, cols = NEW), counts

| OLD\NEW | L | R | Tele | Others |
|---|--:|--:|--:|--:|
| **L** | 12,546 | 0 | 0 | 1 |
| **R** | 1 | 11,525 | 0 | 1 |
| **Tele** | 0 | 0 | 16,127 | 0 |
| **Others** | 0 | 0 | 0 | 9,585 |

## Acoustic label distribution

| label | OLD | NEW |
|---|--:|--:|
| L | 0.252 | 0.252 |
| R | 0.232 | 0.231 |
| Tele | 0.324 | 0.324 |
| Others | 0.193 | 0.193 |

## The 3 disagreeing tick(s)

The only ticks whose 4-label differs. The label = argmax of the region percentile metric (green channel, uint8 0–255; ties broken by priority L>R>Tele>Others). Every disagreement below is a **≤1-unit tie** at that quantized boundary while the maps themselves are near-identical (see the `pearson`/`peak_dist` columns and `disagreements.png`).

| bag | tick | VAD | OLD→NEW | OLD [L R T O] | NEW [L R T O] | r | peakΔ |
|---|--:|:-:|---|---|---|--:|--:|
| G10_game3_PSSP | 443 | off | R→L | 209.1 210.0 0.0 161.0 | 210.0 210.0 0.0 161.0 | 0.9999 | 0 |
| G8_game3_PSSP | 307 | off | L→Others | 90.0 5.0 0.0 90.0 | 90.0 5.0 0.0 91.0 | 0.9999 | 0 |
| G8_game5_Tele | 485 | off | R→Others | 133.0 177.0 0.0 177.0 | 133.0 176.1 0.0 177.0 | 0.9999 | 0 |

Inspect the actual maps with `python inspect_disagreements.py` (re-scans this parquet, recomputes both maps, asserts the labels reproduce, writes `disagreements.png` + `disagreement_maps.npz`).

## Interpretation

Head-to-head, the two generators are **functionally identical**: the raw 64×64 maps correlate at r≈0.99991 and the 4-label decision agrees on 0.9999 of ticks (3 disagreements in 49,786). The 2000–8000 Hz band (NEW) vs 1/3-octave-at-2000 (OLD) difference lands almost entirely in low-energy cells that the `exp(x−max)` labeling transform crushes to ~0, so it never moves the argmax that decides the label.

This **revises** the hypothesis in `analysis1_until_0702/doa_kappa_debug_0705.md`. That note attributed the low DoA/PSSP gaze-on-speaker κ to the OLD↔NEW generator swap, but it never actually compared the generators (it compared the *live* target against the *offline-recomputed* gt, ~71–75%). Isolating the generator alone — same audio window, same head boxes, same VAD, same labeling path — the generators barely differ. The analysis1 κ shortfall must therefore come from the OTHER differences bundled into that live-vs-offline comparison (window/timing misalignment, the Teleoperator/Others random-walk decoupling, motor smoothing, live-vs-recomputed head boxes), not from the beamformer. The NEW generator is also ~7-8× faster.

Plots: `confusion_matrix.png`, `agreement_by_mode.png`, `label_distribution.png`, `raw_sm_similarity.png`. Machine-readable: `metrics.json`, `confusion.csv`.
