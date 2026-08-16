# acoular vs 1-bit -- 4-label agreement

- bags: `G11_game4_DoA,G2_game3_PSSP,G12_game3_Tele,G13_game4_Random` (3060 ticks at 4 Hz, first 10s skipped)
- machine: HOST (22 threads, unconstrained) (labels are machine-independent; this only affects how long the scan took)

**overall agreement: 2753/3060 (90.0%)**

| subset | agreement | n |
|---|---:|---:|
| overall | 90.0% | 3060 |
| VAD active | 93.3% | 1168 |
| VAD silent | 87.9% | 1892 |
| both head boxes valid | 90.0% | 3060 |

| bag | agreement | n |
|---|---:|---:|
| G11_game4_DoA | 88.0% | 756 |
| G2_game3_PSSP | 91.9% | 786 |
| G12_game3_Tele | 92.0% | 766 |
| G13_game4_Random | 87.9% | 752 |

confusion (rows = acoular label, cols = 1-bit label):

| acoular \ 1-bit | Left | Others | Right | Teleoperator |
|---|---|---|---|---|
| **Left** | 641 | 33 | 20 | 24 |
| **Others** | 35 | 533 | 103 | 14 |
| **Right** | 29 | 14 | 629 | 28 |
| **Teleoperator** | 4 | 1 | 2 | 950 |

mean decision margin (peak region metric - runner-up):

| generator | on agreeing ticks | on disagreeing ticks |
|---|---:|---:|
| acoular | 124.26 | 74.53 |
| 1bit | 82.82 | 38.89 |

most common disagreements (acoular -> 1-bit):

- `Others` -> `Right`: 103
- `Others` -> `Left`: 35
- `Left` -> `Others`: 33
- `Right` -> `Left`: 29
- `Right` -> `Teleoperator`: 28
- `Left` -> `Teleoperator`: 24
- `Left` -> `Right`: 20
- `Others` -> `Teleoperator`: 14

same, restricted to VAD-active ticks (a real talker, not room noise):

- `Right` -> `Teleoperator`: 28
- `Left` -> `Teleoperator`: 24
- `Others` -> `Teleoperator`: 14
- `Teleoperator` -> `Left`: 4
- `Left` -> `Right`: 3
- `Teleoperator` -> `Right`: 2
- `Right` -> `Left`: 2
- `Teleoperator` -> `Others`: 1
