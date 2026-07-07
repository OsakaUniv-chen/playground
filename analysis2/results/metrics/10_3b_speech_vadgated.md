## 10.3b VAD-gated speaking-turn length (追加 — silero room1 gate)

Does **not** replace §10.3. Room1 speech gate = silero-vad strict (threshold 0.7, ratio 0.6, window [t-0.46s, t], hangover 1 tick). A tick is a turn tick only if someone is really speaking in room1; silence breaks a run and is dropped. Pooled over 52 bags.

### Room1 speaking coverage
Overall speaking = **62%** of ticks (n=39794). Of ungated GT=Left/Right ticks, **33%** (6518/19696) fall in room1 silence — spurious acoustic flicker the gate removes.

| condition | speaking % | ticks |
|---|---|---|
| Tele | 65% | 9859 |
| PSSP | 59% | 10140 |
| DoA | 62% | 9969 |
| Random | 63% | 9826 |

### Speaking-turn duration per label — ungated vs VAD-gated
mean ± SD (median, n turns), seconds.

| label | ungated | VAD-gated | Δ mean |
|---|---|---|---|
| Left | 0.92 ± 0.94 (med 0.50, n 2755) | 0.84 ± 0.79 (med 0.50, n 1839) | -0.08 |
| Right | 0.94 ± 0.85 (med 0.75, n 2549) | 0.86 ± 0.74 (med 0.50, n 2034) | -0.07 |
| Teleoperator | 1.66 ± 1.51 (med 1.25, n 1930) | 1.30 ± 1.15 (med 1.00, n 1985) | -0.37 |
| Others | 0.69 ± 0.72 (med 0.50, n 2619) | 0.32 ± 0.15 (med 0.25, n 996) | -0.38 |

**Left+Right (facing participants)**: ungated 0.93 ± 0.90 s (median 0.75, n 5304) → VAD-gated **0.85 ± 0.76 s** (median 0.50, n 3873).

### Room1-VAD speaking-turn duration (silero native segments) — overall VAD feature
One turn = one uninterrupted room1 speech episode from the room1 VAD (gate on→off), independent of who / of the beamformed label. **Uses silero's native sample-resolution segments** (not the 4 Hz gate), so turn boundaries are not quantized to 0.25 s — the right resolution for a duration distribution. Silence-robust, speaker-agnostic (histogram: `room1_vad_segment_hist.png`).

mean **1.46 ± 1.46 s**, median 1.03, p90 3.17, min 0.10, max 18.28, n 3984 turns.

