"""Sound-map API: thin wrapper over the vendored acoular 'old' generator.

    api = SoundMapAPI()
    sm = api.generate([raw_bytes_of_audio_msg, ...])   # (64,64) float in [0,160]

`audio_chunks` = the raw uint8 payloads of consecutive /audio/audio_raw messages
(int16, 16ch interleaved), i.e. exactly what bag_io.decode_audio returns. This
reproduces the live DoA/PSSP sound map (SoundMapGenerator, generator='old').
"""
from __future__ import annotations

import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_SM_DIR = os.path.join(_HERE, "soundmap")
if _SM_DIR not in sys.path:
    sys.path.insert(0, _SM_DIR)  # makes both `import acoular` and `import sound_map` resolve here

XML_PATH = os.path.join(_SM_DIR, "acoular", "xml", "minidsp_uma-16.xml")


class SoundMapAPI:
    def __init__(self, xml_path: str = XML_PATH, fs: int = 44100, channels: int = 16,
                 blocksize: int = 4096, sm_size: int = 64, plot_size: int = 1080):
        from sound_map import SoundMapGenerator  # vendored, sets acoular caching to 'none'
        self._gen = SoundMapGenerator(
            fs=fs, channels=channels, blocksize=blocksize,
            xml_path=xml_path, sm_size=sm_size, plot_size=plot_size,
        )
        self.sm_size = sm_size
        self.channels = channels

    def generate(self, audio_chunks) -> np.ndarray:
        """audio_chunks: iterable of raw int16/16ch byte payloads. Returns (sm_size, sm_size) float [0,160]."""
        return self._gen.generate(list(audio_chunks))
