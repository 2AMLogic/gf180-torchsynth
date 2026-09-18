# gf180-torchsynth — agent instructions

Open-source canary for a hardware implementation of TorchSynth's default
`Voice`, beginning as a deterministic one-shot sound explorer.

- The normative software source is `torchsynth/torchsynth` at commit
  `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`; do not silently follow upstream.
- `spec/` owns behavior. Changes to the target, arithmetic profile, noise
  policy, normalization, parameter ordering, or clip timing require a decision
  record before RTL changes.
- Verification is the product. Floating TorchSynth to a fixed-point model uses
  declared error metrics; fixed-point model to RTL is sample-exact. A test that
  did not run must never be reported as a pass.
- The canonical first product profile is an offline 4-second, 44.1 kHz,
  default-nebula clip. Live note semantics and the drum nebula are later,
  separately named profiles.
- Never claim gf180mcu synthesis, layout, signoff, hardware playback, or sound
  fidelity without a committed evidence record that actually establishes it.
- Use `klt` for ASIC flow work and report generic klayout-tools friction in
  `2AMLogic/klayout-tools`; `tb/run_tb.py` may remain a PDK-free convenience.
- Keep `AGENTS.md` and `CLAUDE.md` substantively identical outside their
  Loom-managed marker blocks.

<!-- BEGIN LOOM ORCHESTRATION (AGENTS) -->
This repository uses [Loom](https://github.com/rjwalters/loom) for AI-powered development orchestration (dual-runtime: Claude Code reads `CLAUDE.md`; OpenAI Codex CLI and other AGENTS.md-aware runtimes read this file). See the Loom repository for the full guide (roles, labels, worktrees, configuration). When installed, Loom also writes a locally-substituted copy of the runtime-neutral guide to `.loom/AGENTS.md`.
<!-- END LOOM ORCHESTRATION (AGENTS) -->
