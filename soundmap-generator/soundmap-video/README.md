# soundmap-video

Sound-map QC video generator, split out of `analysis2/code`.

The sound map itself is **not** generated here — it comes from the shared
PyTorch generator in the sibling `../generator-pytorch/` (`NewSoundMapAPI`), a
torch-based frequency-domain beamforming generator (no acoular). See
`../generator-compare/` for the validation that it's a harmless, ~7-8x faster
drop-in for the old acoular generator still used in `analysis2/`.

| file | purpose |
|---|---|
| `bag2video.py` | render one hardcoded bag (`BAG_PATH` + `BAG_NAME` at the top of the file) |
| `bag2video_all_bag.py` | render a hardcoded list of bags (`BAG_PATH` + `BAG_NAMES` at the top of the file) |
| `bag_io.py` | sqlite ROS2 bag reader + hand-written CDR decoders |
| `room1_vad.py` | silero VAD gate for the room1 mic, plus the QC-video VAD strip / label-bar renderer |
| `labeling.py` | sound-map mask / transform / colorize / 4-label extraction |
| `results/qc_video/` | rendered `*_sm_qc.mp4` output |

The sound-map generator is imported from `../generator-pytorch/` (added to
`sys.path` at the top of each script) as `NewSoundMapAPI` — there is no local
copy of the generator in this folder.

To point at a different bag, edit the `BAG_PATH`/`BAG_NAME` (or `BAG_NAMES`)
constants directly in the script — these are intentionally hardcoded rather
than passed as CLI args.

```bash
OPENBLAS_NUM_THREADS=1 python bag2video.py                  # single hardcoded bag
OPENBLAS_NUM_THREADS=1 python bag2video_all_bag.py --limit 5  # first 5 of the hardcoded list
```

No acoular / numpy<2 constraint (see `requirements.txt`) — run inside the
`wolf` virtualenv.
