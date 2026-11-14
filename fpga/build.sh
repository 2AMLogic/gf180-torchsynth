#!/usr/bin/env bash
# FPGA feasibility-measurement build (DR-0013, issue #80).
#
#   fpga/build.sh [stage ...]     stages: env stage synth engines pnr report (default: all)
#
# Everything is written under fpga/build/ (git-ignored); the small evidence
# files are copied to fpga/reports/. Single-threaded by design (one yosys,
# one nextpnr; read_slang -j 1; nextpnr --threads 1). Device: LFE5U-85F,
# CABGA381, speed 6. Clock constraint: FREQ_MHZ (default 25).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
B="$HERE/build"
VENV="$B/venv"
SRC="$B/src"
OUT="$B/out"
REP="$HERE/reports"
FREQ_MHZ="${FREQ_MHZ:-25}"
PY="$VENV/bin/python"
RUN="$PY -W ignore $HERE/scripts/yowasp_bigstack.py"
SOURCES="gf180_rtl_constants_pkg.sv adsr_engine.sv lfo_vca_engine.sv mod_matrix_engine.sv upsample_engine.sv quarter_wave_lut.sv sine_vco_engine.sv square_saw_vco_engine.sv noise_stream_dut.sv audio_mix_engine.sv normalization_replay_engine.sv one_shot_voice_top.sv fpga_oneshot_top.sv"
TOP=fpga_oneshot_top
STAGES=("$@"); [ ${#STAGES[@]} -eq 0 ] && STAGES=(env stage synth engines pnr report)
want() { local s; for s in "${STAGES[@]}"; do [ "$s" = "$1" ] && return 0; done; return 1; }

mkdir -p "$B" "$OUT" "$REP"

if want env; then
  [ -x "$PY" ] || uv venv --python 3.14 "$VENV"
  uv pip install --python "$PY" -r "$HERE/requirements.txt"
fi

if want stage; then
  python3 "$HERE/scripts/gen_luts.py" "$SRC" > "$OUT/luts.json"
  python3 "$HERE/scripts/prepare_sources.py" "$SRC" > "$OUT/sources.json"
  cp "$HERE/rtl/fpga_oneshot_top.sv" "$SRC/"
fi

if want synth; then
  cd "$SRC"
  READ="read_slang -j 1 --top $TOP $SOURCES"
  # synth_ecp5 always runs abc9 in map_luts; it had not finished after 590 s
  # on this design, so map_luts is replaced by plain `abc -lut 4:7`.
  MAPLUTS="check -latchonly -assert; techmap -map +/lattice/latches_map.v; abc -lut 4:7 -dress; clean"
  # (a) flat, for resource totals and the netlist handed to nextpnr
  $RUN yosys -q -l "$OUT/synth_flat.log" -p "$READ; synth_ecp5 -top $TOP -run :map_luts; $MAPLUTS; synth_ecp5 -top $TOP -run map_cells: -json $OUT/$TOP.json; tee -o $OUT/stat_flat.txt stat" >/dev/null 2>"$OUT/synth_flat.stderr"
fi

if want engines; then
  cd "$SRC"
  MAPLUTS="check -latchonly -assert; techmap -map +/lattice/latches_map.v; abc -lut 4:7 -dress; clean"
  ENGSRC="${SOURCES% one_shot_voice_top.sv fpga_oneshot_top.sv}"
  # per-engine bottleneck table. A -noflatten run of the whole top was
  # killed after ~10 min (4 GiB wasm limit), so each engine is synthesized
  # standalone with the same flow; instance counts come from one_shot_voice_top.
  : > "$OUT/stat_engines.txt"
  for spec in adsr_engine:6 lfo_vca_engine:2 mod_matrix_engine:1 upsample_engine:5 \
              sine_vco_engine:1 square_saw_vco_engine:1 noise_stream_dut:1 \
              audio_mix_engine:1 normalization_replay_engine:1; do
    eng="${spec%%:*}"; n="${spec##*:}"
    $RUN yosys -q -l "$OUT/synth_$eng.log" -p "read_slang -j 1 --top $eng $ENGSRC; synth_ecp5 -top $eng -run :map_luts; $MAPLUTS; synth_ecp5 -top $eng -run map_cells:; tee -o $OUT/stat_$eng.txt stat" >/dev/null 2>"$OUT/synth_$eng.stderr" || echo "FAILED $eng" >> "$OUT/stat_engines.txt"
    echo "### $eng x$n" >> "$OUT/stat_engines.txt"
    cat "$OUT/stat_$eng.txt" >> "$OUT/stat_engines.txt" 2>/dev/null || true
  done
fi

if want pnr; then
  cd "$OUT"
  set +e
  timeout 3000 $RUN nextpnr-ecp5 --85k --package CABGA381 --speed 6 --freq "$FREQ_MHZ" \
    --threads 1 --seed 1 --json "$OUT/$TOP.json" --report "$OUT/pnr_report.json" \
    --log "$OUT/pnr.log" --textcfg "$OUT/$TOP.cfg" >/dev/null 2>"$OUT/pnr.stderr"
  echo "nextpnr exit code: $?" > "$OUT/pnr.rc"
  set -e
fi

if want report; then
  python3 "$HERE/scripts/make_report.py" "$ROOT" "$OUT" "$SRC" "$REP" "$FREQ_MHZ"
fi
