# generator-acoular — OLD / acoular sound-map generator

The **live-robot** sound-map generator (`mode_doa` / `mode_pssp`,
`generator='old'`): vendored **acoular** `BeamformerBase.synthetic(f=2000, num=3)`
over a 3-level merged grid, 16-mic minidsp UMA array, fs=44100, blocksize=4096,
output 64×64 in [0,160].

## Contents

- `soundmap_api.py` — thin wrapper: `SoundMapAPI().generate(audio_chunks) -> (64,64)`.
- `soundmap/` — vendored acoular library + `sound_map.py` (`SoundMapGenerator`) and
  the mic geometry `acoular/xml/minidsp_uma-16.xml`.

## Use

```python
from soundmap_api import SoundMapAPI   # add this folder to sys.path
sm = SoundMapAPI().generate(audio_chunks)   # audio_chunks = raw int16/16ch payloads
```

Needs Python 3.10 with numpy<2 (acoular + numba). See
`../generator-compare/acoular-vs-pytorch` for the apples-to-apples comparison
against the PyTorch reimplementation (`../generator-pytorch`).
