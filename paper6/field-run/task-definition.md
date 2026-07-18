# Paper 6 — Task Definition (working)

> The concrete Paper 6 field task. Decisions locked so far + next-level TODOs.
> Scenario = reception (locked) · service = information desk · flow = multi-party simultaneous · visitors = goal-directed (they wait).

## One-line task
A **stationary** social robot acts as a **multi-party information reception desk**: it **proactively** greets approaching visitors and **anticipatorily** allocates attention to whoever needs it next, while a **teleoperator supplies the spoken answers**. Autonomy owns *whom to attend to* and *when to initiate / acknowledge*; teleop owns *what to say*.

## Setting
- **Robot:** stationary (child-like humanoid; Boxie fallback) — head + hand motion + teleop speech.
- **Place:** a real venue with visitor flow (info desk for an event / exhibition / building lobby; exact site provided by supervisor at the final stage).
- **Visitors:** **goal-directed — they come on purpose and will wait** (they do not casually leave). **Multiple may be present at once.**
- **Service:** visitors ask for information / directions; the teleoperator answers through the robot.

## Core structure — serial dialogue + parallel attention
The teleop voice serves **one visitor at a time (serial)**, but head + hands give **parallel, non-verbal attention management**. So the robot must, *during* a primary dialogue, autonomously and **anticipatorily** allocate secondary attention to waiting visitors — turn-to-acknowledge ("I see you"), gesture "one moment", pre-orient to whoever is about to ask next.

**This is where anticipatory motion finally has functional consequence.** In Paper 5, looking at the right person had no consequence (so no measurable effect). Here, timely acknowledgement of the right waiting visitor changes their **behavior and the group's order**.

## Why attention is consequential (visitors wait → value is *order*, not retention)
Because visitors wait, the payoff is **not** reduced drop-off; it is **concurrency management**. Ignored-but-waiting visitors produce observable **conflict behaviors** — interrupting the ongoing service, repeating their question, cutting in line, visible confusion. Proactive + anticipatory acknowledgement **reduces these conflict events** and keeps service orderly.

## Autonomy boundary (teleop-avatar paradigm, inherited from P5)
- **Autonomous:** attention allocation (PSSP anticipatory + vision), engagement/acknowledgement timing (proactive policy: greet / acknowledge / "one moment" / TRACK / WAIT), queue management.
- **Teleop:** the actual answer content (dialogue).

## Role of each prior asset
- **P1–P2 policy (vision→action):** decides *when* to initiate/greet/acknowledge from visual visitor state. Reused; small adaptation for multi-party + an "acknowledge/queue" action.
- **PSSP (P3–P5):** the anticipatory cue — predicts *who is about to speak / engage next* among multiple visitors, so the robot pre-orients/acknowledges ahead of time (its native acoustic-temporal regime).
- **Child-humanoid hands:** beckoning / "one moment" gestures — core to acknowledgement & queue management, richer than head-only.

## Outcome metrics (direction locked: concurrency management / order)
All auto-loggable from robot logs + overhead/ego video; **none rely on the subjective scales that failed in P5**.
- **Primary:** concurrent **conflict-event rate** — interruptions / overlapping speech / repeated questions / line-cutting.
- **Primary:** **acknowledgement timeliness** — latency from a waiting visitor's arrival to first acknowledgement.
- **Composite:** throughput (served / unit time); service-order fairness & correctness.
- **Secondary + confound control:** P5 stack — GQS-J / impressions for served visitors; behavior logs (κ gaze-on-target, switch rate, motion smoothness) to control the P5 motion-dynamics confound.

## To confirm next
1. **Operationalize the metrics** — concrete detection of an "acknowledgement", a "conflict event", "served".
2. **Action repertoire** — finalize the discrete actions (greet / acknowledge / one-moment / track / wait / …) for the policy.
3. **Integration architecture** A/B/C (separate step).
4. **Condition design** — integrated (proactive+PSSP) vs reactive baseline vs (optional) teleop reference.
