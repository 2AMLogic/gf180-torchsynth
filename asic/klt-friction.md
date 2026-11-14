# Generic `klt` friction found by issue #82 (ready for 2AMLogic/klayout-tools)

Everything in this file is about `klt` itself, not about this repository's
RTL. Each item is stated so it can be pasted into an upstream issue: observed
behaviour, minimal reproduction, and the workaround used here. Observed with
`klt 0.7.1.dev427` / `dev429` (the shared worker's `klt` was upgraded while
the sweep ran; per-response `provenance.klt_version` is recorded in the
evidence record), Yosys 0.67+post, OpenROAD 26Q3-1510, `gf180mcuD`
(open_pdks `f6eeac7d`), `gf180mcu_fd_sc_mcu7t5v0`.

Not yet filed upstream; the PR for #82 names this file.

## Re-check of 2026-11-14

Every item below was re-checked on **2026-11-14** against
**`klt 0.9.2.dev1022+g2a9c52b27ce7`** (`klt --version`), on host
**`loom-worker-1`**, with this repository at `ead68f4`. The original
`0.7.1.dev427`/`dev429` observations above are retained unchanged — each item's
`Status` line is the delta since then.

Each item carries exactly one `Status` line: `still present`, `changed`,
`fixed`, or `not re-checked` (with the reason it could not be settled cheaply).
Nothing here was re-checked by re-running `klt synthesize` or `klt sta` on the
voice or its engines; the evidence is help text plus the installed package
source, which is read-only. Where an item asked for a named field or flag, the
re-check is literally whether that name now exists — and the package's
top-level request allowlist (`synthesize.py:964-984`,
`_ALLOWED_REQUEST_KEYS`) is the authoritative answer for `klt synthesize`:

```
arithmetic, baseline, constraints, defines, engine, hdl_toplevel, latch_map,
macros, outputs, parameters, pdk, run_id, schema, sources, structural,
sv_frontend, yosys_timeout_s
```

