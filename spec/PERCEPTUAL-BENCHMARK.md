# Paired perceptual-diagnostics benchmark v1

`torchsynth_voice.perceptual_benchmark` benchmarks explicitly configured
candidate perceptual metrics against the landed objective paired
diagnostics over the ratified issue 44 corruption ladder
(`spec/reference/listening-protocol-config-v1.json`; session 01 manifest
`sim/reference/listening-session-01-manifest.json`). The orchestrating tool
is `tools/benchmark_perceptual_metrics.py`; its committed receipt is
`sim/qualification/perceptual-benchmark-v1.json`.

These candidates are diagnostics only. They never establish per-sound
identity or implementation correctness, are never an acceptance oracle, and
are never an optimization target (the One Billion Sounds negative results:
optimizing an unqualified summary score produced extreme pitches and
unpleasant sounds that blind listeners rejected; docs/MEASUREMENT-PLAN.md).
No candidate becomes mandatory solely for a good aggregate correlation, and
no aggregate score is produced anywhere.

## Pins (versions, configs, windows, floors, resampling)

| Candidate | Pin | Key pinned config | Resampling |
| --- | --- | --- | --- |
| `multires-log-spectral-v1` (in-repo, numpy-gated) | module + `perceptual-benchmark-v1` | FFT sizes 512/1024/2048, half-FFT hop, periodic Hann, power with phase discarded, log floor 1e-10 (`10*log10(max(power,1e-10))`), per-frame L2 mean dB, summed over scales, identical-count framing | none (native 44.1 kHz) |
| `itu-r-bs.1387-2-peaq` | ITU-R BS.1387-2 (2023); probed implementations `peaqb`, `peaq`, gst `peaq` element | implementation-declared output grade; pinned per produced receipt | none applied by the adapter; the implementation's declared input rate governs |
| `google-visqol-v3-audio` | ViSQOL v3 audio mode, `libsvm_nu_svr_model.txt`; probed as the `visqol` binary | similarity-to-quality NSVR model | requires 48 kHz → declared resampler `windowed-sinc-kaiser16-w32-v1` (Kaiser β=16, half-width 32); its contribution is measured and reported beside, never inside, candidate scores |
| `cdpam-arXiv-2102.05109-pretrained` | pretrained CDPAM checkpoint (arXiv:2102.05109); probed as `cdpam` + `torch` | checkpoint version pinned per produced receipt | implementation-declared input rate; the adapter applies the declared resampler and reports it beside the score |

## Candidate limitations (primary sources)

- **PEAQ** is designed for perceptual impairment of audio codecs; it may be
  informative once fixed-point errors resemble codec impairment, but its
  version and implementation must be pinned and validated on our sounds
  (ITU-R BS.1387-2; docs/MEASUREMENT-PLAN.md).
- **ViSQOL** is trained around codec-like degradation, requires 48 kHz, and
  its own documentation warns it can perform poorly outside its training
  use and that single scores are not meaningful (Google ViSQOL docs).
- **CDPAM** is trained on human judgments of audio perturbations but rooted
  in speech-processing data; it is exploratory until calibrated against
  blinded judgments on TorchSynth sounds (arXiv:2102.05109).
- **All of them**: audio similarity is unreliable as a proxy for audio
  quality; similarity metrics must not be misrepresented as human quality
  (arXiv:2206.13411).

## Declared unavailability is explicit, never silent

Candidate implementations are declared pins, not repo dependencies. When a
tool or model is absent, availability probes report the exact missing
dependency (`peaq_implementation_unavailable`, `visqol_binary_unavailable`,
`cdpam_dependency_unavailable`, `numpy_unavailable_install_metrics_extra`)
and every affected pair gets an explicit refusal row — it is never skipped.
Score functions raise; the receipt records per-pair refusals verbatim.

## Human comparison: explicit NO VERDICT

The receipt carries a dedicated human-comparison row:

> verdict `NO VERDICT`, status `not_collected` — "listening not collected —
> operator declination of listening sessions (time cost), 2026-09-20;
> recorded in issue 44 closure comment 5753825121".

