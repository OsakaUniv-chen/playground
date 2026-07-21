# generator-pytorch — NEW / torch sound-map generator

The **offline** PyTorch reimplementation (`DoADetector(generator='new')`): a direct
linear sum of FFT power over a fixed **2000–8000 Hz** band, vs the acoular
`BeamformerBase.synthetic`. Same 16-mic geometry, fs/blocksize/grid/sm_size as
`../generator-acoular`, so the only difference is the beamforming algorithm.
Raw maps correlate at r≈0.99999 with acoular and it is ~7–8× faster.

## Contents

- `new_soundmap_api.py` — wrapper: `NewSoundMapAPI(device=...).generate(audio_chunks) -> (64,64)`.
- `new_sound_map.py` — vendored verbatim from the robot-PC source tree (`NewSoundMapGenerator`).
- `minidsp_uma-16.xml` — local copy of the 16-mic geometry (same file as
  `../generator-acoular/soundmap/acoular/xml/minidsp_uma-16.xml`), so this folder
  is self-contained.

## Use

```python
from new_soundmap_api import NewSoundMapAPI   # add this folder to sys.path
sm = NewSoundMapAPI(device="cpu").generate(audio_chunks)
```

See `../generator-compare/acoular-vs-pytorch` for the full OLD-vs-NEW comparison.