The package source read throughout is
`/home/ubuntu/.local/share/uv/tools/klayout-tools/lib/python3.14/site-packages/klayout_tools/`
(`klt` is a `uv` tool in its own venv, so `klayout_tools` is **not** importable
from the system `python3`; read the tool's own site-packages path directly, or
run under the tool's interpreter).

**Summary**

| Item | Status | Short form |
|---|---|---|
| F1 | `fixed` | `-sv` inferred from the extension, plus an opt-in `sv2v` front end |
| F2 | `fixed` | request-level `yosys_timeout_s` exists; still no CLI flag |
| F3 | `still present` | the synthesize path still does not process-group Yosys |
| F4 | `still present` | no ABC script / `-fast` / timeout / `-noabc` request field |
| F5 | `changed` | false-path-from + uncertainty + multi-clock landed; multicycle and generated clocks did not |
| F6 | `still present` (host-scoped) | `klt_statime_native` not provisioned on `loom-worker-1` |
| F7 | `still present` | multi-corner top-level `deck` is still `null`, no `decks[]` |
| F8 | `still present` | no `hierarchy[]` response field |
| F9 | `still present` | nothing in a request pins the required klt version |
| F10 | `still present` | no `replaced_by_frontend` memory status |
| F11 | `not re-checked` | the 968 s measurement needs a long `klt sta` run |
| F12 | `still present` | the mutation guard is still stat-based |

## F1. `klt synthesize` cannot read SystemVerilog (no `read_verilog -sv`)

`synthesize.py` emits `read_verilog <file>` with no `-sv`, and there is no
request field to change that (and no `read_slang` path). Any `package`,
`import pkg::*`, `localparam int|string`, or other SV construct fails with
`<file>:1: ERROR: syntax error, unexpected TOK_ID` even for a `.sv` file.

* Repro: a file starting with `package p; localparam int W = 8; endpackage`.
* Wanted: `request.language` (`verilog`/`systemverilog`) or `-sv` inferred from
  the `.sv` extension, and optionally `read_slang` when the plugin exists.
* Workaround here: `tools/synth_view.py` rewrites the package into
  `` `define `` macros (a derived source, hashed in the evidence record).

**Status (2026-11-14, `klt 0.9.2.dev1022+g2a9c52b27ce7`): fixed.** Both of the
mechanisms the item asked for now exist, by a different route than the "Wanted"
line guessed:

* **`-sv` is inferred from the extension.** `read_verilog_line()`
  (`_verilog_params.py:131-158`) emits `read_verilog -sv <path>` for a
  `.sv`/`.svh` source (case-insensitive) and plain `read_verilog` for every
  other extension. Checked by reading the function and its docstring (issue
  #3797 upstream). This is the half that matters for the repro above: a
  `package`/`localparam int` in a `.sv` file now parses.
* **An external SV front end, stronger than the optional `read_slang` ask.**
  `request.sv_frontend` is in the allowlist and accepts `"yosys"` (default) or
  `"sv2v"` (`_sv2v.py`, upstream #4198); `"sv2v"` converts all sources once,
  through a single `sv2v` invocation, into one Verilog-2005 file every
  downstream pass reads, so all reported numbers describe one elaboration.
  Wired in at `synthesize.py:1163` and `:1258-1272`, with the route and
  `sv2v --version` recorded in `provenance.sv_frontend`.
* `request.language` itself does **not** exist (not in the allowlist above),
  and there is no `read_slang` path (`grep -rn 'read_slang\|slang'` over the
  package: no match). Neither is needed — the item's actual defect is gone.
* Yosys's native `-sv` still rejects some SystemVerilog (e.g. unpacked-array
  module ports), which is exactly what the `sv2v` route is for;
  `synthesize.py:3164` adds a hint pointing at it when a `.sv` source draws an
  `ERROR:`.

**Also present at the CI pin.** At `klayout-tools==0.8.0`
(`uvx`/`uv run --with` throwaway env, 2026-11-14)
`read_verilog_line("foo.sv")` already returns `read_verilog -sv foo.sv`, and
`read_verilog_line("foo.v")` returns `read_verilog foo.v`. The `sv2v` route is
*not* at `0.8.0` (`klayout_tools._sv2v` does not import). See "Removable
workarounds".

## F2. Yosys wall-clock timeout is a hard-coded 300 s with no override

`DEFAULT_YOSYS_TIMEOUT_S = 300.0` (the docs say "a fixed module constant (no
request-level override/CLI flag)"). A real design whose `abc` step is slow is
reported as `yosys did not complete within 300.0s ... process killed`, with no
way to ask for more from the request or CLI.

* Wanted: `--yosys-timeout-s` / `constraints.yosys_timeout_s` (mirroring
  `--equiv-timeout-s`).
* Workaround here: `tools/synthesize_oneshot.py run --timeout N` (N > 300)
  patches `klayout_tools.synthesize._run_yosys.__kwdefaults__['timeout_s']`
  from a `-I -c` wrapper under klt's own interpreter; the record states which
  targets ran that way.

**Status (2026-11-14, `klt 0.9.2.dev1022+g2a9c52b27ce7`): fixed** for the
request half; the CLI half is still unmet.

* A request-level override exists: **top-level `request["yosys_timeout_s"]`**,
  validated and bound by `synthesize_timeout.py` (`bind_yosys_timeout()`,
  called at `synthesize.py:1164`), with `YOSYS_TIMEOUT_MAX_S = 3600.0` as the
  ceiling. It is bound for the whole `run_synthesize` call through a
  `ContextVar`, so every `_run_yosys` call — including those inside
  `synthesize_arithmetic` / `synthesize_memory` — honours it, and it is echoed
  into `provenance` (`synthesize.py:1253`) and the report (`:1633`).
* Note the field is **top-level `yosys_timeout_s`, not
  `constraints.yosys_timeout_s`** as the "Wanted" line above guessed.
* `DEFAULT_YOSYS_TIMEOUT_S = 300.0` is still the default
  (`synthesize.py:3077`), resolved against the bound budget at `:3117`. That is
  now a default rather than a hard-coded constant, which is what the item
  asked for.
* **No `--yosys-timeout-s` CLI flag.** `klt synthesize --help` (re-run
  2026-11-14) lists `--equiv-timeout-s` but no Yosys timeout flag; a caller
  that cannot author the request document still has no override.

**Absent at the CI pin — this is the load-bearing caveat.** At
`klayout-tools==0.8.0` (`uv run --with "klayout-tools==0.8.0"`, 2026-11-14)
`yosys_timeout_s` is **not** in `_ALLOWED_REQUEST_KEYS` (the `0.8.0` allowlist
is `arithmetic, baseline, constraints, engine, hdl_toplevel, outputs,
parameters, pdk, run_id, schema, sources, structural`) and
`klayout_tools.synthesize_timeout` does not exist. So the fix is real at
`0.9.2.dev` and absent at the version this repo runs. See "Removable
workarounds".

* Paste-ready upstream title (for the remaining CLI gap):
  `klt synthesize: add --yosys-timeout-s, mirroring --equiv-timeout-s, for the request-level yosys_timeout_s budget`

## F3. Timeout kills `yosys` but leaves its `yosys-abc` child running

After the 300 s kill, `yosys-abc -s <abc.script>` kept running at 100 % CPU,
re-parented to the user manager (observed 6+ minutes after the kill, still
consuming a core on a shared 8-vCPU worker). `subprocess.run(timeout=)`
kills only the direct child.

* Wanted: run Yosys in its own process group (`start_new_session=True`) and
  `killpg` on timeout.
* Workaround here: the harness reaps `yosys-abc` processes whose cwd is the
  repo and whose parent is not a `yosys` process, after every klt call.

**Status (2026-11-14, `klt 0.9.2.dev1022+g2a9c52b27ce7`): still present.**
`_run_yosys` (`synthesize.py:3080-3135`) still calls
`subprocess.run(["yosys", "-s", script_path], ..., timeout=timeout_s, cwd=cwd)`
with **no** `start_new_session`, and its `subprocess.TimeoutExpired` handler
(`:3129`) writes the log and raises `SynthesizeError` without signalling a
process group. The grandchild is therefore still orphaned on timeout.

Searched the whole package this time, not just `synthesize.py`
(`grep -rn 'start_new_session\|killpg\|setsid\|process_group\|preexec_fn'`):
the pattern **does** exist upstream, just not on this path —
`netlist.py:299` (`start_new_session=True`) with its `_signal_group` helper at
`:188-214`, whose docstring states "Never signals our own group", `formal.py:439`/`:448`,
and `functional_verification_mutation_variant.py:95`/`:123`. So the fix is a
matter of applying an established in-tree idiom to `_run_yosys`, which makes
this a cheap upstream change.

* Paste-ready upstream title:
  `klt synthesize: _run_yosys orphans yosys-abc on timeout — use start_new_session + killpg as netlist.py and formal.py already do`

## F4. The ABC step has no cheap or bounded mode

The generated script always runs `synth` (whose internal `abc -fast` is
followed by the liberty `abc -constr -D` pass using the default script with
`&fraig -x`, `scorr`, `&dch -f`, `&nf`...). SAT-sweeping/choice computation
does not converge in minutes on arithmetic-heavy combinational cones such as
a 64-iteration unrolled restoring divider (about 38 k gates into ABC). There is
no request field for `synth -noabc`/`abc -fast`, an ABC script, a per-run ABC
time limit, `synth -flatten`/`-run` ranges, or a `-D`-less map.

* Wanted: `synthesis.abc_script` / `abc_fast: true` / `abc_timeout_s`, and a
  documented way to keep a partial (pre-ABC) gate-level result when ABC is
  killed.

**Status (2026-11-14, `klt 0.9.2.dev1022+g2a9c52b27ce7`): still present.** The
feature-absence half is verified package-wide;
`grep -rn 'abc_script\|abc_fast\|abc_timeout\|noabc\|abc -fast\|"-fast"'` over
every `.py` in the package returns **no match**, and none of
`synthesis`/`abc_script`/`abc_fast`/`abc_timeout_s` is in the top-level request
allowlist above.

* The script is still emitted unconditionally: `synthesize.py:3015` appends
  `synth -top <top>` with no `-noabc`, no `-flatten`, no `-run` range, followed
  by `dfflibmap -liberty`, then `abc_command`, then `clean` (`:3020-3024`).
  `abc_command` is built at `:2969-2977` as
  `abc -liberty <lib>` plus an optional `-constr`, `-D <ps>` and `-dont_use`,
  so the `-constr -D` pass the item describes is intact and there is still no
  way to bound or replace the ABC invocation.
* There is also still no documented way to keep a partial pre-ABC netlist when
  ABC is killed: a timed-out run raises `SynthesizeError` (F3 above) and
  `_read_produced_netlist` (`synthesize.py:1743`) treats a missing netlist as a
  hard error.
* `--restructure-timing` / `--restructure-max-iterations` (new in
  `klt synthesize --help` since the original observation) do **not** address
  this: they bound a post-synthesis cell-resizing loop, not the ABC mapping
  pass.
* **Not re-measured:** the convergence claim itself (the unrolled divider not
  converging in minutes, ~38 k gates into ABC) is a measurement from the #82
  run. It was not re-measured here — that needs a `klt synthesize` run this
  issue's scope forbids. Only the absence of the request fields is re-verified.

* Paste-ready upstream title:
  `klt synthesize: no request-level control over the ABC mapping pass (abc_script / abc_fast / abc_timeout_s), so an arithmetic-heavy cone can only be abandoned`

## F5. `klt sta` cannot express exceptions, generated clocks or uncertainty

Constraints are `clock_port`, `clock_period_ns`, scalar or per-port
input/output delays only. There is no multicycle path, false path, clock
uncertainty/latency, generated clock, clock-gating or case analysis. A design
that is correct only because a host sequences enables over several cycles
(this one: `ctl_en_*`, `audio` phase enables) can only be timed single-cycle,
which is pessimistic by construction. The honest report is the one made
here (the exception is not modelled, so the number is a bound on the
single-cycle interpretation, not on the multi-cycle design).

* Wanted: `constraints.exceptions[]` (multicycle/false path by port or pin
  pattern) and `clock_uncertainty_ns`, or an `sdc` file input with the
  existing completeness report applied to it.

**Status (2026-11-14, `klt 0.9.2.dev1022+g2a9c52b27ce7`): changed.** The
constraint surface has widened materially, but not in the way this repo's own
multi-cycle enable sequencing needs. Validation lives in `post_route_sta.py`
(which `klt sta` shares).

Now available:

* **`constraints.false_path_from`** — a list of literal input-port names (one
  scalar port or one bus bit) excluded with `set_false_path -from`
  (`_validate_false_path_from`, `post_route_sta.py:2448-2484`, upstream #2968;
  threaded through to the engine session at `:695`, `:784`, `:1160`). It
  rejects the clock port outright. This is the *false path* half of
  `exceptions[]`, restricted to `-from <input port>`.
* **`constraints.clock_uncertainty_ns`** — scalar or a
  setup/hold map, emitted as `set_clock_uncertainty`
  (`_sta_clock_uncertainty.py`, upstream #4240). This is the second thing the
  "Wanted" line asked for, and it is **fixed**.
* **`constraints.clocks`** (a `{port, period_ns}` list, first is primary) and
  **`constraints.clock_groups`** (asynchronous groups) for multi-clock designs
  (upstream #4241, `_validate_clock_extras`), plus per-corner
  `period_ns` maps under `pdk.corners` (upstream #5240). All confirmed in
  `klt sta --help` (re-run 2026-11-14).
* **`request.sdc_check = {"path": "<file>.sdc"}`** (`_sta_sdc_check.py`,
  upstream #5236) — but read its docstring before counting it: it is a
  **drift check only**, a pure-Python reader comparing four commands
  (`create_clock`, `set_input_delay`, `set_output_delay`,
  `set_clock_uncertainty`) against the request. "No Tcl is executed, nothing
  reaches OpenSTA, the SDC is never a constraint source", and "drift is data,
  not a gate". So the item's alternative ask — *an `sdc` file input* — is
  **not** met.

Still absent (`grep -rn 'multicycle\|set_multicycle\|create_generated_clock\|generated_clock\|case_analysis'`
over the package: no match in the request/constraint surface):

* **multicycle paths** — the exception this repo actually needs, since
  `ctl_en_*` / `audio` phase enables are sequenced by the host over several
  cycles. `false_path_from` cannot express it: it only anchors at input ports,
  not at the internal register-to-register paths the enables gate.
* **generated clocks**, **case analysis**, and **clock gating** as request
  constraints (`-clock_gating_setup`/`-hold` appear only as OpenSTA *report*
  check families in `_openroad_engine.py:551-566` and
  `post_route_sta_path_groups.py:54-55`, not as constraints you can declare).
* **false paths other than `-from <input port>`** — no `-to`, no `-through`, no
  pin patterns.

So the original finding's conclusion still stands for this design: the reported
number remains a bound on the single-cycle interpretation, and the honest
report made for #82 does not need revising.

* Paste-ready upstream title:
  `klt sta: no multicycle-path or generated-clock constraint (false_path_from only anchors at input ports), so an enable-sequenced design can only be timed single-cycle`

## F6. `sta` in `klt synthesize` is `null` on the shared worker

`"sta": null, "sta_unavailable_reason": "the optional klt_statime_native
extension is not installed ..."`. The extension is not part of the uv tool
install on the worker, so every response carries only ABC's wire-free
`stime` number (which the docs say is *never slack*). This repo therefore
uses `klt sta` (OpenSTA, `verilog` mode) for timing. Worker-spec item, not a
code bug; noted so the provisioning owner can decide.

**Status (2026-11-14, `klt 0.9.2.dev1022+g2a9c52b27ce7`, host
`loom-worker-1`): still present on this host.**

This is a **host provisioning fact, not a klt code fact**, and
`loom-worker-1` is **not** the machine the #82 sweep ran on — so this is a
second independent observation, not a re-check of the original one. Checked
under klt's own interpreter (the extension would not be visible to the system
`python3` either way):

