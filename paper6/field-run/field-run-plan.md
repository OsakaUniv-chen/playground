# Paper 6 — Field-Run Plan (working)

> How the 2-condition experiment is actually run. Numbers from the assumed site.

## Field parameters (assumed)
- **Site:** shopping mall (exact site TBD by supervisor).
- **Duration:** one weekend, 2 days × 6 h (10:00–16:00) = **12 h**.
- **Flow:** ~**10 interaction groups / hour** → **~120 interaction episodes total**.
- **Group size:** 1–5 people per episode.

## Sample estimate
- Total episodes ≈ **120**.
- **Multi-party episodes (≥2, needed for conflict metrics):** ~60–80 (group-size dependent) → **~36–48 per condition** (between-session split).
- **Acknowledgement-timeliness** scales with waiting visitors (a 3-person group → 2 data points), so effective N for timeliness is larger.
- ≈ **3× Paper 5's groups**; adequate but not lavish → gain power at episode/event level (below).

## Subject design — between-session
Field visitors interact **once**, so conditions cannot be within-visitor. Condition is assigned by **time block** (between-session), like P1–P2's day-wise switching.

## Condition assignment & counterbalancing
- **Block = 1 hour**; 6 blocks/day, **12 total**; ~10 episodes/block.
- **Counterbalance time-of-day** (the main confound):
  - Day 1: C1 C2 C1 C2 C1 C2
  - Day 2: C2 C1 C2 C1 C2 C1 (mirror)
  - → each condition = 6 blocks spanning all times of day across both Sat & Sun; time-of-day and weekday balanced.
- Short **buffer at each switch** (reset system; no episode spans a block boundary).

## Analysis
- Go to **event level** to gain power (don't analyze only per-episode means — that's part of what weakened P5):
  - **Conflict:** per-episode count/rate → mixed model (Poisson / negative-binomial), **block + episode random effects**, time-of-day + group-size covariates.
  - **Acknowledgement timeliness:** per-waiting-visitor latency → mixed model, episode random effect.
- **Confound control (from P5):** confirm C1/C2 low-level motion dynamics are similar (κ, switch rate, smoothness) so any effect is *timing*, not dynamics.

## Power vs Paper 5
P5 = 13 groups, incomplete within-subject design, point estimates ≈ 0 hard to interpret. P6 = ~40 multi-party episodes/condition + event-level modeling + a **directional causal hypothesis** (prevention > remediation). Materially better positioned; effect size still unknown.

## Risks / TODO
- **Group-size distribution unknown** → calibrate in Day-1 first block; if single-person episodes dominate, multi-party N drops (mitigation: extend hours / second weekend if needed).
- **Engagement is natural** (no forced concurrency) → preserves ecological validity but N is not controllable.
- **Operationalize** conflict-event & acknowledgement detection (next step).
- Confirm **site, ethics, logging rig, teleoperator shifts**, and dialogue scope.
