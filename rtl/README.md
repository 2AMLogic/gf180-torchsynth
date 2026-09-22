# RTL

No top-level RTL exists yet; the serialized one-shot pipeline begins only
after the fixed numeric contract, golden model, and the per-stage engines
are landed. The first implementation target is the exact four-second
one-shot profile, not live-note behavior.

Per-stage bit-exact engine RTL lives under `tb/sv/` with its golden-vector
flows (see `tb/README.md`): the LUT sine block (#68), the ADSR envelope
engine (#70), the LFO + control-rate VCA engine (#71), and the 4x5
modulation-matrix + endpoint-aligned control-upsample engines (#72).