* `importlib.util.find_spec('klt_statime_native')` → `None`.
* No `klt_statime_native*` anywhere under the `klt` uv tool install
  (`find /home/ubuntu/.local/share/uv/tools/klayout-tools -name 'klt_statime_native*'`:
  no match).

The klt-side handling is unchanged and remains correct: the reason string at
`synthesize.py:3507-3513` still distinguishes "extension not installed" from
the other unavailability cause (upstream #2628), and `--restructure-timing`
documents the extension as a hard requirement. Nothing to file upstream as a
code bug; this stays a note for whoever owns the worker spec. Per the host
policy, no attempt was made to install it.

## F7. `klt sta` `verilog` mode fills the response `deck` with `null` for `pdk.corners`

For a `pdk.corners` request the top-level `provenance.deck` is `null` (by
design, documented) and each `corners[]` entry carries its own `deck`; a
consumer that only reads `provenance.deck.content_hash` sees no liberty
identity. Harmless but easy to miss; a top-level `decks[]` summary would make
committed records self-describing.

**Status (2026-11-14, `klt 0.9.2.dev1022+g2a9c52b27ce7`): still present.**
`_run_multi_corner` (`post_route_sta.py:1456`) still builds its provenance with
`build_provenance(deck_name=None, deck_path=None, ...)` (`:1618-1620`), and
each corner entry still carries its own `"deck": _deck_block(...)`
(`:1597-1604`). The module docstring at `:200-211` confirms the intent is
unchanged: the structurally-shared fields hoisted to the top level are
`def_path`/`verilog_path`/`geometry_source`/`wire_load_model`/`wire_load_mode`/
`spef_path` and `provenance.pdk`/`.input` — `deck` is deliberately not among
them. No top-level `decks[]` summary exists
(`grep -rn '"decks"' post_route_sta.py sta.py`: no match; the only `decks`
hits in the package are the unrelated DRC/`decks/` deck-library package).

* Paste-ready upstream title:
  `klt sta: add a top-level decks[] summary for pdk.corners responses, so a committed multi-corner record is self-describing without walking corners[]`

## F8. No hierarchy view in the response

`klt synthesize` reports one aggregate `instance_count` / `area_um2` /
`instance_counts_by_type`. Per-module area (needed for "cell area by
hierarchy") has to be re-read from the retained `<top>_stats.json`
(`modules.<name>.num_cells` / `.area`), whose schema is Yosys's, not klt's.
A `hierarchy[]` response field (module, cells, area, instance count) would
make that a contract rather than a convention.

**Status (2026-11-14, `klt 0.9.2.dev1022+g2a9c52b27ce7`): still present.** No
`hierarchy` field exists in the response
(`grep -rn '"hierarchy"\|hierarchy\[\]\|per_module\|area_by_module'` over
`synthesize.py`: no match), and the response assembly (`synthesize.py:1570-1640`)
still emits only the aggregate `instance_count` / `area_um2` /
`instance_counts_by_type` alongside `structural`, `warnings`, `memory_mapping`
and friends. Per-module numbers are still read out of Yosys's own
`stat -json` shape (`module_stats["num_cells"]` at `:1515` and `:1644`,
`num_cells_by_type` at `:1409`), i.e. the retained `<top>_stats.json`
convention the item describes, not a klt contract.

* Paste-ready upstream title:
  `klt synthesize: add a hierarchy[] response field (module, cells, area, instance count) so per-module area is a contract, not a Yosys stats.json convention`

## F9. Reproducibility: `klt --version` moved during one sweep

`klt 0.7.1.dev427+ga7e9b9cb01c9` at the start of the run and
`0.7.1.dev429+g435837f5c237` in the later `klt sta` responses on the same
worker (tool upgraded in place by provisioning). Nothing in a request pins the
required klt version, so a `--check --rerun` can legitimately drift. A
`requires_klt` request field (or `klt synthesize --require-version`) would
turn silent drift into a hard error.

**Status (2026-11-14, `klt 0.9.2.dev1022+g2a9c52b27ce7`): still present.**
Searched the whole package, with a control to prove the search reaches the
files:
`grep -rn 'requires_klt\|require_version\|require-version\|min_klt\|klt_version_required'`
over every `.py` returns **0** hits, while a control
`grep -c read_verilog synthesize.py` on the same path returns 17. Neither
`requires_klt` nor any equivalent is in the top-level request allowlist above,
and `klt synthesize --help` (re-run 2026-11-14) has no `--require-version`.

`--check`/`--rerun` still explicitly *exclude* `provenance.klt_version` from
the diffed fields (documented in `--rerun`'s own help text: "excluding
`provenance.klt_version`/`klayout_version`/`pdk.version`"), which is the
behaviour that lets the drift pass silently — so the item is not merely
unimplemented, the excluded-field list is where a fix would have to engage.

* Paste-ready upstream title:
  `klt: no request-level requires_klt version pin, so --check --rerun silently tolerates a tool upgrade mid-sweep`

## F10. `memory_mapping` reports `none_detected` for an array the front end already flattened

`upsample_engine` holds `reg signed [23:0] mem [0:1763]` (written on a column
load, read combinationally). Yosys' `read_verilog` replaced it with registers
(`Warning: Replacing memory \mem with list of registers.`, counted in
`warnings.total = 1`), so the later `elaborated`/`pre_lowering` stages see no
memory and klt reports `memory_mapping.status: "none_detected"`, while the
mapped netlist holds 42,336 `dffq_1` and 42,382 `mux2_1`. The evidence field
therefore under-reports exactly the case a memory-macro decision needs.

* Wanted: count the `Replacing memory ... with list of registers` front-end
  warning (module, name) as a fourth memory stage, e.g.
  `status: "replaced_by_frontend"`, with the array bit count.

**Status (2026-11-14, `klt 0.9.2.dev1022+g2a9c52b27ce7`): still present.** The
status vocabulary is now an explicit, documented-as-fixed tuple
(`synthesize_memory.py:53-61`):

```
none_detected, lowered_to_logic, optimized_away, surviving, mixed, indeterminate
```

There is no `replaced_by_frontend` (or any fourth stage), and nothing in the
package parses the front-end warning the item names
(`grep -rn 'Replacing memory\|list of registers'` over every `.py`: no match).
`classify_module()` (`:203-234`) still decides from the same three counts
(`elaborated`, `pre_lowering`, `final`), so an array the front end replaced
before `elaborated` is still counted as zero at every stage and folded to
`none_detected` at `:226` and `:249-251`. The under-report is unchanged.

* Paste-ready upstream title:
  `klt synthesize: memory_mapping reports none_detected for an array Yosys' read_verilog replaced with registers — add a replaced_by_frontend status from the front-end warning`

## F11. `klt sta` over a large netlist is serial and slow

`klt sta` on the 165,732-cell `upsample_engine` netlist with three
`pdk.corners` took 968 s (one OpenROAD session per corner, run back to back,
each re-reading the netlist and liberty). Corners are independent.

* Wanted: concurrent corner sessions with a documented cap (the shared worker
  forbids more than 2), or one session that swaps liberties.

**Status (2026-11-14, `klt 0.9.2.dev1022+g2a9c52b27ce7`): not re-checked.** The
968 s figure is a measurement over a three-corner `klt sta` run on the
165,732-cell netlist; re-measuring it means re-running `klt sta` on an engine,
which this re-check's scope forbids. It is therefore **not** restated as
current, and no new timing number is claimed.

The *structural* half of the item was settled cheaply, and is recorded here
rather than guessed at: the corner loop is still strictly serial, so the
feature the item asks for still does not exist.

* `_run_multi_corner` (`post_route_sta.py:1456-1604`) is a plain `for` over
  the requested corners, calling `_run_corner_session(...)` once per corner and
  appending to `corner_entries` — no concurrency of any kind.
* `grep -rn 'ThreadPool\|ProcessPool\|concurrent.futures\|max_workers\|parallel'`
  over `post_route_sta.py` and `sta.py`: **no match**. `klt sta --help` (re-run
  2026-11-14) exposes only `--pdk`, `--pdk-root` and `--format` — no jobs or
  concurrency flag, and no request field for one.
* The serial design is deliberate and documented (`post_route_sta.py:200-215`):
  N separate engine sessions rather than one shared
  `define_corners`/`read_liberty -corner` session. So the "one session that
  swaps liberties" alternative has an upstream rationale against it, and the
  "concurrent sessions with a cap" alternative is the one worth filing.

* Paste-ready upstream title (safe to file — the serial structure is verified,
  only the runtime number is unverified):
  `klt sta: run independent pdk.corners sessions concurrently under a documented cap, instead of strictly back to back`

## F12. The input-mutation guard discards a finished run on a byte-identical rewrite

`klt synthesize` fails a finished run ("synthesis input ... changed during
execution; refusing to attribute outputs to an inconsistent input state") when
any declared source's size/mtime/ctime moved, including a byte-identical
rewrite by a regenerating harness. That is documented and defensible, but it
turned a completed 30-minute Yosys run into a total loss here. The message
names the file but not what changed (before/after stat), and the already
written netlist/stats stay in the run directory with no supported way to
re-attribute them. A `--keep-on-input-touch` diagnostic mode, or hashing
content instead of stat, would avoid the loss. The harness now rewrites derived
sources only when their bytes differ.

**Status (2026-11-14, `klt 0.9.2.dev1022+g2a9c52b27ce7`): still present**, in
every particular:

* The guard is still **stat-based, not content-based**. `_input_file_state`
  (`synthesize.py:1714-1728`) returns
  `(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)`, and its docstring
  still states ctime is included deliberately so it "catches write-then-restore
  and replacement with equal bytes" — i.e. a byte-identical rewrite is still a
  failure by design.
* The **message is unchanged** and still names only the file:
  `synthesis input '<path>' changed during execution; refusing to attribute
  outputs to an inconsistent input state` (`_assert_inputs_unchanged`,
  `:1730-1741`). No before/after stat is reported.
* **No diagnostic escape hatch.**
  `grep -rn 'keep_on_input_touch\|keep-on-input-touch'` over the package: no
  match; `klt synthesize --help` (re-run 2026-11-14) has no such flag. There is
  still no supported way to re-attribute the netlist/stats already written into
  the run directory.

* Paste-ready upstream title:
  `klt synthesize: input-mutation guard discards a finished run on a byte-identical rewrite — hash content, or add a --keep-on-input-touch diagnostic mode that reports the before/after stat`

## Removable workarounds

What the re-check above implies for the workarounds this repository carries.
**Nothing is removed in this PR** — this section only records what has become
removable, and at which `klt` version, so a later change can act on it.

A workaround is only safely removable when the version this repo *runs* has
the fix. That is the CI pin, `klayout-tools==0.8.0`
(`.github/workflows/tb-sim.yml:80,96`) — not the newer host tool. Both rows
below were therefore confirmed against `0.8.0` in a throwaway
`uv run --with "klayout-tools==0.8.0"` environment on 2026-11-14, not inferred
from the host `klt`.

| Workaround | Item | Fixed at host `0.9.2.dev`? | Present at CI pin `0.8.0`? | Removable now? |
|---|---|---|---|---|
| `tools/synth_view.py` package -> `` `define `` rewrite | F1 | yes | **yes** (`read_verilog -sv foo.sv`) | **yes** |
| `tools/synthesize_oneshot.py:313-348` Yosys-timeout monkeypatch | F2 | yes | **no** (`yosys_timeout_s` not in the `0.8.0` allowlist) | **not yet** — only after the pin moves |
| `tools/synthesize_oneshot.py:240-254` `reap_orphan_abc()` | F3 | no (still present) | n/a | **no** |

* **F1 / `tools/synth_view.py` — removable now, with one caveat.** `0.8.0`
  already emits `read_verilog -sv` for a `.sv`/`.svh` source, and this repo's
  RTL is `.sv` (`tb/sv/*.sv`, plus `gf180_patch_table.svh`), so the *syntax*
  reason for the package-to-`` `define `` rewrite is gone. The caveat: that
  rewrite is only **reason 1** of the view's two jobs — reason 2 (injecting the
  `quarter_wave_lut` / `lfo_vca_engine` ROM payloads, `tools/synth_view.py:16`)
  is not klt friction and is unaffected. So this is a *narrowing* of
  `synth_view.py`, not a deletion of it, and it changes the derived-source
  bytes that the #82 evidence record hashes — which is why it belongs in its
  own issue with its own re-attestation, not here.
* **F2 / `tools/synthesize_oneshot.py` monkeypatch — hold.** The fix is real at
  the host `klt` but absent at `0.8.0`, so removing the monkeypatch now would
  break the pinned CI path. It becomes removable once the pin moves (that bump
  is #467's scope, not this issue's), and the replacement is a one-line
  request field — set top-level `"yosys_timeout_s": <N>` in the generated
  request (`tools/synthesize_oneshot.py:295-307`) instead of patching
  `_run_yosys.__kwdefaults__` under klt's own interpreter. Note the ceiling:
  `YOSYS_TIMEOUT_MAX_S = 3600.0`, so a request cannot ask for more than an
  hour per Yosys invocation.
* **F3 / `reap_orphan_abc()` — keep.** F3 is still present at the host `klt`,
  so the reaper is still load-bearing on a shared worker. It is called after
  every klt invocation (`tools/synthesize_oneshot.py:345`) and its count is
  reported as `reaped_orphan_abc`.
