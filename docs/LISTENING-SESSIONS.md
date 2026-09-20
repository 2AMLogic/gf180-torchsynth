# Listening sessions — operator guide (issue 44, ratified protocol v1)

Status: the listening protocol was **ratified as drafted** on 2026-09-20
(issue #44 comment 5752723656, ratifying draft comment 5752151406 — One
Billion Sounds §4.2/§6 methodology, arXiv:2104.12922). The ratified
parameterization is [`spec/reference/listening-protocol-config-v1.json`](../spec/reference/listening-protocol-config-v1.json)
(config identity
`a23f9e9c101970404f3ee275b9a37934a71fbd5d94e914f9bf17d402d0bf278e`).
Stimulus set **session 01** has been generated on this host and is ready for
listening. **No listening has occurred yet; no perceptual claim exists.**

## Where things are

| Item | Location |
| --- | --- |
| Ratified protocol config | `spec/reference/listening-protocol-config-v1.json` (committed) |
| Session 01 manifest receipt (committed copy) | `sim/reference/listening-session-01-manifest.json` |
| Session 01 working directory (LOCAL — never committed) | `/Users/joseph/dev/gf180-listening-sessions/session-01/` |
| Stimulus audio (LOCAL) | `session-01/wav/` — 18 references (`REF-<case>.wav`), 84 degraded native (`<code>.wav`), 64 C1 RMS-matched variants (`<code>.C1.wav`) |
| Per-stimulus pm1 diagnostics (LOCAL) | `session-01/diagnostics/` — 84 files |
| Raw responses (LOCAL, PRIVATE) | `session-01/responses/` |
| Per-listener play orders (LOCAL) | `session-01/orders/` |
| Local render/order helper (LOCAL) | `session-01/render_session_stimuli.py` |

Custody is receipts-only: the repository holds the manifest receipt
(SHA-256 identities only). Stimulus audio and raw responses stay outside the
repository. The committed manifest copy is byte-identical to
`session-01/manifest.json` (sha256 `e2d62ec0c06a7c8e50245ed0a1d5caa67290f3dd`;
byte-exact reproduction requires the documented session paths above).

Session 01 contents: **84 degraded stimuli** (4 corpus-audio ladders × 5
ladder steps × 4 cases = 80, plus 4 `gain.polarity` binary trials) over **18
unique development-corpus cases**, each clip 4.0 s at 44.1 kHz mono; **22
host-run-gated operators** are recorded as `skipped` rows in the manifest,
never rendered. All 84 rendered payloads were re-derived from the corpus
store and verified byte-exact against their receipt SHA-256s.

## Regenerating the session (determinism)

```bash
WT=/Users/joseph/dev/gf180-torchsynth   # repo checkout with the ratified config
mkdir -p /Users/joseph/dev/gf180-listening-sessions/session-01/{audio,diagnostics}
python3 "$WT/tools/generate_listening_stimuli.py" \
  --config "$WT/spec/reference/listening-protocol-config-v1.json" \
  --receipt "$WT/sim/reference/development-corpus-first.json" \
  --coverage "$WT/sim/reference/development-corpus-coverage.json" \
  --store /Users/joseph/dev/gf180-issue19-corpus-store \
  --out /Users/joseph/dev/gf180-listening-sessions/session-01/manifest.json \
  --audio-dir /Users/joseph/dev/gf180-listening-sessions/session-01/audio \
  --diagnostics-dir /Users/joseph/dev/gf180-listening-sessions/session-01/diagnostics
cd /Users/joseph/dev/gf180-listening-sessions/session-01
python3 render_session_stimuli.py            # wav + sha256 verification
python3 render_session_stimuli.py --order L1 # randomized play order for listener L1
```

Two runs with the same arguments produce byte-identical receipts (verified
pre-release). Same config + seed ⇒ same selection, same codes, same audio.

## Session structure and listening order

Listener sequence per the ratified config: **pilot = 2 listeners**
(harness validation only — pilot results are *not evidence* and never adjust
magnitudes), then **main = 6 listeners (≥3 non-author musicians)**. Fixed
counts; no adaptive extension; a session failing validity is replaced at most
once.

Per listener session:

1. **Hardware record first** (fixed at ratification): one headphone model,
   one interface/DAC, 44.1 kHz playback, no EQ/processing, fixed comfortable
   level, quiet room. Record the hardware/level in the session notes.
2. **Session anchor:** play `REF-<case>` vs the L5 `clip.round_step`
   (2⁻⁵ quantization) pair for one case; the listener must identify the
   degraded interval. Failing the anchor invalidates the session.
3. **3AFC AX trials** in `orders/order-<listener>.csv` order. Per trial the
   operator plays **R** (reference), then **A**, then **B** from `wav/`:
   one of A/B is R again (hidden reference), the other is the degraded clip
   named by the row. Two responses per trial, recorded independently:
   - **Q1 (detection):** "Which clip differs from the reference?" — A, B, or
     "no difference". `detected=true` only when the degraded interval is
     correctly picked.
   - **Q2 (identity, forced):** "Does the differing clip still sound like the
     same patch/voice?" — yes/no. This is the AC3 identity question; it is
     **never merged** with Q1.
   - **Level condition:** play the `<code>.C1.wav` (RMS-matched) variant for
     every operator except `gain.db`; `gain.db` is **C2 native** — play
     `<code>.wav`. (Gain-matching is presentation harnessing only; paired
     measurement rows stay unaligned and un-gain-fit.)
   - **Catch trials** (~20%, marked in the private order sheet): A = B = R;
     the only correct answer is "no difference". The order sheet is for the
     operator's eyes only — never shown to the listener.
   - Take a break roughly every 20 trials; 4-second clips, ~101 trials +
     2 MUSHRA blocks ≈ 45–60 min.
