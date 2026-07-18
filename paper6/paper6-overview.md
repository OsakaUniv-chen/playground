# Paper 6 — Overview

> Control document for **Paper 6** (the PhD capstone). Keep compact.
> Dissertation-level direction (title, key problem, chapter map, each paper's role) lives in
> [thesis-prospectus.md](thesis-prospectus.md) — this file is Paper 6 only.
> **Consolidated, self-contained write-up (Japanese, supervisor-facing): [paper6-proposal.md](paper6-proposal.md)**.

## What Paper 6 Is
The **capstone paper** of the PhD — its job is **integration, not invention**.
- **No new algorithm.** The contribution is unifying all prior work into one coherent study.
- **Leverages all of Papers 1–5**; applied to a **real-world HRI scenario with a field run**.
- Doubles as the **thesis backbone** (final PhD year). Venue: **ICRA-level conference**.

## Place in the Dissertation
Thesis line: *proactive & anticipatory social robots* (vs reactive). Paper 6 is **Chapter 5 — Integration & field validation**: it carries the line into a real task where the attention/engagement target has **functional consequence**, answering the open question Paper 5 raised. (Prior chapters: P1–P2 proactive action; P3–P5 anticipatory attention. See [thesis-prospectus.md](thesis-prospectus.md) and [publications/publications-summary.md](publications/publications-summary.md).)

## Paper 6 Positioning
Integrate proactive action (P1–P2) + anticipatory attention (P3–P5) into **one autonomous robot**, deployed where *whom the robot attends to / when it initiates* changes the outcome (e.g. who gets served).
- **The delta:** move the value question of social attention from the **perception level** (where P5 found no payoff — motion dynamics dominated) to the **task level** — does proactive & anticipatory behavior pay off when the target is *functionally consequential*?
- **No "first"/novelty claims.** Contribution rests on integration + real-world field evidence + boundary conditions.
- **Primary metric = task outcome** (auto-logged); subjective scales secondary (P5 showed they're unreliable in this regime).

### Candidate research question (draft)
> In a real multi-party setting where *whom the robot attends to / when it initiates* changes the task outcome, does proactive & anticipatory social behavior produce a measurable task benefit — unlike the no-consequence game of Paper 5?

### Scenario requirements
A fitting scenario must: be **real-world, multi-party** (visitor/pedestrian flow); make the **attention target functionally consequential** (who is served); yield an **auto-loggable task outcome**; **reuse** the P1–2 policy + PSSP; be **feasible to prepare by September**.
→ **Scenario locked: reception-type task** (the only viable testbed satisfying all of the above). Exact site provided by supervisor at the final stage.

## Status

**Decided**
- PhD line = proactive & anticipatory (A); title = *Proactive and Anticipatory Social Robots for Real-World HRI*.
- Primary metric = task outcome (conflict-event rate + acknowledgement timeliness); subjective secondary. Venue = ICRA-level. No "first" claims.
- Scenario = multi-party **information reception desk**; goal-directed visitors who wait; value = concurrency management.
- Conditions = **2** (single variable = timing): C1 Reactive vs C2 Full/anticipatory. Causal claim: prevention > remediation.
- Architecture = **A (modular)**: PSSP = independent attention-timing channel; policy reused, no retrain.
- Platform leaning = child-like humanoid (Boxie fallback).

**Open (Paper 6 work, in dependency order)**
1. **Task definition** — *mostly defined*: multi-party information desk; goal-directed visitors who wait; **serial dialogue (teleop) + parallel anticipatory attention (autonomous head/hand)**; value = concurrency management/order; primary metrics = conflict-event rate + acknowledgement timeliness. See [field-run/task-definition.md](field-run/task-definition.md). Remaining: operationalize metrics, action repertoire.
2. **Condition design** — *defined*: 2 conditions, single variable = attention timing. **C1 Reactive** (acknowledge after conflict) vs **C2 Full/anticipatory** (PSSP acknowledges before conflict). Causal claim: prevention > remediation. See [field-run/condition-design.md](field-run/condition-design.md). Remaining: subject design, counterbalancing, sample.
3. **Platform** — confirm child-like humanoid + PSSP sensing-module mounting feasibility.
4. **Field-run plan** — *drafted*: mall, 2-day weekend, ~120 episodes, ~40 multi-party/condition; **between-session**, hourly blocks, mirror counterbalance; event-level mixed models. See [field-run/field-run-plan.md](field-run/field-run-plan.md). Remaining: site, ethics, logging rig.
5. **Operationalize** — *defined*: C1=ack after speech onset / C2=PSSP ack before onset (conflict = downstream, circularity broken); conflict-event & acknowledgement detection; action repertoire; rig reuses P3–P5 hardware. See [field-run/operationalization.md](field-run/operationalization.md).

## Paper 5 — cautionary input
P5 (complete, negative): correct anticipatory targeting did **not** improve perception over baselines — **motion dynamics, not gaze target, dominated**. For Paper 6: the **evaluation machinery is validated and reusable**, but do not assume "perceives correctly ⇒ perceived as better"; manage and measure low-level motion quality. The P5 null is scoped to a *no-consequence* task — it must **not** be carried over as "random suffices" into the consequential field setting. Details: [publications/paper5-experiment-results.md](publications/paper5-experiment-results.md).

## Folder Structure
```
paper6/
├── thesis-prospectus.md          # dissertation-level direction
├── paper6-overview.md            # this control document (Paper 6 only)
├── publications/                 # source PDFs + per-paper files + summary index
├── integration/                  # integration-concept.md (lifecycle framing, A/B/C options)
├── field-run/   (planned)        # scenario design, protocol, logistics
└── writing/     (planned)        # manuscript drafts
```