Per the issue 45 acceptance criteria, lack of human data is explicit. The
row is a sibling output, never merged into candidate statistics, and may
not promote any candidate.

## Agreement reporting (rows only, no aggregate)

Correlations are computed WITHIN one operator so every axis stays
unit-coherent. The rank axis is the ratified config's declared ladder-step
position (for `clip.saturation_ceiling` severity rises as the numeric
ceiling drops — the position, not the numeric value, is the rank). For each
(family, operator, candidate) the receipt reports Spearman rank
correlation with a deterministic percentile-bootstrap confidence interval
(seeded; `trials` and `seed` recorded in provenance) against:

- `declared_step_rank` (the ladder-validation statistic, OBS §4.2.1
  transposed), and
- the landed objective diagnostic rows `snr_db`, `error_rms`,
  `max_abs_error` (from the committed manifest's `paired_metrics`).

Ladder monotonicity is flagged per operator (group step-mean) and per case;
a non-monotone ladder becomes an `invalid_anchor` row — it is repaired in a
new protocol version, never smoothed or reinterpreted. Every agreement row
carries the note that a correlation never promotes a candidate. There is no
aggregate score and no all-up scalar.

## Resampler contribution (measured, not assumed)

The declared 44.1→48→44.1 kHz round trip is measured per reference case:

- **Primary rows** use the repo's unaligned paired diagnostics
  (`compare_paired`, no alignment/trim/gain-fit): the whole resampler cost,
  including delay and edge effects, under the no-alignment contract.
- One clearly-labeled delay-aligned diagnostic row (cross-correlation
  argmax, `diagnostic_only: true`) is reported beside the primary rows and
  never replaces them.

## Custody and binding

Stimulus audio stays in the operator's local session directory; the tool
refuses a session directory inside the repository. Every benchmarked pair
is re-derived from local reference audio and verified byte-exact (SHA-256)
against the committed session manifest before any candidate sees it; any
drift refuses the run. The receipt carries rows and identities only.

## Verification

`tests/test_perceptual_benchmark.py` contains stdlib tests (pins,
limitations coverage, human-row semantics, tie-aware Spearman, bootstrap
determinism and bracketing, monotonicity flagging, availability refusals,
input validation, agreement-row structure with no aggregate keys) plus an
explicit `--numpy` suite (resampler identity/lengths, Kaiser window,
multires identity and noise-monotonicity, contribution rows). The gated
suite fails to start if NumPy is absent.

Builder evidence recorded 2026-09-20, worktree `feature/issue-45` on main
`6ea3340`:

| Executed check | Observed result |
| --- | --- |
| `python3 -m compileall -q src tests tools` | Exit 0. |
| `python3 tests/test_perceptual_benchmark.py --numpy` (numpy-gated + stdlib suite, Python 3.14.7, NumPy 2.4.2) | 40 tests passed, zero failures/skips. |
| Full `python3 -m unittest discover -s tests` | See PR description; full suite is CI's to arbitrate. |
| `python3 tools/check_contract.py` | contract manifests are internally consistent, exit 0. |
| `timeout 600 python3 tools/benchmark_perceptual_metrics.py --session-dir <local session-01>` | receipt written; 84/84 pairs re-derived and byte-verified; 84 multires rows executed; PEAQ/ViSQOL/CDPAM refused with explicit reasons; human row NO VERDICT. |
| Receipt inspection | per-operator step-rank Spearman: `gain.db` +0.98, `clip.round_step` +0.89, `clip.saturation_ceiling` +0.79, `gain.dc_offset` +0.47; 16/16 per-case ladders monotone (0 invalid anchors); resampler primary SNR 80.2–111.3 dB unaligned with estimated delay 0 on all 18 cases. |

No perceptual verdict, threshold, or fidelity claim exists. Listening was
not collected; the human-comparison row is NO VERDICT. No gf180mcu
synthesis, layout, signoff, hardware playback, or sound-fidelity claim is
made.
