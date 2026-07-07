## 10.4 PSSP prediction accuracy (pooled all bags)
L/R-restricted: base = ticks where GT(t+h)∈{L,R}; a prediction of Teleoperator/Others counts as wrong. 4-label = full match. Baseline = persistence (GT(t)).

| horizon | GT lag(ticks) | PSSP L/R acc | persist L/R acc | PSSP 4-lab acc | persist 4-lab acc | n(L/R) |
|---|---|---|---|---|---|---|
| +0.5s | 2 | 0.596 | 0.577 | 0.495 | 0.610 | 19649 |
| +1.0s | 4 | 0.548 | 0.450 | 0.403 | 0.474 | 19596 |
| +1.5s | 6 | 0.508 | 0.402 | 0.365 | 0.415 | 19546 |
| +2.0s | 8 | 0.483 | 0.368 | 0.344 | 0.380 | 19496 |

### pssp_p10 (+1.0s): predicted-label distribution & per-actual-label accuracy (n=39586)
PSSP over-predicts L/R and under-predicts the persistent Teleoperator/Others.

| label | PSSP predicts | GT(t+1s) actual | PSSP acc | persist acc |
|---|---|---|---|---|
| Left | 41.6% | 25.4% | 0.587 | 0.459 |
| Right | 32.3% | 24.1% | 0.507 | 0.440 |
| Teleoperator | 20.2% | 32.3% | 0.342 | 0.583 |
| Others | 6.0% | 18.2% | 0.115 | 0.349 |
| **L/R total** | **73.9%** | 49.5% | | |
