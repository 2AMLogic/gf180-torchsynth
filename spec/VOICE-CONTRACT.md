# TorchSynth 1 Voice compatibility contract

This is the normative behavior target for the first product profile. Values
marked **unratified** are research questions, not promises.

## Identity

- Upstream repository: `https://github.com/torchsynth/torchsynth`
- Commit: `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`
- Released compatibility baseline: `v1.0.2`
- Synth: `torchsynth.synth.Voice`
- Nebula: `torchsynth/nebulae/voice/default.json`
- Execution device for canonical fixtures: CPU
- Source and nebula hashes: [`reference/upstream.json`](reference/upstream.json)

The runtime lock and canonical host platform remain to be qualified. Every
render records them; no artifact is canonical merely because it used the right
Git commit.

## Clip configuration

| Property | Value |
| --- | --- |
| Sample rate | 44,100 Hz |
| Control rate | 441 Hz |
| Duration | 4.0 s |
| Output samples | 176,400 |
| Channels | 1 |
| Python reference dtype | float32 |
| Canonical corpus adapter batch size | 32, the minimum reproducible multiple |
| Hardware execution width | one resolved sound |
| Patch distribution | default Voice nebula |

TorchSynth's global identity for a sound is
`sound_index = batch_index * batch_size + slot`. The adapter may render an
identity in a different supported batch size without changing the selected
parameter draw. For example, upstream `synth1B1-312-6` at batch size 128 is
global sound index `39942`, or batch 1248 slot 6 at batch size 32.

Those batch coordinates belong to synth1B1 parameter/noise selection and to
efficient Python execution. They do not require a 32-lane implementation. Once
the canonical named patch and selected noise stream are resolved, the device
executes one sound. The relationship between batched upstream floating-point
evaluation and `batch_size=1, reproducible=False` scalar evaluation is being
qualified in [issue #88](https://github.com/2AMLogic/gf180-torchsynth/issues/88);
byte identity is not assumed.

## Graph

The default Voice consists of:

- monophonic keyboard/note signal;
- two LFOs and six ADSR envelopes;
- one sine audio oscillator and one square/saw audio oscillator;
- deterministic white-noise source;
- 4-by-5 modulation matrix;
- VCAs and final audio mixer.

There is no ladder filter in this target.

The default patch has 78 normalized latent parameters. The nebula contains a
curve and symmetry entry for each one (156 entries). Persistent patches are
maps from canonical names to values. Positional order is never an interchange
format.

## Semantics which are easy to accidentally change

- Control signals are linearly upsampled over the complete audio buffer using
  PyTorch endpoint-aligned interpolation (`align_corners=True`), not a
  zero-order hold and not necessarily a simple factor-of-100 interpolator.
- Envelopes receive note duration in advance.
- Mixer normalization is conditional: compute the maximum absolute sample of
  the complete clip and divide the whole clip by that value only when it
  exceeds 1.0.
- The upstream CPU noise generator is seeded with 13 and creates 32 streams;
  larger reproducible batches repeat those streams. The canonical noise slot
  for a sound is `sound_index % 32`.
- TorchSynth distinguishes normalized parameters from physical values. Saved
  metadata contains both, keyed by canonical name.
- Timing, gain, DC level, and clipping are contract behavior. A comparison may
  report an aligned or level-normalized diagnostic, but may not use it for the
  primary verdict.

## Hardware boundary

The accepted product profile renders one clip at a time. The remaining initial
boundary details in DR-0003 are proposed, not ratified. Numeric formats,
function approximations, rounding, saturation, output word length, clocks,
transport, and reset semantics are all **unratified** until measurement.
