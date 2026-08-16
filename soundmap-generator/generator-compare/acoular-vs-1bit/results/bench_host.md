# bench_n100 -- HOST (22 threads, unconstrained)

- host CPU: `Intel(R) Core(TM) Ultra 9 185H`
- measured clock under load: 4091 MHz mean (400-4800, n=623)
- bag `G11_game4_DoA`, 200 ticks from t=40s, budget 250 ms/tick (4 Hz)
- cyclic GC during the timed loop: **on**
- shared labeling pipeline: 6.3 ms/tick (3% of the budget, same for both generators)

| generator | wall mean | p50 | p95 | max | CPU ms/map | cores busy | +label = tick | budget used | over budget | max rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| acoular | 287.2 | 104.2 | 1651.0 | 3459.0 | 5290.5 | 18.42 | 293.5 ms | 117% | 37/200 | 3.4 Hz |
| 1bit | 14.4 | 13.7 | 16.2 | 39.3 | 18.1 | 1.26 | 20.7 ms | 8% | 0/200 | 48.4 Hz |

4-label agreement over these ticks: 174/200 (87.0%) -- see `analyze_agreement.py` for the breakdown.
