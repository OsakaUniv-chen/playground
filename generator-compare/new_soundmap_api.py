"""New sound-map API: thin wrapper over the vendored 'new' generator.

    api = NewSoundMapAPI(device="cpu")
    sm = api.generate([raw_bytes_of_audio_msg, ...])   # (64,64) float in [0,160]

This is the PyTorch reimplementation the OFFLINE analysis1 used
(DoADetector(generator='new') -> NewSoundMapGenerator): a direct linear sum of
FFT power over a fixed 2000-8000 Hz band, vs the live/'old' acoular
BeamformerBase.synthetic(f=2000, num=3). Same audio input, same 16-mic xml,
same fs/blocksize/grid/sm_size as SoundMapAPI, so the ONLY thing that differs is
the beamforming algorithm.

`audio_chunks` = the raw uint8 payloads of consecutive /audio/audio_raw messages
(int16, 16ch interleaved), i.e. exactly what bag_io.decode_audio returns and what
SoundMapAPI.generate consumes.
"""
from __future__ import annotations

import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)                    # resolves `new_sound_map`
# Reuse the SAME 16-mic geometry as the 'old' generator (fair comparison).
XML_PATH = os.path.join(_HERE, os.pardir, "code", "soundmap", "acoular",
                        "xml", "minidsp_uma-16.xml")


class NewSoundMapAPI:
    def __init__(self, xml_path: str = XML_PATH, fs: int = 44100, channels: int = 16,
                 blocksize: int = 4096, sm_size: int = 64, plot_size: int = 1080,
                 device: str = "cpu", precision: str = "float32"):
        from new_sound_map import NewSoundMapGenerator
        self._gen = NewSoundMapGenerator(
            fs=fs, channels=channels, blocksize=blocksize,
            xml_path=os.path.abspath(xml_path), sm_size=sm_size, plot_size=plot_size,
            device=device, precision=precision,
        )
        self.sm_size = sm_size
        self.channels = channels
        self.device = str(self._gen.device)

    def generate(self, audio_chunks) -> np.ndarray:
        """audio_chunks: iterable of raw int16/16ch byte payloads.
        Returns (sm_size, sm_size) float in [0,160]."""
        return self._gen.generate(list(audio_chunks))
