# bench_n100 -- N100-sim (4x E-core, 3.4GHz locked)

- host CPU: `Intel(R) Core(TM) Ultra 9 185H`
- emulated cores: `[12, 13, 14, 15]`, thread cap 4, clock lock **ON** (target 3.4 GHz)
- measured clock under load: 3379 MHz mean (3183-3433, n=602)
- bag `G11_game4_DoA`, 300 ticks from t=40s, budget 250 ms/tick (4 Hz)
- cyclic GC during the timed loop: **on**
- shared labeling pipeline: 10.7 ms/tick (4% of the budget, same for both generators)

| generator | wall mean | p50 | p95 | max | CPU ms/map | cores busy | +label = tick | budget used | over budget | max rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| acoular | 161.8 | 156.1 | 163.8 | 319.0 | 472.7 | 2.92 | 172.6 ms | 69% | 11/300 | 5.8 Hz |
| 1bit | 25.8 | 25.8 | 26.5 | 26.8 | 26.1 | 1.01 | 36.6 ms | 15% | 0/300 | 27.3 Hz |

4-label agreement over these ticks: 269/300 (89.7%) -- see `analyze_agreement.py` for the breakdown.
