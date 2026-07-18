# Paper 6 — Integration Concept (working draft)

> Status: **discussion-stage candidate**, not a locked plan. Captures the interaction-lifecycle
> integration idea and how Paper 5's negative result reframes the motivation. Architecture (A/B/C)
> and final scenario remain open.

## Core idea: the two fields are two phases of one interaction

A real reception interaction has a timeline, and the PhD has solved it phase by phase:

```
 passerby            visitor             engaged group              departure
────●───────────────────●───────────────────●───────────────────────────●────▶
    │   PHASE 1: ENGAGE  │  PHASE 2: SERVE   │       PHASE 3: CLOSE
    │  notice, attract,  │  attend among     │   thank, release,
    │  greet at the      │  speakers while   │   reset for next
    │  right moment      │  conversing       │   visitor
    │                    │                   │
    │  Field B (P1–P2)   │  Field A (P3–P5)  │   Field B (P1 actions)
    │  learned policy:   │  PSSP anticipatory│   THANKS/AWAY
    │  image→action      │  attention +      │
    │  HELLO/TRACK/WAIT  │  teleop dialogue  │
```

**Integration claim:** neither field alone is a working receptionist. P1–2 could *start* interactions
but had no attention management once engaged; P3–5 could *sustain* multi-party attention but assumed
someone was already there. Paper 6 is the first robot to span the **full lifecycle** in the wild.

Why this framing is strong:
- The "vision already sees approach — why predict sound?" objection disappears: PSSP operates **inside
  conversation** (Phase 2), its training domain — not for approach detection (Phase 1, owned by the vision policy).
- PSSP domain-shift shrinks: Phase-2 reception (2–3 visitors talking to the robot) resembles a Word Wolf group.
- Per-phase metrics fall out naturally: Phase 1 engagement rate (P1–2 style auto-logged), Phase 2 GQS-J +
  head-behavior logs (P5 stack), end-to-end service completion / visitors-served-per-hour.

## How Paper 5's negative result reframes (not weakens) the motivation

Paper 5 finding (scoped): in a **low-stakes, 2-target, symmetric** conversational game where the gaze target
has **no functional consequence**, *correct* anticipatory targeting (PSSP, κ=0.34) produced **no perceptual
improvement** over baselines — **motion dynamics dominated social perception**. Crucially this is a claim about
*perception in that setting*, NOT "PSSP targeting is useless" (PSSP is functionally correct; Random's parity is
an artifact of the 2-target symmetric geometry and a no-consequence task).

This sets up Paper 6 rather than undermining it:
- **The open question Paper 5 raises:** does anticipatory attention matter when the gaze target **has functional
  consequence**? Reception is exactly that regime — *who* the robot attends to determines *who gets served next*.
  Being attended-to is no longer cosmetic; it changes outcomes.
- **Two consequences to design for:**
  1. **Targeting now has task value** → measure it on **task outcomes** (served/not, wait fairness, throughput),
     not only on impression scales that proved insensitive in Paper 5.
  2. **Motion dynamics dominate impressions** → low-level motion quality must be **controlled and measured** in the
     field; do not assume "perceives correctly ⇒ perceived as better."

## Candidate research question (draft)

> *Can a robot autonomously manage the perceptual/engagement front-end of reception — anticipating, attending to,
> and initiating with the right visitor at the right time in real multi-party pedestrian traffic — and does
> anticipatory (PSSP) attention yield measurable benefit when the gaze target is functionally consequential
> (who gets served), unlike the no-consequence game setting of Paper 5?*

## Open decisions (carried into discussion)
- **Integration architecture** — A (modular: vision policy + PSSP attention channel + teleop dialogue; no policy
  retrain), B (fused: retrain policy on image+acoustic, recollect data — highest risk before deadline),
  C (feature injection: PSSP output as a compact policy state feature). **Resolved 2026-06-28 → A (modular).** In the P6 design PSSP is an independent attention-timing channel that does not enter the policy's action decision, so the policy needs no sound-map retrain; B/C have no role here and move to thesis future-work.
- **Platform** — design around the **child-like humanoid** (hands + head + teleop speech); Boxie as committed fallback.
  Needs PSSP sensing-module mounting check.
- **Asset note** — all P1–2 (IRL/RL) and PSSP code reusable; but the IRL/RL dataset is image–action only (no sound
  map). Adding acoustic input to the policy (B/C) requires recollecting a small dataset + retraining.
- **Outcome metric** — per-phase (engagement rate / service completion); make the *targeting-consequential* metric primary.
- **Conditions** — cleanest field ablation targets Phase 2 only: policy + **PSSP attention** vs policy + **reactive
  attention**, Phase-1 policy constant; teleop reference optional if operator hours allow.
- **Venue** — ICRA-level conference paper.
