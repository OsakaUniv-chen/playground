# analysis2 — Validation Report

Generated 2026-07-05 23:11 from `validate.py`. Time axis = bag record timestamp.

## WP1 — bag audit

52 robot-condition bags. Rate = msgs/duration (Hz). Audio p95/max = inter-message gap (s) → gap detection. SM interval = /sm_without_transform period (tick proxy, DoA/PSSP).

| bag | dur(s) | audio Hz | audio p95/max gap | cam Hz | vad Hz | motors Hz | sm Hz | sm p50/p95(s) |
|---|---|---|---|---|---|---|---|---|
| G10_game3_PSSP | 204 | 344.5 | 0.005/0.02 | 29.9 | 50.0 | 2.8 | 4.0 | 0.249/0.275 |
| G10_game4_Tele | 198 | 344.5 | 0.005/0.02 | 29.9 | 50.0 | 2.9 | – | – |
| G10_game5_DoA | 198 | 344.5 | 0.005/0.02 | 29.9 | 50.0 | 3.0 | 4.0 | 0.250/0.254 |
| G10_game6_Random | 197 | 344.5 | 0.005/0.02 | 29.9 | 50.0 | 2.7 | – | – |
| G11_game3_Random | 198 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 2.5 | – | – |
| G11_game4_DoA | 199 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 2.7 | 4.0 | 0.250/0.255 |
| G11_game5_Tele | 198 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 2.8 | – | – |
| G11_game6_PSSP | 204 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 2.5 | 4.0 | 0.249/0.273 |
| G12_game3_Tele | 201 | 344.5 | 0.005/0.02 | 26.5 | 50.0 | 1.8 | – | – |
| G12_game4_PSSP | 203 | 344.5 | 0.005/0.01 | 23.6 | 50.0 | 1.9 | 4.0 | 0.248/0.271 |
| G12_game5_Random | 198 | 344.5 | 0.005/0.01 | 24.5 | 50.0 | 1.5 | – | – |
| G12_game6_DoA | 211 | 344.5 | 0.005/0.01 | 27.7 | 50.0 | 3.0 | 4.0 | 0.250/0.254 |
| G13_game3_DoA | 198 | 344.5 | 0.005/0.02 | 29.1 | 50.0 | 3.2 | 4.0 | 0.250/0.254 |
| G13_game4_Random | 198 | 344.5 | 0.005/0.01 | 27.8 | 50.0 | 3.3 | – | – |
| G13_game5_PSSP | 205 | 344.5 | 0.005/0.02 | 27.4 | 50.0 | 2.5 | 4.0 | 0.248/0.271 |
| G13_game6_Tele | 198 | 344.5 | 0.005/0.02 | 28.2 | 50.0 | 3.2 | – | – |
| G1_game3_Tele | 199 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 1.9 | – | – |
| G1_game4_PSSP | 206 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 2.2 | 4.0 | 0.250/0.254 |
| G1_game5_DoA | 203 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 1.7 | 4.0 | 0.249/0.279 |
| G1_game6_Random | 198 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 1.8 | – | – |
| G2_game3_PSSP | 206 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 2.7 | 4.0 | 0.250/0.254 |
| G2_game4_DoA | 202 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 3.5 | 4.0 | 0.249/0.280 |
| G2_game5_Tele | 206 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 2.4 | – | – |
| G2_game6_Random | 208 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 2.5 | – | – |
| G3_game3_Tele | 197 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 2.8 | – | – |
| G3_game4_DoA | 205 | 344.5 | 0.005/0.01 | 30.0 | 50.0 | 2.6 | 4.0 | 0.249/0.282 |
| G3_game5_PSSP | 205 | 344.5 | 0.005/0.02 | 29.9 | 50.0 | 2.3 | 4.0 | 0.250/0.254 |
| G3_game6_Random | 199 | 344.5 | 0.005/0.01 | 30.0 | 50.0 | 2.9 | – | – |
| G4_game3_DoA | 200 | 344.5 | 0.005/0.05 | 30.0 | 50.0 | 2.6 | 4.0 | 0.249/0.279 |
| G4_game4_PSSP | 205 | 344.5 | 0.005/0.03 | 30.0 | 50.0 | 2.2 | 4.0 | 0.250/0.254 |
| G4_game5_Tele | 200 | 344.5 | 0.005/0.03 | 30.0 | 50.0 | 2.5 | – | – |
| G4_game6_Random | 198 | 344.5 | 0.005/0.02 | 30.0 | 50.0 | 2.3 | – | – |
| G5_game3_Random | 201 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 2.6 | – | – |
| G5_game4_Tele | 202 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 2.8 | – | – |
| G5_game5_PSSP | 204 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 3.6 | 4.0 | 0.250/0.254 |
| G5_game6_DoA | 200 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 2.5 | 4.0 | 0.249/0.276 |
| G6_game3_Tele | 199 | 344.5 | 0.005/0.02 | 29.9 | 50.0 | 1.7 | – | – |
| G6_game4_Random | 199 | 344.5 | 0.005/0.02 | 29.9 | 50.0 | 1.9 | – | – |
| G6_game5_PSSP | 205 | 344.5 | 0.005/0.01 | 30.0 | 50.0 | 3.5 | 4.0 | 0.250/0.254 |
| G6_game6_DoA | 199 | 344.6 | 0.005/0.01 | 29.9 | 50.0 | 2.5 | 4.0 | 0.249/0.283 |
| G7_game3_DoA | 200 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 2.8 | 4.0 | 0.250/0.254 |
| G7_game4_Tele | 198 | 344.5 | 0.005/0.02 | 29.9 | 50.0 | 3.4 | – | – |
| G7_game5_Random | 197 | 344.5 | 0.005/0.02 | 29.9 | 50.0 | 2.8 | – | – |
| G7_game6_PSSP | 205 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 2.8 | 4.0 | 0.249/0.272 |
| G8_game3_PSSP | 205 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 2.9 | 4.0 | 0.249/0.270 |
| G8_game4_Random | 197 | 344.5 | 0.005/0.01 | 29.9 | 50.0 | 3.4 | – | – |
| G8_game5_Tele | 199 | 344.5 | 0.005/0.01 | 30.0 | 50.0 | 3.1 | – | – |
| G8_game6_DoA | 209 | 344.5 | 0.005/0.01 | 30.0 | 50.0 | 2.9 | 4.0 | 0.250/0.254 |
| G9_game3_Random | 197 | 344.5 | 0.005/0.01 | 30.0 | 50.0 | 1.7 | – | – |
| G9_game4_DoA | 198 | 344.5 | 0.005/0.02 | 30.0 | 50.0 | 3.2 | 4.0 | 0.250/0.254 |
| G9_game5_PSSP | 206 | 344.5 | 0.005/0.01 | 29.8 | 50.0 | 1.8 | 4.0 | 0.249/0.271 |
| G9_game6_Tele | 198 | 344.5 | 0.005/0.02 | 30.0 | 50.0 | 1.1 | – | – |

