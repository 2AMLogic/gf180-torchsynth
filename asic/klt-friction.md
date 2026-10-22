# Generic `klt` friction found by issue #82 (ready for 2AMLogic/klayout-tools)

Everything in this file is about `klt` itself, not about this repository's
RTL. Each item is stated so it can be pasted into an upstream issue: observed
behaviour, minimal reproduction, and the workaround used here. Observed with
`klt 0.7.1.dev427` / `dev429` (the shared worker's `klt` was upgraded while
the sweep ran; per-response `provenance.klt_version` is recorded in the
evidence record), Yosys 0.67+post, OpenROAD 26Q3-1510, `gf180mcuD`
(open_pdks `f6eeac7d`), `gf180mcu_fd_sc_mcu7t5v0`.

Not yet filed upstream; the PR for #82 names this file.

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

## F3. Timeout kills `yosys` but leaves its `yosys-abc` child running

After the 300 s kill, `yosys-abc -s <abc.script>` kept running at 100 % CPU,
re-parented to the user manager (observed 6+ minutes after the kill, still
consuming a core on a shared 8-vCPU worker). `subprocess.run(timeout=)`
kills only the direct child.

* Wanted: run Yosys in its own process group (`start_new_session=True`) and
  `killpg` on timeout.
* Workaround here: the harness reaps `yosys-abc` processes whose cwd is the
  repo and whose parent is not a `yosys` process, after every klt call.

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

## F6. `sta` in `klt synthesize` is `null` on the shared worker

`"sta": null, "sta_unavailable_reason": "the optional klt_statime_native
extension is not installed ..."`. The extension is not part of the uv tool
install on the worker, so every response carries only ABC's wire-free
`stime` number (which the docs say is *never slack*). This repo therefore
uses `klt sta` (OpenSTA, `verilog` mode) for timing. Worker-spec item, not a
code bug; noted so the provisioning owner can decide.

## F7. `klt sta` `verilog` mode fills the response `deck` with `null` for `pdk.corners`

For a `pdk.corners` request the top-level `provenance.deck` is `null` (by
design, documented) and each `corners[]` entry carries its own `deck`; a
consumer that only reads `provenance.deck.content_hash` sees no liberty
identity. Harmless but easy to miss; a top-level `decks[]` summary would make
committed records self-describing.

## F8. No hierarchy view in the response

`klt synthesize` reports one aggregate `instance_count` / `area_um2` /
`instance_counts_by_type`. Per-module area (needed for "cell area by
hierarchy") has to be re-read from the retained `<top>_stats.json`
(`modules.<name>.num_cells` / `.area`), whose schema is Yosys's, not klt's.
A `hierarchy[]` response field (module, cells, area, instance count) would
make that a contract rather than a convention.

## F9. Reproducibility: `klt --version` moved during one sweep

`klt 0.7.1.dev427+ga7e9b9cb01c9` at the start of the run and
`0.7.1.dev429+g435837f5c237` in the later `klt sta` responses on the same
worker (tool upgraded in place by provisioning). Nothing in a request pins the
required klt version, so a `--check --rerun` can legitimately drift. A
`requires_klt` request field (or `klt synthesize --require-version`) would
turn silent drift into a hard error.

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

## F11. `klt sta` over a large netlist is serial and slow

`klt sta` on the 165,732-cell `upsample_engine` netlist with three
`pdk.corners` took 968 s (one OpenROAD session per corner, run back to back,
each re-reading the netlist and liberty). Corners are independent.

* Wanted: concurrent corner sessions with a documented cap (the shared worker
  forbids more than 2), or one session that swaps liberties.

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
