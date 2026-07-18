# PhD Thesis Prospectus

> The dissertation direction that Paper 6 serves as the backbone of. Compact by design.
> Derived from `paper6-overview.md` (PhD Central Line, chosen 2026-06-28).

## Working Title
**Proactive and Anticipatory Social Robots for Real-World Human–Robot Interaction**

*Alternatives:*
- Toward Proactive and Anticipatory Social Robots for Real-World Human–Robot Interaction
- From Reaction to Anticipation: Proactive and Anticipatory Social Robots for Real-World HRI

## Field Keywords
Human–Robot Interaction (HRI) · Social Robotics · Proactive / Anticipatory Behavior · Social Attention & Gaze · Learning from Demonstration (IRL / RL) · Multimodal Acoustic-Visual Perception · Predictive Sound Source Positioning (PSSP) · Multi-Party Interaction · Real-World / In-the-Wild Field Study

## Key Problem (one sentence)
How can a social robot **act proactively and anticipatorily** — initiating engagement and allocating attention *before / without waiting for* explicit triggers — to participate effectively in real-world multi-party human interaction, and **when does such anticipation actually pay off**?

## Key Problem (one paragraph)
Most deployed social robots are **reactive**: they respond only after a stimulus has occurred and they treat people undifferentiated, which makes their behavior feel delayed, mechanical, and socially disconnected — especially in real, multi-party settings where attention shifts quickly and where *whom* the robot engages matters. This dissertation argues that effective situated HRI instead requires **proactive and anticipatory** social behavior, and pursues it along two complementary routes: **learning proactive action** from human demonstration so a robot can autonomously initiate well-timed social acts in the field (and even improve beyond its human teacher), and **anticipatory perception** that predicts near-future sound-source activity so a robot can orient and allocate attention ahead of events rather than chasing them. A controlled study, however, exposes a crucial boundary: anticipatory attention that is *functionally correct* (it does select the right target) is **not necessarily perceptually rewarded** — in a low-stakes setting without task consequences, low-level motion dynamics dominate human judgments, not the gaze target. The dissertation therefore reframes the central question from "does social attention feel better?" to "**does anticipatory, proactive social behavior produce measurable benefit when the attention target has functional consequence?**", and answers it by integrating both routes into a single autonomous robot and validating it in a **real-world field deployment** where being attended to changes the task outcome.

## Thesis Structure & the Role of Each Study

| Ch. | Theme | Study | Role in the dissertation |
|----|-------|-------|--------------------------|
| 1 | Introduction | — | Frame the *reactive* limitation; state the anticipatory-&-proactive thesis line and the two routes. |
| 2 | **Proactive action, learned in the wild** | **P1** (IRL mall receptionist) | Robots can *autonomously initiate* well-timed social actions in a real field setting, learned from humans — no hand-crafted rules. |
| 2 | | **P2** (IRL→RL) | That proactivity can *self-improve* online in the field, surpassing the human expert — proactivity is not just imitation. |
| 3 | **Anticipatory perception** | **P3** (PSSP foundation) | Technical basis for anticipation: predict *future* sound-source maps from acoustic-visual fusion, validated in the wild. |
| 4 | **Anticipation in behavior** | **P4** (pilot) | Anticipation enters embodied behavior: PSSP drives anticipatory head motion in live HRI (proof of concept). |
| 4 | | **P5** (controlled study) | A *scoped negative* — anticipatory targeting is functionally correct but not perceptually rewarded; **motion dynamics dominate perception**. Establishes the boundary condition and the validated measurement methodology. |
| 5 | **Integration & field validation** | **P6** (capstone) | Integrate proactive action + anticipatory attention into one autonomous robot; deploy in a real reception task where the attention target is *functionally consequential*; measure payoff by **task outcome**. Answers the question P5 raised. |
| 6 | General discussion & conclusion | — | When anticipation pays off (boundary conditions); from reaction to anticipation as a design principle for situated social robots. |

## The arc in one line
**P1–P2** show a robot can *act* proactively in the real world; **P3–P5** show it can *attend* anticipatorily but reveal that correctness alone is not perceived; **P6** closes the loop by testing the integrated behavior where attention has real task consequence — turning a negative finding into the motivation for the capstone.