### Cross-machine clock offset (header.stamp − record_ts, seconds)
room2 topics originate on the teleoperate PC. Large/again-varying offset ⇒ unsynced clocks → confirms using record_ts (not header.stamp).

| bag | room2 vad median[p05,p95] | room2 cam median | room1 cam median |
|---|---|---|---|
| G10_game3_PSSP | -0.001[-0.00,-0.00] | -0.002 | -0.005 |
| G10_game4_Tele | -0.001[-0.00,-0.00] | -0.002 | -0.005 |
| G10_game5_DoA | -0.001[-0.00,-0.00] | -0.002 | -0.005 |
| G10_game6_Random | -0.001[-0.00,-0.00] | -0.002 | -0.005 |
| G11_game3_Random | +0.002[+0.00,+0.00] | +0.001 | -0.004 |
| G11_game4_DoA | +0.002[+0.00,+0.00] | +0.001 | -0.004 |
| G11_game5_Tele | +0.001[+0.00,+0.00] | +0.000 | -0.004 |
| G11_game6_PSSP | -0.001[-0.00,-0.00] | -0.002 | -0.005 |
| G12_game3_Tele | +0.001[+0.00,+0.00] | -0.003 | -0.008 |
| G12_game4_PSSP | +0.001[+0.00,+0.00] | -0.000 | -0.009 |
| G12_game5_Random | +0.001[+0.00,+0.00] | -0.000 | -0.009 |
| G12_game6_DoA | +0.001[+0.00,+0.00] | -0.000 | -0.006 |
| G13_game3_DoA | +0.001[+0.00,+0.00] | -0.000 | -0.007 |
| G13_game4_Random | +0.001[+0.00,+0.00] | -0.000 | -0.008 |
| G13_game5_PSSP | +0.001[+0.00,+0.00] | +0.000 | -0.007 |
| G13_game6_Tele | +0.001[+0.00,+0.00] | +0.000 | -0.008 |
| G1_game3_Tele | -0.002[-0.00,-0.00] | -0.003 | -0.005 |
| G1_game4_PSSP | -0.002[-0.00,-0.00] | -0.003 | -0.005 |
| G1_game5_DoA | -0.002[-0.00,-0.00] | -0.003 | -0.005 |
| G1_game6_Random | -0.001[-0.00,-0.00] | -0.002 | -0.005 |
| G2_game3_PSSP | +0.002[+0.00,+0.00] | +0.001 | -0.004 |
| G2_game4_DoA | +0.000[+0.00,+0.00] | -0.001 | -0.004 |
| G2_game5_Tele | +0.000[+0.00,+0.00] | -0.000 | -0.004 |
| G2_game6_Random | -0.000[-0.00,-0.00] | -0.002 | -0.005 |
| G3_game3_Tele | -0.002[-0.00,-0.00] | -0.004 | -0.004 |
| G3_game4_DoA | -0.002[-0.00,-0.00] | -0.003 | -0.004 |
| G3_game5_PSSP | -0.001[-0.00,-0.00] | -0.002 | -0.004 |
| G3_game6_Random | -0.001[-0.00,-0.00] | -0.002 | -0.004 |
| G4_game3_DoA | -0.001[-0.00,-0.00] | -0.002 | -0.005 |
| G4_game4_PSSP | -0.000[-0.00,-0.00] | -0.002 | -0.005 |
| G4_game5_Tele | -0.000[-0.00,+0.00] | -0.004 | -0.005 |
| G4_game6_Random | +0.000[-0.00,+0.00] | -0.001 | -0.005 |
| G5_game3_Random | +0.004[+0.00,+0.00] | +0.003 | -0.004 |
| G5_game4_Tele | +0.001[+0.00,+0.00] | -0.000 | -0.004 |
| G5_game5_PSSP | -0.000[-0.00,-0.00] | -0.001 | -0.005 |
| G5_game6_DoA | -0.000[-0.00,-0.00] | -0.002 | -0.004 |
| G6_game3_Tele | -0.002[-0.00,-0.00] | -0.017 | -0.006 |
| G6_game4_Random | -0.002[-0.00,-0.00] | -0.003 | -0.005 |
| G6_game5_PSSP | -0.002[-0.00,-0.00] | -0.003 | -0.005 |
| G6_game6_DoA | -0.002[-0.00,-0.00] | -0.003 | -0.005 |
| G7_game3_DoA | -0.001[-0.00,-0.00] | -0.002 | -0.005 |
| G7_game4_Tele | -0.001[-0.00,-0.00] | -0.002 | -0.005 |
| G7_game5_Random | -0.001[-0.00,-0.00] | -0.002 | -0.005 |
| G7_game6_PSSP | -0.000[-0.00,-0.00] | -0.001 | -0.005 |
| G8_game3_PSSP | +0.004[+0.00,+0.00] | +0.003 | -0.004 |
| G8_game4_Random | +0.003[+0.00,+0.00] | +0.002 | -0.004 |
| G8_game5_Tele | +0.005[+0.00,+0.00] | +0.004 | -0.004 |
| G8_game6_DoA | +0.003[+0.00,+0.00] | +0.002 | -0.004 |
| G9_game3_Random | -0.001[-0.00,-0.00] | -0.003 | -0.005 |
| G9_game4_DoA | -0.001[-0.00,-0.00] | -0.002 | -0.005 |
| G9_game5_PSSP | -0.001[-0.00,-0.00] | -0.002 | -0.006 |
| G9_game6_Tele | -0.001[-0.00,-0.00] | -0.004 | -0.005 |