4. **MUSHRA blocks (2, seven items each).** One block = one drawn case × one
   ladder operator; items = hidden reference + that operator's L1..L5 + the
   standardized anchor (`clip.round_step` at 2⁻⁵, same case). Rate each 0–100
   on impairment (100 = imperceptible). Suggested blocks: `clip.saturation_ceiling`
   on two different drawn cases (keeps the anchor role distinct from the
   block operator). Record the case/operator chosen per block in the
   responses file. **Bracket validation:** the hidden reference and anchor
   must bracket the block's ratings or the block is invalidated.

## Recording responses

Keep one **JSON response set per listener** in `session-01/responses/` (e.g.
`responses-L1.json`). During the session a plain CSV tally is convenient;
transcribe into the JSON afterwards — the analysis tool consumes only the
JSON. `stimulus_code` values and the condition map come from
`session-01/manifest.json` (operator-private custody copy).

CSV tally (one row per trial):

```csv
listener_code,index,stimulus_code,catch,response_differs,identity_same_patch
L1,1,ls1-<code>,no,A,no
L1,2,CATCH,yes,none,
```

JSON response set (exact contract of `tools/analyze_listening_responses.py`,
schema `torchsynth-listening-responses`; keys must be exactly these):

```json
{
  "schema": "torchsynth-listening-responses",
  "schema_version": 1,
  "protocol_config_sha256": "a23f9e9c101970404f3ee275b9a37934a71fbd5d94e914f9bf17d402d0bf278e",
  "unblinded": false,
  "condition_map": [
    {"stimulus_code": "ls1-<code>", "case_id": "global-1",
     "operator": "gain.db", "ladder_step": 1, "audio_sha256": "<from manifest>"}
  ],
  "listeners": [
    {
      "listener_code": "L1",
      "session_anchor_passed": true,
      "abx_trials": [
        {"stimulus_code": "ls1-<code>", "detected": true, "identity_same_patch": true}
      ],
      "catch_trials": [
        {"stimulus_code": "ls1-<code>", "false_positive": false}
      ],
      "mushra_blocks": [
        {"block_id": "M1",
         "ratings": [{"stimulus_code": "ls1-<code>", "rating": 62}]}
      ]
    }
  ]
}
```

Rules the analyzer enforces: `protocol_config_sha256` must equal the ratified
config's content identity (above); every `stimulus_code` is re-derived from
the config and any drift refuses the run; `unblinded` must be `true`, so set
it (and unblind) only **after all listener evaluations complete** (OBS §6).
`detected` = Q1 correct identification; `identity_same_patch` = Q2; catch
`false_positive` = claimed a difference when A = B = R; MUSHRA ratings are
0–100 integers.

## How the analysis runs

After the last listener finishes and the map is unblinded:

```bash
python3 tools/analyze_listening_responses.py \
  --config spec/reference/listening-protocol-config-v1.json \
  --responses /Users/joseph/dev/gf180-listening-sessions/session-01/responses/responses-all.json \
  --out /Users/joseph/dev/gf180-listening-sessions/session-01/analysis.json
```

Gates, in order: config ratified; AC6 ethics box complete (it is, per the
ratified default); response set bound to this config's identity; codes
re-derivable. Output is **rows only, no aggregate score**: per-condition
detection with exact Clopper–Pearson CIs, per-case and per-operator Spearman
ladder validation with monotonicity flagging (`invalid_anchor` is repaired in
a new protocol version, never smoothed), independent identity red-flag rows,
listener validity rows, MUSHRA bracket rows. Ladder verdicts are human
readings of these rows — the tool computes, people decide.

## Ethics reminder (AC6, ratified default — verbatim)

> raw responses retained privately by the operator, no public release of raw
> responses, anonymized aggregates only, amendment permitted before first
> collection

Concretely: keep `responses/`, orders, and the condition map in the local
session directory; **never commit or push raw responses**; listeners are
anonymized codes; only anonymized aggregate rows may later enter receipts or
PRs, after unblind. The default is amendable only **before** first
collection. Do not discuss trials between listeners before unblind.

## Honest limits of this package

- **Development corpus only.** All 96 stimuli come from the 96-case
  development partition. The 32-case blind holdout is sealed and untouched
  (issue #55); the generator structurally refuses any non-development case.
- **Corpus-audio operators only.** 4 ladders (`gain.db`, `gain.dc_offset`,
  `clip.round_step`, `clip.saturation_ceiling`) + the `gain.polarity` binary
  are rendered. 22 operators (9 ladders incl. all `osc.*`/`noise.*`/
  envelope/modulation ladders, and 13 binaries incl. all `norm.*` and timing
  ops) are host-run-gated pending the DR-0006 gated actual-Voice bridge; the
  draft's full fixed-session arithmetic (8 core operators, 160 3AFC trials)
  completes once the bridge and the blinded presentation harness land.
- **Manual presentation.** The blinded presentation harness (protocol-draft
  scaffold item b) is not landed; this package uses the documented manual
  procedure (deterministic order sheets from seed 440, sha256-verified
  rendered clips). A harness PR can adopt the same receipts unchanged.
- **No claims.** No perceptual verdict, ladder validity, threshold, or
  fidelity claim exists or may be inferred from this package. Pilot results
  are harness validation only. Thresholds move only via a new rubric version
  informed by ratified-protocol evidence plus estimator floors.
- `osc.tuning_shift` (when rendered post-bridge) carries the
  `unqualified_pitch_property_tag`: listen-only evidence, never backing a
  pitch-property claim (coverage receipt: 96/96 cases pitch-unqualified).
