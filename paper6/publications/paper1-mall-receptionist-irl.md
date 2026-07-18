# Paper 1 — Android Receptionist via Inverse Reinforcement Learning

| | |
|---|---|
| **Title** | Android as a Receptionist in a Shopping Mall Using Inverse Reinforcement Learning |
| **Authors** | Zhichao Chen, Yutaka Nakamura, Hiroshi Ishiguro |
| **Venue / Year** | IEEE Robotics and Automation Letters (RA-L), Vol. 7(3), July 2022 |
| **Field** | B — Policy learning (decide/act) |
| **Status** | Published |
| **PhD role** | First demonstration that a data-driven action policy works in a real, uncontrolled HRI field setting. |

**Abstract.** For human-robot interaction (HRI), it is difficult to hand-craft all the rules for robots owing to diverse situations. Therefore, inverse reinforcement learning (IRL) is a potential solution that helps transfer human knowledge about interactions to robots. However, the feasibility of practically using IRL for HRI remains unknown. Here, we demonstrate a practical HRI application of IRL. An android was trained using IRL and acted as a receptionist to encourage visitors to practice hand hygiene in a shopping mall. We found that android learning through IRL has a competitive ability to a well-trained human operator on the reception task. Furthermore, we found that the android maintained high performance regardless of customer traffic. Our results demonstrate the potential of IRL in advancing the social HRI field.

## 1. Background
Hand-crafting interaction rules for social robots does not scale to the diversity of real-world situations. IRL can transfer human interaction know-how to robots without explicit rules, but its practical feasibility for real HRI was unproven.

## 2. Objective
Demonstrate that IRL can produce a usable interaction policy for a real-world social-HRI task, and test whether it matches a trained human teleoperator.

## 3. Task design
A Geminoid-F android acts as a receptionist at LaLaport EXPOCITY shopping mall, encouraging passing visitors to use a hand sanitizer. Seven pre-defined actions (verbal: HELLO/SANITIZER/THANKS/AWAY; nonverbal: TRACK/INITIALIZE/WAIT); the policy decides *which action and when* from camera-derived visitor state.

## 4. Method
Control is framed as an MDP; the policy is learned offline from human-teleoperator demonstrations via **Inverse Reinforcement Learning** (reward inferred from demonstrations, then a Double-DQN policy). Behavioral cloning (BC) is implemented as a comparison.

## 5. Evaluation method
Field trial in the live mall; performance measured by sanitizer-usage success, compared against the human operator and BC, across varying customer-traffic levels.

## 6. Result
The IRL android reached performance **competitive with a well-trained human operator** and **maintained high performance regardless of customer traffic**, outperforming the BC baseline.

## 7. Conclusion
IRL is a feasible route to real-world social-HRI behavior, removing the need for hand-crafted rules — a starting point for IRL-based HRI applications.

## → Relevance to Paper 6
Establishes Paper 6's **decision/action layer**: a learned, field-deployed policy for *what the robot does and when*. Provides the real-world-deployment credibility (mall android) that Paper 6's field run builds on.