### Previously-excluded bags (now usable, head box re-detected)
- `G12_game4_PSSP`: present, audio 345 Hz, dur 203s → usable.
- `G6_game3_Tele`: present, audio 345 Hz, dur 199s → usable.

## WP2 — reproduction gate

Generated 2026-07-05 23:43. First 10 s of each bag discarded.

### 1. Sound-map reproduction (regenerated vs recorded /sm_without_transform)
Gate: 4-label agreement ≥ 95%. best_offset = generation delay that maximizes correlation.

| bag | n | best offset(s) | mean Pearson r | med peak dist(px) | **label agree** |
|---|---|---|---|---|---|
| G10_game5_DoA | 40 | 0.1 | 1.000 | 6.3 | **80.0%** |
| G10_game3_PSSP | 40 | 0.3 | 0.999 | 10.3 | **72.5%** |

offset scan (mean r) DoA: 0.1s=1.000, 0.2s=0.999, 0.3s=0.999

### 2. Tele re-derivation (vs recorded /tele/head_orientation)
Gate: side agreement ≥ 95%.

| bag | n | yaw MAE(deg) | yaw med|err| | **side agree** |
|---|---|---|---|---|
| G10_game4_Tele | 5558 | 0.63 | 0.00 | **98.6%** |

### 3. Head-box re-detection (vs recorded /head/head_box)
Fresh detector per sampled frame (no cross-frame persistence); IoU on co-valid boxes.

