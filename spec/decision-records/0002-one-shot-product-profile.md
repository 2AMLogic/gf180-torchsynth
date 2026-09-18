# DR-0002: Start with a one-shot sound explorer

- Status: Accepted
- Date: 2026-09-18
- Decision owners: 2AM Logic

## Decision

The first product profile renders the stock four-second Voice clip. A user can
generate, audition, repeat, save, and vary a sound, including locking selected
parameters. It is triggerable, but it is not initially a conventional live
keyboard synth.

## Rationale

Stock Voice is non-causal at two important boundaries:

- ADSR construction receives the note duration in advance.
- `AudioMixer` can inspect the peak of the complete clip and rescale every
  sample if the peak exceeds one.

Its control-rate signals are also interpolated across the known output buffer
with endpoint alignment. Faithfully rendering a clip therefore has a much
clearer reference contract than inventing note-off, retrigger, legato, and
real-time gain semantics.

## Consequences

- Canonical output is mono, 44.1 kHz, four seconds (176,400 samples).
- Repeat/recall and parameter identity are first-class product behavior.
- Live MIDI and sustained-note behavior require a separately named profile and
  a decision record; they must not silently alter the exact clip renderer.
- A host may provide exploration and patch storage before these functions are
  implemented on chip.

