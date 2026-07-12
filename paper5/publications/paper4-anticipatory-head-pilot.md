# Paper 4 — Anticipatory Head Dynamics (Pilot)

| | |
|---|---|
| **Title** | Exploring Anticipatory Head Dynamics for Enhanced Robot Perception in Human–Robot Interaction |
| **Authors** | Zhichao Chen, Huthaifa Ahmad, Liliana Villamar Gómez, Yuya Okadome, Hiroshi Ishiguro, Yutaka Nakamura |
| **Venue / Year** | Short paper / pilot study, 2025 _(confirm venue)_ |
| **Field** | A — Anticipatory perception (perceive/attend) |
| **Status** | Published (pilot) |
| **PhD role** | The bridge that turns PSSP prediction into actual robot behavior — anticipatory head orientation in live HRI. |

**Abstract.** Effective human–robot interaction (HRI) requires robots to exhibit smooth, human-like head dynamics that extend beyond simple reactive control. Existing social robots often orient after stimuli occur, leading to delayed and unnatural motion. Anticipatory behavior, such as proactive head movements based on predicted sound activity, may help maintain conversational flow. However, its benefits in HRI remain insufficiently explored. To investigate this, we conduct a pilot study using Boxie, a minimal robot equipped with essential head degrees of freedom. Participants interact under multiple head-motion strategies in a conversational game. Subjective evaluations indicate that predictive head motion may enhance game experience and perceived social qualities.

## 1. Background
Head orientation is a key cue for attention and turn-taking; humans move anticipatorily, reducing uncertainty. Robots typically react *after* stimuli, producing delayed, socially disconnected motion. Predictive head-motion generators are rare.

## 2. Objective
Explore whether **anticipatory (predictive) head motion** improves interaction experience and robot perception versus reactive and non-informative baselines.

## 3. Task design
Participants play the conversational party game **Word Wolf** with **Boxie**, a minimal robot with essential head DoF (yaw & pitch). Speech is teleoperated by a remote participant; head motion varies by condition. Conditions: **C1 Tele**, **C2 DoA** (current acoustic map), **C3 PSSP** (~2 s predicted acoustic maps), **C4 Random** (Poisson policy).

## 4. Method
Boxie integrates a 16-ch mic array + fisheye camera → acoustic maps; the **PSSP model** outputs ~2 s predicted maps to drive anticipatory yaw/pitch. Tele uses operator head-orientation detection; DoA uses current maps; Random uses a Poisson switching policy.

## 5. Evaluation method
Pilot conversation study (six participants); after each game, in-person participants give **subjective evaluations** of game experience and robot perception, plus wolf-guess accuracy and teleoperation-likelihood/confidence.

## 6. Result
Preliminary and small-sample, but directionally encouraging: **PSSP gave the highest wolf-guess accuracy (83.3%)** vs DoA (16.7%), and the **strongest perception of human control** (75% "teleoperated" with highest confidence). Predictive head motion *may* enhance game experience and perceived social qualities.

## 7. Conclusion
An initial look at how anticipatory head movement shapes interaction experience and robot perception; limited sample and design call for a larger, controlled follow-up (→ Paper 5).

## → Relevance to Paper 6
The proof-of-concept that PSSP prediction can drive real robot behavior (Boxie). Defines the four-mode comparison and teleoperated-avatar paradigm that Paper 5 scales up and Paper 6 carries into the field.
