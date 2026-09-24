# RTL

No top-level RTL exists yet; the serialized one-shot pipeline begins only
after the fixed numeric contract, golden model, and the per-stage engines
are landed. The first implementation target is the exact four-second
one-shot profile, not live-note behavior.

Per-stage bit-exact engine RTL lives under `tb/sv/` with its golden-vector
flows (see `tb/README.md`): the LUT sine block (#68), the ADSR envelope
engine (#70), the LFO + control-rate VCA engine (#71), the 4x5
modulation-matrix + endpoint-aligned control-upsample engines (#72), and
the full sine VCO engine (#73) — frequency formation from the keyboard
and mod inputs through the wrapping phase accumulator, the hash-linked
C5 quarter-wave LUT interpolation, and the Q2.21 output narrowing (the
declared `midi->Hz exp2` binary64 shadow site replayed host-side, per
DR-0008) — and the square/saw VCO engine (#74; the DR-0008/DR-0010
exp2/tanh/partials approximation sites are declared open items replayed
host-side — no RTL transcendental is implemented or claimed).
