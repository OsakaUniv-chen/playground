# Paper 5 — Anticipatory Head Motion in Word Wolf (Controlled Study)

| | |
|---|---|
| **Title** | (working) Predictive Sound-Source Positioning for Socially Responsive Robot Head Orientation in Multi-Party Interaction |
| **Authors** | Zhichao Chen et al. |
| **Venue / Year** | TBD — was targeting ROBIO 2026; submission undecided (see below) |
| **Field** | A — Anticipatory perception (perceive/attend) |
| **Status** | **Experiment complete (13 groups, 2026-06). Primary hypothesis not supported; results negative. Whether to submit a paper or fold into the thesis is undecided.** Full digest: [paper5-experiment-results.md](paper5-experiment-results.md) |
| **PhD role** | The rigorous evaluation of anticipatory head motion in multi-party HRI; supplies Paper 6's validation methodology. |

**Abstract (draft).** Social robots communicate through speech and through visible orientation of the body, head, and gaze. In multi-party interaction, these movements help people infer attention, turn-taking, participation roles, and social awareness. Robots that orient only toward current sound sources may appear delayed when conversational attention shifts quickly. This paper investigates whether predicted future sound-source information can support more socially readable robot head movement. We use Predictive Sound Source Positioning to convert acoustic-visual forecasts into anticipatory orientation targets for a teleoperated robot avatar. The robot's speech comes from a remote human participant, while its head movement is assigned by predictive, human-controlled, reactive, or non-conversational strategies within a shared target-selection framework. The study evaluates these strategies in live multi-party interaction through subjective robot perception, interaction experience, teleoperation-like impressions, and behavior logs.

## 1. Background
Robot head/gaze orientation signals attention, addressee, and conversational role. Reactive following of the *current* speaker lags rapid attention shifts in multi-party talk, appearing delayed and mechanical.

## 2. Objective
Test, in a larger and controlled study, whether **PSSP-based anticipatory** head orientation is more socially readable than reactive following — isolating *target-selection timing* from low-level motion.

## 3. Task design
Three-person **Word Wolf** groups; robot **Boxie** is a **teleoperated avatar** (speech from a remote human, head varied by mode). Six-game sequence: G1 **Onsite** + G2 **Video** (context), G3–6 four counterbalanced modes — **Tele** (operator yaw, upper-bound reference), **PSSP** (+1.0 s predicted map, primary), **DoA** (current map, reactive baseline), **Random** (Poisson, non-conversational). A **shared target-selection framework** has all modes emit the same Left/Right participant target into one low-level controller.

## 4. Method
PSSP (from Paper 3, retrained on new dataset scenes) and DoA share a left/right region-scoring rule over sound maps — PSSP scores the predicted future map, DoA the current map. Tele maps operator head-yaw to a target; Random uses Poisson switching. Identical smoothing/controller downstream keeps low-level motion comparable.

## 5. Evaluation method
Planned **12 groups / 36 participants**. In-person measures: **GQS-J** (Godspeed), **GEQ-Core**, teleoperation-likeness/confidence; **robot logs** (switch rate, dwell time, target distribution, smoothness); wolf-guess (exploratory). Plus a **crowdsourced third-person evaluation** (≈96 videos, ~288 workers) analyzed with mixed-effects models (LMM for subscale means, CLMM for ordinal items; group/set/worker random effects). Hypothesis: PSSP > DoA, Random; Tele = human reference.

## 6. Result
**Complete — 13 groups / 39 participants (2026-06-15–19). The primary hypothesis (PSSP > DoA, Random) is not supported; the result did not go well.** Full digest: [paper5-experiment-results.md](paper5-experiment-results.md). Key points:
- **Subjective: no PSSP advantage.** GQS 4/5 and GEQ 5/5 subscales show no group difference; wolf-guess at chance. Only GQS PerceivedSafety is significant — driven by **DoA being low** (over-switching), not PSSP being high. PSSP−Random / PSSP−Tele point estimates ≈ 0.
- **Teleoperation perception (PTL) uninformative:** even true-human Tele is at chance, so conditions cannot be ranked (metric insensitivity / acquaintance bias / large individual variance).
- **Behavior (the one solid finding):** PSSP genuinely tracks the speaker above chance (κ=0.34) while Random does not (κ=0.04), yet their motion dynamics are near-identical (1−JS=0.958) **and their subjective ratings are equal**.
- **Mechanistic takeaway (negative but clear):** semantically correct social gaze (*whom* to look at) is not reflected in perception; evaluation is dominated by **motion dynamics** (*how* it moves). Interviews corroborate this dissociation.

## 7. Conclusion
The original "anticipatory targeting is more socially readable" thesis is **not** borne out: correct target selection (PSSP) did not improve participant perception over a non-conversational baseline. The durable finding is a **behavior↔perception dissociation** — motion dynamics, not gaze target, drive social judgments in this setting. **Publication decision is open:** options are (A) a perception-mechanism short paper / workshop / LBR framing PSSP as a "correctly-targets-but-unperceived probe", or (B) folding the negative result + measurement method into the thesis. See the digest's "Options" section.

## → Relevance to Paper 6
Supplies Paper 6's **evaluation machinery**: the teleoperated-avatar paradigm, the shared target-selection framework, the subjective + behavioral + third-person measurement stack, and the mixed-effects analysis. This machinery is now **validated and reusable independent of Paper 5's negative outcome**. Paper 6 reuses it to validate the fused perception+policy robot in a real-world field run.

**Cautionary input for Paper 6's field run:** here, *correct* anticipatory targeting did not translate into perceived improvement — **motion dynamics dominated social perception**. Paper 6 should not assume "perceives correctly ⇒ perceived as better"; low-level motion quality must be managed and measured in the field evaluation.

### Supporting assets (captured here — no external folder needed)
- **Word Wolf game + crowdworker questionnaire forms** — deployed game and web survey.
- **Crowdsourcing study** — third-person video-evaluation protocol and analysis plan (above).
- **Result plots & analysis** — player-study figures and behavior logs analyzed (GQS/GEQ boxplots, κ/switch-rate, JS similarity). Distilled, self-contained version: [paper5-experiment-results.md](paper5-experiment-results.md); the GQS/GEQ boxplot figure is in-repo at [paper5-figures/](paper5-figures/) (`post_survey.png`/`.pdf`). Source analysis scripts/rosbag remain in the external `result-plot/` working folder.
- **JSAI 2026 presentation** — Japanese talk disseminating the anticipatory-head-dynamics line.
