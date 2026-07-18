# Paper 2 — IRL→RL Transition: Outperforming the Human Expert

| | |
|---|---|
| **Title** | Outperformance of Mall-Receptionist Android as Inverse Reinforcement Learning is Transitioned to Reinforcement Learning |
| **Authors** | Zhichao Chen, Yutaka Nakamura, Hiroshi Ishiguro |
| **Venue / Year** | IEEE Robotics and Automation Letters (RA-L), Vol. 8(6), June 2023 |
| **Field** | B — Policy learning (decide/act) |
| **Status** | Published |
| **PhD role** | Matures the action layer from imitation toward autonomy: the policy keeps improving in the field and surpasses its human teacher. |

**Abstract.** Robots can tackle human–robot interaction (HRI) tasks through inverse reinforcement learning (IRL). However, offline IRL agents' performance is upper-bounded by experts. Limited demonstration fails to provide an overall picture of the environment, especially in real-world applications. To further enhance IRL's performance, we implement a cross-modal inverse reinforcement learning to reinforcement learning (IRL-to-RL) transition framework for a real-world HRI interaction task, in which a mall receptionist android promotes sanitizer usage. During the 10-day experiment, the android develops a more proactive and effective strategy than the human expert. Furthermore, we explore four decay modes of prior knowledge supervision and suggest a preferable pattern for practical use. Our results demonstrate the feasibility of the framework to assist robots in switching to diverse modalities, learning incrementally with a sparse reward function, and eventually outperforming the human expert.

## 1. Background
Offline IRL is upper-bounded by the demonstrating expert, and limited demonstrations under-represent the real environment. Online RL could surpass this ceiling but is unstable from scratch with sparse rewards.

## 2. Objective
Let an IRL-initialized android keep improving online via RL in the real world, surpass the human expert, and identify how best to fade out the prior IRL knowledge.

## 3. Task design
Same mall-receptionist sanitizer-promotion task and android as Paper 1 (LaLaport EXPOCITY), enabling direct comparison. Visitors approach from three routes; the android masters timing of its four key actions.

## 4. Method
A **cross-modal IRL-to-RL transition framework**: start from the IRL policy, continue with RL under a **sparse reward**, while **prior-knowledge supervision decays** over training. Four decay modes are compared.

## 5. Evaluation method
A **10-day field trial** in the live mall: first six days for incremental learning vs the human expert, last four days to compare the four prior-knowledge decay modes; measured by task effectiveness (sanitizer usage).

## 6. Result
The android **learned incrementally and ultimately outperformed the human expert**, developing a more proactive, effective strategy. A preferable decay pattern for prior-knowledge supervision was identified.

## 7. Conclusion
The IRL-to-RL framework lets field robots learn incrementally with sparse rewards and **outcompete their human teachers**, a paradigm for further real-world HRI skill refinement.

## → Relevance to Paper 6
Shows Paper 6's policy layer can **adapt and improve during real deployment**, not just imitate. Reinforces field-readiness and gives a principled way to keep the integrated robot improving in situ.
