# Paper 3 — PSSP Foundation: Acoustic-Visual Fusion (In-the-Wild)

| | |
|---|---|
| **Title** | A Feasibility Study With In-the-Wild Data in Human Interaction Settings: Acoustic-Visual Fusion for Predictive Sound Source Positioning |
| **Authors** | Zhichao Chen, Chenfei Xu, Huthaifa Ahmad, Yuya Okadome, Hiroshi Ishiguro, Yutaka Nakamura |
| **Venue / Year** | IEEE Access, 2025 |
| **Field** | A — Anticipatory perception (perceive/attend) |
| **Status** | Published |
| **PhD role** | The perception engine: predicts *future* sound-source maps from audio+video — anticipatory, not reactive, spatial awareness. |

**Abstract.** Acoustic data inherently contains spatial information regarding sounds. Most robotic systems rely primarily on speech recognition results for control; however, the utilization of rich directional cues could enhance their functionality. Especially in human-robot interaction, both verbal content and spatial context in acoustic data are crucial. To capture such auditory context, we employ acoustic maps in this research. These maps represent soundscapes as 2D projections, allowing robots to integrate auditory spatial cues with visual perception. Using a custom acoustic-visual module, we collect egocentric data from four daily scenarios and generate corresponding acoustic maps. By demonstrating a predictive sound source positioning task, we confirm the potential of acoustic maps to capture dialogue-relevant information. Based on these findings, we anticipate potential applications for robots, such as identifying individuals attempting to engage with the robot in crowded environments or predicting turn-taking in multi-party conversations.

## 1. Background
Robots mostly use audio for speech recognition and treat spatial sound only as noise to filter, overlooking rich directional cues. Existing spatial-audio methods stop at single-source localization of the *current* source.

## 2. Objective
Establish **acoustic maps** as a visual modality fused with vision, and show they carry **predictive, dialogue-relevant** spatial information — i.e., the future sound source can be anticipated.

## 3. Task design
**Predictive Sound Source Positioning (PSSP):** from a window of past acoustic-visual states, predict the *future* acoustic map (sound-source layout ahead in time) rather than localizing the present source.

## 4. Method
A custom acoustic-visual module produces acoustic maps (beamformed audio projected as 2D soundscapes overlaid on RGB). A multimodal model takes ten past states (~5 s) and predicts future maps at +0.5/+1.0/+1.5/+2.0 s; modality combinations are ablated.

## 5. Evaluation method
**In-the-wild egocentric data** from four daily human-interaction scenarios; prediction quality assessed against ground-truth future maps, with ablations across input modalities/channels.

## 6. Result
Acoustic maps capture dialogue-relevant predictive cues. Notably, **sound-map-only prediction can outperform some full-modality configurations**, and **more input channels do not guarantee better performance** — modality interplay is task-specific.

## 7. Conclusion
Acoustic mapping is a viable visual modality for anticipatory robotic perception, validated on in-the-wild data — paving the way to context-sensitive uses such as detecting engagement-seeking people or predicting turn-taking.

## → Relevance to Paper 6
Supplies the **perception/attention layer**: a validated mechanism for *where to attend, anticipatorily*. PSSP's future sound-source prediction is the sensing primitive Paper 6 fuses with the learned policy layer.
