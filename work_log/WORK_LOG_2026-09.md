# Work Log Archive — 2026-09

Sections moved byte-for-byte from [WORK_LOG.md](../WORK_LOG.md) by `loom-daemon work-docs archive`.

### 2026-09-24

- **Issue #84** (closed): Lessons from gf180-parasynth: what cost us most, and the 80/20 that would have prevented it
- **Issue #151** (closed): reference-scalar CI red on main: intermittent release-era sentinel drift (global-6 canonical)
- **Issue #165** (closed): fix(tb): latent quotient-bit-offset bug in adsr_engine.sv's dead div_half_even path (found by #71)
- **Issue #172** (closed): Champion amendment (pre-approved): ratify the host-replay shadow boundary as the #74 declaration-class decision (DR-0008/DR-0010)
- **Issue #177** (closed): Reference repeatability CI red on main: in-container dispatch tier varies with host generation (sibling of #151, second sentinel workflow)
- **Issue #180** (closed): Trace artifacts CI red on main after #178 runtime publication republish: renderer QUALIFICATION_SHA256 pin stale
- **PR #168**: feat(tb): exact host-fed canonical noise-stream lane in RTL (issue 75)
- **PR #170**: fix(deps): update dependency setuptools to v83 [security]
- **PR #173**: docs(spec): ratify the host-replay shadow boundary as the #74 declaration-class decision (DR-0008/DR-0010 amendment)
- **PR #175**: fix(tb): correct dead div_half_even quotient-bit offset (issue 165)
- **PR #176**: fix(release-era): enforce the scalar sentinel's dispatch pins in the render container and bind the declared envelope (issue 151)
- **PR #178**: fix(release-era): pin the canonical AVX2 ISA tier in the repeatability render container (#177)
- **PR #179**: fix(release-era): recapture the scalar sentinel receipt after the #170 lockfile (#151)
- **PR #181**: fix(artifacts): re-pin the ratified runtime publication digest after the #178 republish (#180)
- **PR #184**: fix(release-era): republish the scalar receipt at full scope from the post-A2 tree (#179 follow-up)

### 2026-09-23

- **PR #167**: feat(rtl): bit-exact square/saw VCO engine vs the frozen fixed model's golden vectors (issue 74, Part of #74)

### 2026-09-22

- **Issue #69** (closed): [Epic #2] Implement RTL patch loader, identity, reset, and keyboard controls
- **Issue #70** (closed): [Epic #2] Implement bit-exact RTL ADSR envelope engine
- **Issue #71** (closed): [Epic #2] Implement bit-exact RTL LFO and control-rate VCA
- **Issue #72** (closed): [Epic #2] Implement modulation matrix and endpoint-aligned control interpolation RTL
- **Issue #74** (closed): [Epic #2] Implement bit-exact square/saw VCO RTL
- **PR #160**: Adopt Renovate dependency security policy (14-day quarantine)
- **PR #161**: feat: bit-exact RTL ADSR envelope engine + golden-vector flow (Part of issue 70)
- **PR #162**: .github/workflows: Migrate workflows to Blacksmith runners
- **PR #163**: feat(rtl): patch loader, identity, reset, and keyboard controls vs a cycle-exact host mirror (issue 69)
- **PR #164**: feat: bit-exact RTL LFO + control-rate VCA engine vs the frozen fixed model's golden vectors (Closes #71)
- **PR #166**: feat: bit-exact RTL modulation matrix + endpoint-aligned control interpolation vs the frozen fixed model's golden vectors (Closes #72)
- **PR #169**: feat(rtl): bit-exact sine VCO engine vs the frozen fixed model's vco_1.raw traces (issue 73)
- **PR #174**: feat(protocol): host transport client and behavioral-mock explorer backend (issue 66)
### 2026-09-21

