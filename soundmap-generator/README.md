# soundmap-generator

Sound-map generators for the word-wolf / robot experiments. Each generator folder
holds **only** its own map-generation code; all comparison code lives under
`generator-compare/`.

| folder | what |
|---|---|
| [`generator-1bit/`](generator-1bit/) | 1-bit ("sign-bit XOR") sound-map generator (CPU-only) |
| [`generator-acoular/`](generator-acoular/) | OLD / **acoular** `BeamformerBase.synthetic` (live-robot generator) |
| [`generator-pytorch/`](generator-pytorch/) | NEW / **PyTorch** FFT-power sum, 2000–8000 Hz (offline reimplementation) |
| [`generator-compare/`](generator-compare/) | comparison harnesses — `utils.py` (shared helpers) + `acoular-vs-pytorch/` + `1bit-vs-pytorch/` |
| [`generator-field/`](generator-field/) | *(planned)* realtime sound-map / DoA detection, on top of pytorch or 1bit |

Each comparison under `generator-compare/` imports the generators it drives from
the sibling generator folders (via `sys.path`), so the generator folders stay free
of comparison code. Runs under the `wolf` virtualenv (Python 3.10, numpy<2).
