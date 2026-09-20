# Strata and measurement-coverage audit of the development corpus (96 cases)

This is the issue #21 evidence record: a non-acceptance audit of the
repeat-qualified development corpus — global indices 0–95 — characterizing
its parameter/audio strata and measurement coverage. It consumes the #19
first-run receipt and #20 repeat receipt through the landed read-only
validator (`tools/audit_development_corpus.py`), independently re-qualifies
the pair per identity, and computes the frozen-rule families in
`tools/audit_corpus_coverage.py`. Thresholds were frozen a priori in
`spec/reference/corpus-audit-rules-v1.json` (literal, justified, versioned);
no estimator was executed; no distribution or average score is computed or
called correctness; no holdout identity was read.

## Audit identity

- Rules: `spec/reference/corpus-audit-rules-v1.json`
  (sha256 `9775445542daf73b1d48253442e747091710adb58901813c76710d766acbddd0`,
  version `v1`), declared before corpus outcomes were examined; thresholds
  are descriptive conventions, not estimator floors or perceptual claims.
- Receipts audited: `sim/reference/development-corpus-first.json`
  (run `9c443eed363e4874a07328d8ddadd095`, receipt sha256 `da255466f611bd88…`)
  and `sim/reference/development-corpus-repeat.json`
  (run `1c3ca47b7e184eeca707b38dd89ca8ae`, receipt sha256 `613ba0500152d20e…`).
- Index (byte-identical across stores):
  `1b81701327150597fd6fd70b24d642bc87519a0c76777bc695506d3ae8bf80cd`.
- Producer commit `bd8b35e47df24133a9bbfe41917c8dd35696f798` (dirty false);
  normative upstream `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`; manifest
  `spec/reference/corpus-v0.json` (`141c7f05…6181`).
- Repeat qualification re-derived here, not trusted from summaries: per
  identity, metadata bytes and audio bytes byte-equal across both retained
  stores (96/96, zero divergences); the repeat receipt's recorded comparator
  verdict `COMPLETE-REPEAT` is recorded as provenance.
- Directed-coverage manifest bound read-only:
  `spec/reference/directed-coverage-v1.json`
  (sha256 `b181bc4b956eba7334b24111ac6ca54188b45923019a9586ecb04c8e9726c5bc`,
  392 prepared cases, `audio_render: not_run`).
- Committed report: `sim/reference/development-corpus-coverage.json`
  (sha256 `35f415a3b94c3a874d09824f2fa7fa5bbc68e2d326b08e78aa6df4d8ff7054b1`).
- Verdict: `QUALIFIED` — every family reconciles to the 96-case denominator.

## Commands and volatile run envelope

The report bytes are deterministic (identical bound inputs rebuild it
exactly); volatile telemetry lives only here, not in the report.

```sh
timeout 900 python3 tools/audit_corpus_coverage.py \
  --first-store /Users/joseph/dev/gf180-issue19-corpus-store \
  --second-store /Users/joseph/dev/gf180-issue20-repeat-store \
  --report sim/reference/development-corpus-coverage.json
timeout 900 python3 tools/audit_corpus_coverage.py \
  --first-store /Users/joseph/dev/gf180-issue19-corpus-store \
  --second-store /Users/joseph/dev/gf180-issue20-repeat-store \
  --check sim/reference/development-corpus-coverage.json
```

Both commands exited 0 on 2026-09-19 (Python 3.14.7, macOS; audit ≈68 s,
check ≈64 s; check verdict `IDENTICAL`). The stores are operator-retained at
the recorded paths; the audit is read-only and never creates directories.

## Findings (descriptive; no quality conclusion)

- **Normalization seam (measured 96/96).** Active 28, not applied 68, from
  producer-observed pre-normalization peaks in the digest-verified run
  envelopes (strict `> 1` contract; tie unchanged). Active-case applied
  gains span 0.3825–0.9990; observed pre-normalization peaks span
  0.0390–2.6145. The final peak alone was never used to reconstruct a seam.
- **Raw audio facts (measured 96/96).** All 96 clips are 176,400 finite
  float32 samples. RMS spans 0.00431–0.48134 native amplitude; |DC mean| ≤
  0.00562. No exact-silence, no near-silence (frozen 1e-6 RMS / 1e-4 peak
  cutoffs), no signed-zero samples, no sample beyond full scale; 28 samples
  sit exactly at |±1.0| — the peaks of the 28 normalized cases — which is
  not, by itself, evidence of a clipping operation. Stored summary rows were
  cross-checked against independently recomputed facts (exact equality).
- **Parameter strata (measured 96/96, 78 canonical names).** Normalized
  values binned into the frozen fixed quarters; raw normalized/physical
  values preserved in the per-case hash-bound artifact metadata (digests in
  the report). `keyboard.midi_f0` spans 2.16–126.88 (physical MIDI range
  0–127); `keyboard.duration` spans 0.0100–3.9837 s (range 0.01–4.0 s).
  These are intended controls, never measured output frequencies.
- **Continuous regimes (intended controls).** Dominant LFO weight (frozen
  lexicographic tie rule) spreads across all five shapes — lfo_1:
  saw 26, sqr 27, sin 10, rsaw 17, tri 16; lfo_2: saw 21, sqr 23, sin 19,
  rsaw 16, tri 17; `vco_2.shape` covers all four quarter bins (20/29/26/21).
  No selector-mode enums were invented.
- **Estimator applicability (no estimator executed).** Pitch stability and
  frequency ranges: unqualified 96/96 — the landed periodic qualification is
  `analytic-development-only` and no landed spectral method covers mixed
  Voice output. Observed noise contribution and observed envelope stages:
  missing 96/96 — the corpus is audio-only (`requested_traces` empty,
  `richer_module_facts` unavailable, reason `audio-only-adapter`); knob
  settings and requested ADSR times are recorded as intended controls, never
  as observed contributions or stage lengths.

## Coverage gaps → directed-fixture follow-ups (no corpus edits)

All four gaps affect all 96 cases and are linked, not filtered; linked
directed families are planned intentions (`audio_render: not_run`), not
executed evidence. Follow-up filings route through the orchestrator.

1. Pitch stability — propose executed isolated-VCO directed renders plus a
   separately qualified mixed-output pitch-qualification fixture before any
   corpus pitch claim.
2. Observed noise contribution — link `noise:isolated` and
   `mod_matrix.*->noise_amp:isolated` planned fixtures; coordinate with the
   trace owner on captured post-VCA/weighted pre-normalization source
   signals.
3. Observed envelope stages — link planned ADSR cut/long/zero-stage fixtures
   with executed `adsr_1.output`/`adsr_2.output` traces.
4. Frequency ranges — propose executed isolated-VCO directed renders before
   any corpus frequency-range claim.

## Limits

Descriptive coverage only: no fidelity, quality, rubric, optimization,
listening, or hardware claim; coverage counts do not estimate error, and
absent truth means no error verdict, not zero error. Estimator applicability
is not silently defined as estimator success — zero estimators were
attempted, by rule. A single audited corpus on one host; holdout identities
96–127 were refused before any payload access and remain unopened. The
audit/report tool is stdlib-only, read-only, and modifies no landed module.