- **Issue #44** (closed): [Epic #1] Preregister an audible-corruption ladder and blind listening protocol
- **Issue #45** (closed): [Epic #1] Benchmark paired perceptual diagnostics on TorchSynth corruptions
- **Issue #46** (closed): [Epic #1] Evaluate FAD-infinity and MMD only as population-drift diagnostics
- **Issue #47** (closed): [Epic #1] Decide the bounded role of perceptual and population metrics
- **Issue #48** (closed): [Epic #1] Freeze verification rubric v0 before numeric optimization
- **Issue #50** (closed): [Epic #1] Sweep fixed formats and approximations for the control path
- **Issue #51** (closed): [Epic #1] Sweep fixed formats and approximations for audio sources and mix path
- **Issue #52** (closed): [Epic #1] Measure and decide the exact whole-clip normalization architecture
- **Issue #53** (closed): [Epic #1] Ratify the fixed arithmetic and error contract
- **Issue #54** (closed): [Epic #1] Freeze the fixed Voice model and publish bit-exact golden vectors
- **Issue #62** (closed): [Epic #2] Specify the resolved-patch core and host transport protocol
- **Issue #63** (closed): [Epic #2] Ratify the one-shot RTL microarchitecture and schedule
- **Issue #68** (closed): [Epic #2] Add RTL numeric/interface package and golden-vector harness
- **PR #147**: feat: benchmark paired perceptual diagnostics on the ratified corruption ladder (issue 45)
- **PR #148**: feat: outlier injection rows + narrow-role decision complete issue 46 ACs
- **PR #149**: feat(spec): freeze verification rubric v0 — composed release rubric over landed families (issue 48)
- **PR #150**: feat: normalization reciprocal-precision measurement — DR-0003 decision evidence (issue 52)
- **PR #152**: feat(spec): control-path fixed-format sweep — per-quantity error tables over the candidate fixed model (issue 50)
- **PR #153**: feat(sim): sweep fixed audio-source formats and LUT geometries vs float references (issue 51)
- **PR #154**: feat(spec): ratify fixed arithmetic contract — DR-0008 Accepted (issue 53)
- **PR #155**: feat(protocol): bind numeric wire formats to the accepted DR-0008 register (issue 62)
- **PR #156**: feat: freeze the candidate fixed Voice model and publish bit-exact golden vectors (issue 54)
- **PR #157**: feat(spec): ratify the one-shot RTL microarchitecture and schedule — DR-0010 + DR-0003 Accepted (issue 63)
- **PR #158**: feat(tb): issue 68 remainder — real golden-vector anchor flow + accepted-contract hash-linking
- **PR #159**: feat(tb): emit the ratified DR-0010 cycle-budget constants + tb clip-budget check (issue 68) — Closes #68

### 2026-09-20

