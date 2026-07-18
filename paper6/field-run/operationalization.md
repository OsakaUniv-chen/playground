# Paper 6 — Operationalization (working)

> Precise definitions for the manipulation and the metrics. Most are auto-loggable from the
> P3–P5 hardware; the rest are post-hoc video annotation.

## C1/C2 trigger — resolved (breaks the circularity)
Same observable event; the conditions differ **only in timing**. Conflict is downstream and is **never** a trigger.
- **C1 Reactive:** acknowledge a waiting visitor **after** their **speech onset** (audio-detected).
- **C2 Anticipatory:** PSSP predicts the onset ~1–2 s ahead → acknowledge **before** it.
- The **physical acknowledgement action is identical** in both conditions → controls the P5 motion-dynamics confound.

## Acknowledgement (robot action — auto-logged)
- **Definition:** orient head to the waiting visitor + a hand gesture (nod / "one moment"), held ≥ threshold.
- **Source:** robot log (action + target + timestamp). Fully automatic.

## Primary metric 1 — Conflict-event rate (the outcome)
Per multi-party episode, count waiting-visitor conflict events, normalized by episode duration / #waiting:
- **Interruption** — waiting visitor speaks over the ongoing dialogue (audio VAD + source zone). *semi-auto.*
- **Repeat** — waiting visitor re-states an already-made request. *video/ASR annotation.*
- **Line-cutting** — waiting visitor physically encroaches the service spot (vision position). *auto.*
- *(secondary)* **Visible impatience** — checking time, sighing, waving, repeated glancing. *annotation.*

Aggregate: event count/rate per episode → mixed model (block + episode random effects; time-of-day, group-size covariates).

## Primary metric 2 — Acknowledgement timeliness
- Per waiting visitor: latency from **start-of-waiting** (enters zone while robot is busy) to **first acknowledgement**.
- Auto (vision arrival + log ack time).

## Manipulation check (NOT an outcome — keep separate)
**Prevention rate** = fraction of waiting visitors acknowledged *before* their own speech onset. By construction C2 should be high and C1 ≈ 0 — this only confirms the manipulation worked. The **outcome** is whether that timing reduces conflict (metric 1).

## Action repertoire (policy output; reuse P1 + add acknowledge)
GREET (new approacher) · SERVE/TRACK (current interlocutor; teleop speaks) · **ACKNOWLEDGE** (head + gesture to a waiter; timing per condition) · ONE-MOMENT gesture · WAIT/IDLE.

## Data-collection rig (reuse P3–P5 hardware)
- **Robot log (rosbag):** actions, targets, head/hand commands, timestamps — auto.
- **16-ch mic array:** speech onset, source zone (C1 trigger + interruption) — auto.
- **Fisheye ego + overhead camera:** visitor position, arrival, group size, line-cutting — auto + annotation.
- **Video** for post-hoc annotation: repeat, impatience.
