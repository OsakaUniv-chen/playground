## 10.3 Per-policy output-label distribution + speech statistics (all bags pooled)

### Output-label distribution over all bags
GT = truth / acoustic scene; DoA/PSSP = the label each policy decided to look at. Same population (all ticks) for every policy.

| label | GT | DoA | PSSP |
|---|---|---|---|
| Left | 25.4% | 25.5% | 41.6% |
| Right | 24.1% | 24.0% | 32.2% |
| Teleoperator | 32.3% | 32.1% | 20.2% |
| Others | 18.2% | 18.3% | 6.0% |
| **n** | 39794 | 39794 | 39794 |

L/R-only policies: **Tele** left 35.3% / right 64.7% (n=39721), **Random** left 49.8% / right 50.2% (n=39794).

### Speaking-turn duration per label (run length × 0.25 s)
Each run of a label = one continuous turn as the dominant acoustic source; i.e. how long they keep speaking each time they start.
| label | mean ± SD (s) | median(s) | p90(s) | n turns |
|---|---|---|---|---|
| Left | 0.92 ± 0.94 | 0.50 | 2.00 | 2755 |
| Right | 0.94 ± 0.85 | 0.75 | 2.00 | 2549 |
| Teleoperator | 1.66 ± 1.51 | 1.25 | 3.50 | 1930 |
| Others | 0.69 ± 0.72 | 0.50 | 1.50 | 2619 |

**Left+Right (facing participants) turn duration: 0.93 ± 0.90 s** (median 0.75, p90 2.00, n=5304)