- **Issue #21** (closed): [Epic #1] Audit development-corpus strata and measurement coverage
- **Issue #24** (closed): [Epic #1] Integrate named traces with content-addressed render artifacts
- **Issue #30** (closed): [Epic #1] Implement named, composable fault-injection seams
- **Issue #31** (closed): [Epic #1] Add identity, parameter, and noise negative controls
- **Issue #32** (closed): [Epic #1] Add timing, interpolation, envelope, and modulation negative controls
- **Issue #33** (closed): [Epic #1] Add oscillator, gain, clipping, and normalization negative controls
- **Issue #34** (closed): [Epic #1] Publish the bidirectional mutation-coverage matrix
- **Issue #40** (closed): [Epic #1] Implement and match the independent float control path
- **Issue #41** (closed): [Epic #1] Implement and match independent float oscillators and noise
- **Issue #42** (closed): [Epic #1] Implement and match float VCA, mixer, and whole-clip normalization
- **Issue #43** (closed): [Epic #1] Publish end-to-end independent float conformance
- **Issue #49** (closed): [Epic #1] Implement deterministic fixed-point numeric primitives
- **Issue #117** (closed): Pin ATEN_CPU_CAPABILITY (host-dispatch env) for the scalar sentinel in CI
- **Issue #138** (closed): Land DR-0009 (draft): CI runner-pool reproducibility envelope for committed reference bytes
- **Issue #141** (closed): Land DR-0010 (draft): one-shot RTL microarchitecture skeleton (#63 groundwork)
- **PR #121**: feat: add deterministic fixed-point numeric primitives for issue 49
- **PR #127**: ci: pin host-dispatch env (ATEN_CPU_CAPABILITY + oneDNN/MKL ISA) for the scalar sentinel CI jobs (issue 117)
- **PR #128**: feat: audit development-corpus strata and measurement coverage (issue 21)
- **PR #129**: feat: add independent float VCO and noise source models matched at declared checkpoints
- **PR #130**: feat: implement and match the independent float control path (#40)
- **PR #131**: feat: add independent float mix chain matched at the declared checkpoints (issue 42)
- **PR #132**: feat: bounded actual-Voice fault injection executed and family seams declared (issue 30)
- **PR #133**: feat: add timing, interpolation, envelope, and modulation negative controls (issue 32)
- **PR #134**: feat: compose the independent float Voice end-to-end at the declared checkpoints (issue 43)
- **PR #135**: feat: add oscillator, gain, clipping, and normalization fault-operator family (issue 33)
- **PR #136**: feat: add identity, parameter, and noise negative controls (issue 31)
- **PR #137**: feat: compose the bidirectional mutation-coverage matrix over the landed fault families (issue 34)
- **PR #139**: docs(spec): file DR-0009 (draft) — CI runner-pool reproducibility envelope for committed reference bytes (#138)
- **PR #140**: feat: protocol-parameterized listening scaffolding for issue 44 (inert until ratification)
- **PR #142**: docs(spec): file DR-0010 (draft) — one-shot RTL microarchitecture skeleton (#63 groundwork) (#141)
- **PR #143**: feat(tb): golden-vector harness, synthetic DUT self-test, refusal-gated constants tooling (Part of #68, issue 68 startable subset)
- **PR #144**: feat: population-drift diagnostics — FAD∞/MMD machinery with declared OBS pin (Part of issue 46)
- **PR #145**: feat: issue 44 ratified config + listening session package (session 01)

### 2026-09-19