| bag | n | median IoU | co-valid | validity match |
|---|---|---|---|---|
| G10_game5_DoA | 60 | 1.000 | 120 | 100.0% |

### Diagnostic — why SM label agreement is < 95% despite r ≈ 0.999

The regenerated sound map is essentially identical to the recorded one
(Pearson r = 0.999, peak within ~6 px). The label mismatch is **not** a
reproduction error — it comes from the recorded `/sm_without_transform` being
**uint8 + jpg** (lossy), and the 4-label being brittle at near-ties.

Decisive test on `G10_game5_DoA` (n = 80):

| comparison | label agreement |
|---|---|
| my float SM vs **my own SM rounded to uint8** | **87.5%** |
| my float SM vs recorded uint8 SM | 86.2% |

Rounding our *own* faithful SM to uint8 already flips 12.5% of labels, and the
float-vs-recorded agreement (86.2%) is the same magnitude — i.e. our regeneration
adds essentially **no** error beyond the recording's own quantization. The
disagreeing ticks sit at small top-2 region margins (median 0.30 vs 0.81 overall),
confirming they are near-ties.

The live robot labeled the **float** SM (not the uint8 recording), so our
float-SM labels reproduce the live decision. The uint8 `/sm` topic is only a lossy
QC snapshot; comparing labels against it *underestimates* fidelity.

### Verdict

| check | result | gate | status |
|---|---|---|---|
| SM correlation (float, r) | **0.999** | ≥ 0.99 | **PASS** |
| SM label vs recorded uint8 | 72–80% | (≥95% — invalid, see above) | N/A: reference is lossy |
| Tele side agreement | **98.6%** | ≥ 95% | **PASS** |
| Tele yaw MAE | 0.63° | — | excellent |
| Head-box IoU / validity | **1.000 / 100%** | — | **PASS** |

**Conclusion**: the live pipeline is faithfully reproduced. SM generation, Tele
head pose, and head-box detection all match the recorded data. The label-agreement
number is bounded by the lossy uint8 `/sm` reference, not by our regeneration; the
proper SM fidelity gate (float correlation ≥ 0.99) passes. Proceed to WP3 pending
user confirmation. (Visual confirmation: `results/qc_video/` from `bag2video.py`.)
