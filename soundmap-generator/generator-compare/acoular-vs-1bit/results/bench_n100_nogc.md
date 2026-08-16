# bench_n100 -- N100-sim (4x E-core, 3.4GHz locked)

- host CPU: `Intel(R) Core(TM) Ultra 9 185H`
- emulated cores: `[12, 13, 14, 15]`, thread cap 4, clock lock **ON** (target 3.4 GHz)
- measured clock under load: 3373 MHz mean (3200-3455, n=603)
- bag `G11_game4_DoA`, 300 ticks from t=40s, budget 250 ms/tick (4 Hz)
- cyclic GC during the timed loop: **off**
- shared labeling pipeline: 11.4 ms/tick (5% of the budget, same for both generators)

| generator | wall mean | p50 | p95 | max | CPU ms/map | cores busy | +label = tick | budget used | over budget | max rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| acoular | 162.2 | 161.8 | 168.4 | 177.3 | 474.3 | 2.92 | 173.6 ms | 69% | 0/300 | 5.8 Hz |
| 1bit | 25.5 | 25.5 | 25.8 | 26.3 | 25.7 | 1.01 | 36.9 ms | 15% | 0/300 | 27.1 Hz |

4-label agreement over these ticks: 269/300 (89.7%) -- see `analyze_agreement.py` for the breakdown.
