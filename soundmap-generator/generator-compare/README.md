# generator-compare

Comparison harnesses for the sound-map generators. Each head-to-head comparison
gets its own subfolder; the code they share lives in `utils.py`.

| item | what |
|---|---|
| [`utils.py`](utils.py) | shared comparison helpers — ROS2 bag reader + CDR decoders (`import utils as B`), the 4-label pipeline (`label_current_sm`, `transform_sm`, `extract_target7`, …), and MediaPipe head-box re-detection (`HeadBoxAPI`, imported lazily). Consolidates the former `bag_io.py` + `labeling.py` + `head_box.py`. |
| [`acoular-vs-pytorch/`](acoular-vs-pytorch/) | OLD **acoular** `BeamformerBase.synthetic` vs NEW **pytorch** FFT-power sum, over all 65 bags (is swapping OLD→NEW harmless to the 4-label decision?) |
| [`1bit-vs-pytorch/`](1bit-vs-pytorch/) | FFT/**pytorch** beamformer vs the **1-bit** XOR generator (real-bag video + synthetic precision sweeps) |

The generators themselves are **not** here — each comparison imports them from
the sibling generator folders under `soundmap-generator/` (and, for
`1bit-vs-pytorch/`, the FFT reference from the Playground-root `video-generator/`)
by prepending the right paths to `sys.path`. See each subfolder's README.

Runs under the `wolf` virtualenv (Python 3.10, numpy<2).
