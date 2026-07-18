# Prior Publications — Index

Compact map of all prior work for Paper 6. Each paper has its own file (background → objective → task design → method → evaluation → result → conclusion → relevance to Paper 6).

The PhD spans **two fields**; Paper 6 unifies them:
- **Field B — Policy learning (decide/act):** *what* the robot does and *when*, learned from data, field-deployed in the real world.
- **Field A — Anticipatory perception (perceive/attend):** *where* to attend, by predicting near-future sound sources to drive anticipatory head motion.

| # | Field | One line | Status | File |
|---|-------|----------|--------|------|
| 1 | B | IRL android receptionist (mall) matches a trained human operator | Published (RA-L 2022) | [paper1-mall-receptionist-irl.md](paper1-mall-receptionist-irl.md) |
| 2 | B | IRL→RL transition: android keeps learning in the field, outperforms the human expert | Published (RA-L 2023) | [paper2-irl-to-rl.md](paper2-irl-to-rl.md) |
| 3 | A | PSSP: predict future sound-source maps from acoustic-visual fusion (in-the-wild) | Published (IEEE Access 2025) | [paper3-pssp-feasibility.md](paper3-pssp-feasibility.md) |
| 4 | A | PSSP → Boxie anticipatory head motion, pilot Word Wolf study | Published (pilot) | [paper4-anticipatory-head-pilot.md](paper4-anticipatory-head-pilot.md) |
| 5 | A | Controlled multi-party study of anticipatory head motion (Tele/PSSP/DoA/Random) | Experiment done; result negative (hypothesis not supported); submission undecided | [paper5-anticipatory-head-wordwolf.md](paper5-anticipatory-head-wordwolf.md) |

## Integration logic for Paper 6

| Layer | Provided by | Maturity |
|-------|-------------|----------|
| **Perceive** — where to attend (anticipatory) | Field A (P3–5) | lab/game-tested |
| **Decide / Act** — what to do, when (learned) | Field B (P1–2) | real-world field-tested |

Paper 6 = combine both layers into **one autonomous social robot** that perceives anticipatorily and acts via a learned policy, validated with a **real-world field run** — no new algorithm; integration is the contribution.
