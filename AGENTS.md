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

## Remote compute (AWS box)

Heavy builds/simulation that the dev Mac and CI cannot finish run on the
project's pinned AWS box, which is also the sanctioned full-coverage
substrate host for release-era campaign work:

- Instance `i-018841ef4169207ba` (tag `repo-remote`, us-east-1), SSH
  only, alias `repo-remote-gf180-torchsynth`. 8 vCPU / Intel Xeon 8175M
  (Skylake-SP, AVX512F); Docker 29 + buildx 0.30.1; python3.11 + uv;
  no apt; sudo present. `/home/ubuntu` sits on a 100G volume.
- The box auto-stops after ~120 minutes idle and **`/tmp` is wiped on
  restart**. Keep persistent state under `/home/ubuntu` and re-add git
  worktrees after any restart (a killed `git worktree add` leaves a
  partial tree — always verify `tools/run_fast_tests.py` exists after
  adding).
- Policy: us-east-1 region only; `ec2:RunInstances` allowed only with
  the `repo-remote` tag attached; no SSM/S3/IAM/AMI changes; no key
  material in the repo, chat, or this file (the credential index lives
  outside the repo at `~/.config/repo/README.md`). Escalate AWS issues
  to Robb (2AM #999).
- Box test invocation: `python3.11 tools/run_fast_tests.py ...` from the
  worktree root (direct file path). Both
  `python3.11 -m tools.run_fast_tests` and `-m tools.run_fast_tests.py`
  fail on the box.

<!-- BEGIN LOOM ORCHESTRATION (AGENTS) -->
This repository uses [Loom](https://github.com/rjwalters/loom) for AI-powered development orchestration (dual-runtime: Claude Code reads `CLAUDE.md`; OpenAI Codex CLI and other AGENTS.md-aware runtimes read this file). See the Loom repository for the full guide (roles, labels, worktrees, configuration). When installed, Loom also writes a locally-substituted copy of the runtime-neutral guide to `.loom/AGENTS.md`.
<!-- END LOOM ORCHESTRATION (AGENTS) -->
