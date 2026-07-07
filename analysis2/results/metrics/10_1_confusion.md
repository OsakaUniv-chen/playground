## 10.1 Same-observation agreement / confusion matrices
Pooled over all bags. 4-label pairs = 4×4; TEL/RAND pairs restricted to L/R ticks = 2×2. GT = truth (window→t); DoA = mode decision (window→t−0.2).

### PSSP_p10(t) vs GT(t+1s) — +1s prediction accuracy
| A\B | Left | Right | Teleoperator | Others |
|---|---|---|---|---|
| Left | 5909 | 2934 | 4578 | 3051 |
| Right | 2540 | 4829 | 3160 | 2241 |
| Teleoperator | 1202 | 1324 | 4384 | 1071 |
| Others | 422 | 436 | 680 | 825 |

agreement = **40.3%**  (n = 39586)

### GT(t) vs GT(t+1s) — 1s persistence base rate
| A\B | Left | Right | Teleoperator | Others |
|---|---|---|---|---|
| Left | 4619 | 1867 | 1930 | 1645 |
| Right | 1894 | 4193 | 1842 | 1615 |
| Teleoperator | 1954 | 1948 | 7460 | 1420 |
| Others | 1606 | 1515 | 1570 | 2508 |

agreement = **47.4%**  (n = 39586)

### DoA(t) vs GT(t) — DoA 0.2s delay penalty
| A\B | Left | Right | Teleoperator | Others |
|---|---|---|---|---|
| Left | 7792 | 731 | 592 | 1051 |
| Right | 733 | 7449 | 569 | 797 |
| Teleoperator | 569 | 572 | 11227 | 422 |
| Others | 1027 | 823 | 464 | 4976 |

agreement = **79.0%**  (n = 39794)

### TEL(t) vs DoA(t) (2×2)
| A\B | L | R |
|---|---|---|
| L | 4289 | 2831 |
| R | 5866 | 6702 |

agreement = **55.8%**  (n = 19688)

### TEL(t) vs PSSP_p10(t) (2×2)
| A\B | L | R |
|---|---|---|
| L | 6514 | 3883 |
| R | 10015 | 8916 |

agreement = **52.6%**  (n = 29328)

### TEL(t) vs GT(t) (2×2)
| A\B | L | R |
|---|---|---|
| L | 4203 | 2859 |
| R | 5905 | 6702 |

agreement = **55.4%**  (n = 19669)

### PSSP_p10(t) vs RAND(t) (2×2)
| A\B | L | R |
|---|---|---|
| L | 8270 | 8284 |
| R | 6425 | 6391 |

agreement = **49.9%**  (n = 29370)

### RAND(t) vs GT(t) (2×2)
| A\B | L | R |
|---|---|---|
| L | 5025 | 4757 |
| R | 5096 | 4818 |

agreement = **50.0%**  (n = 19696)

### TEL(t) vs GT(t+lag) — human tracking-lag curve (2×2 agreement)
| lag(s) | -1 | -0.5 | 0 | 0.5 | 1 | 1.5 | 2 | 2.5 | 3 | 5 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| agree% | 58.9 | 57.5 | 55.4 | 54.3 | 53.5 | 52.8 | 51.9 | 51.2 | 51.2 | 50.9 | 50.2 |

### PSSP_p10 vs GT(t+1s) — per-label precision / recall
| label | precision | recall |
|---|---|---|
| Left | 0.359 | 0.587 |
| Right | 0.378 | 0.507 |
| Teleoperator | 0.549 | 0.342 |
| Others | 0.349 | 0.115 |