- **Issue #8** (closed): [Epic #1] Phase plan: qualified property estimators
- **Issue #11** (closed): [Epic #1] Prove selected-commit equivalence to TorchSynth v1.0.2
- **Issue #12** (closed): [Epic #1] Qualify repeatability and batch-size-independent sound identity
- **Issue #14** (closed): [Epic #1] Implement atomic content-addressed reference artifact storage
- **Issue #15** (closed): [Epic #1] Implement manifest-range rendering, corpus indexing, and holdout gate
- **Issue #17** (closed): [Epic #1] Preregister name-keyed directed Voice patch fixtures
- **Issue #19** (closed): [Epic #1] Render the first clean 96-case development corpus
- **Issue #20** (closed): [Epic #1] Independently repeat and byte-compare the development corpus
- **Issue #22** (closed): [Epic #1] Specify the Voice trace registry and non-perturbing capture seams
- **Issue #23** (closed): [Epic #1] Implement non-perturbing named Voice trace capture
- **Issue #25** (closed): [Epic #1] Implement exact paired waveform and trace metrics
- **Issue #26** (closed): [Epic #1] Qualify oscillator and LFO frequency, phase, and depth estimators
- **Issue #27** (closed): [Epic #1] Qualify ADSR and modulation-route property estimators
- **Issue #28** (closed): [Epic #1] Qualify waveform, spectral, noise, mixer, and normalization estimators
- **Issue #29** (closed): [Epic #1] Publish estimator validity, floors, and refusal qualification
- **Issue #39** (closed): [Epic #1] Specify independent float-model interfaces and trace checkpoints
- **Issue #64** (closed): [Epic #2] Build a software-only sound explorer MVP
- **Issue #65** (closed): [Epic #2] Implement favorites, parameter locks, and nearby variation
- **Issue #85** (closed): [Epic #1] Implement the evidence-derived capability DAG and stamped-node compiler
- **Issue #86** (closed): [Epic #1] Qualify signal preparation and semantic invariants
- **Issue #87** (closed): [Epic #1] Implement the case registry and generated evidence scorecard
- **Issue #88** (closed): [Epic #1] Qualify single-sound scalar execution against the batched reference
- **Issue #105** (closed): [Epic #2] Implement the artifact-backed explorer session and command layer
- **Issue #112** (closed): Land root rulings of 2026-09-19: reconcile DR-0006/DR-0007 and file DR-0008 (Proposed)
- **Issue #124** (closed): Reference scalar sentinel drifts across GitHub runner hardware (float32 ULP-level)
- **PR #95**: feat: add atomic content-addressed artifact storage
- **PR #96**: test: qualify selected Voice against patched v1.0.2
- **PR #97**: feat: add time-locked paired metrics and explicit scorecard rubrics
- **PR #98**: feat: derive capability states from validated evidence
- **PR #99**: feat: preregister name-keyed directed Voice fixtures
- **PR #100**: feat: qualify resolved scalar replay against batched Voice
- **PR #101**: feat: qualify fractional ADSR and signed route estimators
- **PR #102**: feat: generate scorecards from a versioned case registry
- **PR #103**: feat: qualify preparation with verified runtime evidence
- **PR #104**: feat: qualify bounded periodic signal estimators
- **PR #106**: test: qualify CPU repeatability and add a render drift sentinel
- **PR #107**: feat: qualify spectral noise and mixer measurement rows
- **PR #108**: feat: add verified artifact explorer sessions and commands
- **PR #109**: feat: specify and qualify passive Voice trace capture
- **PR #110**: feat: render resumable corpora with audited holdout admission
- **PR #111**: feat: generate evidence-derived README capability status
- **PR #113**: docs(spec): land 2026-09-19 root rulings — accept DR-0006, reconcile DR-0007, file DR-0008 (#112)
- **PR #114**: feat: specify arithmetic-independent core/host protocol subset (issue 62)
- **PR #115**: feat: implement non-perturbing named Voice trace capture for issue 23
- **PR #116**: feat: render first clean 96-case development corpus with audited fixed-96 receipt
- **PR #118**: feat: add software-only sound explorer MVP (issue #64)
- **PR #119**: spec: declare float-model interfaces and trace checkpoint map (issue 39)
- **PR #120**: feat: publish estimator validity, floors, and refusal qualification ledger (issue 29)
- **PR #122**: feat: apparatus fault-injection seams and refusal matrix (issue 30)
- **PR #123**: feat: implement favorites, parameter locks, and nearby variation (issue 65)
- **PR #125**: feat: independently byte-repeat the 96-case development corpus (issue 20)
- **PR #126**: feat: integrate named traces with content-addressed render artifacts

### 2026-09-18

- **Issue #10** (closed): [Epic #1] Reproduce a release-era TorchSynth CPU environment
- **Issue #13** (closed): [Epic #1] Define portable render-artifact and provenance schemas
- **Issue #16** (closed): [Epic #1] Generate and validate the canonical 78-parameter Voice inventory
- **Issue #18** (closed): [Epic #1] Implement the versioned scorecard row schema and no-verdict semantics
- **Issue #60** (closed): [Epic #2] Audit reusable substrate in 2AMLogic instrument repositories
- **Issue #61** (closed): [Epic #2] Decide pinned reuse and shared-substrate governance
- **PR #89**: feat: validate versioned scorecards and preserve refusal states
- **PR #90**: feat: generate the pinned Voice parameter inventory
- **PR #91**: docs: audit pinned instrument substrate reuse and evidence
- **PR #92**: feat: validate portable render artifacts and corpus indexes
- **PR #93**: feat: add pinned release-era CPU environment and render evidence
- **PR #94**: docs: define pinned local substrate reuse governance
