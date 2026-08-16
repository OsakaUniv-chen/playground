# generator-field — realtime sound-map generator (planned)

Placeholder. This folder is reserved for the **realtime** sound-map / DoA
detection generator, to be built on top of the PyTorch (`../generator-pytorch`)
or 1-bit (`../generator-1bit`) implementations.

_No code yet — to be implemented._

## Which base, and on what box

The field target is a passively-cooled mini-PC (Intel N100 class) with no GPU,
so the choice is not "which generator is most accurate" but "which one still
leaves a tick's worth of budget for everything else". That question is measured,
not guessed, in
[`../generator-compare/acoular-vs-1bit/`](../generator-compare/acoular-vs-1bit/),
which runs both generators inside an emulated N100 (four pinned E-cores at a
locked 3.4 GHz):

| generator | ms/map | CPU ms/map | cores busy | of the 250 ms (4 Hz) tick |
|---|---:|---:|---:|---:|
| acoular (what the robot runs today) | 161.8 | 472.7 | 2.92 | 69 % |
| 1-bit (bit-shift + XOR) | 25.8 | 26.1 | 1.01 | 15 % |

Plus **10.7 ms/tick** for the shared labeling pipeline, the same for both.

So the 1-bit generator is the base to build this folder on: it holds 4 Hz with
~7× headroom on one core, needs no GPU by construction, and leaves three of the
N100's four cores free for the camera decode, head detection, policy and motor
control that share the tick. acoular fits the tick on paper but uses nearly the
whole machine to do it, and the pytorch generator's CPU path is slower still
(~18 ms/map vs the 1-bit generator's ~16 ms on the workstation — see
[`../generator-compare/1bit-vs-pytorch/`](../generator-compare/1bit-vs-pytorch/)),
with its GPU path unavailable on this hardware.

The cost is accuracy: the 1-bit generator agrees with acoular's 4-label decision
90 % of the time over 3060 ticks (93 % when someone is actually talking), and
85 % of the disagreements that happen *while* someone is talking are one
specific failure: the 1-bit map's narrower lobe spilling into the adjacent
teleoperator box. Its hard-limiting also makes a quieter second talker
about 6 dB harder to resolve than the linear beamformer does (both quantified in
the comparison READMEs). That is the tradeoff this folder is committing to.
