"""Issue #465: static sample-identity checks for the synthesis-view rewrites.

``tools/synth_view.py`` applies four rewrites to ``tb/sv/*.sv`` before
``klt synthesize`` reads them (see that module's docstring and
``asic/whole-voice-feasibility-plan.md`` "Sample-identity gap"). The #79
bit-exactness evidence covers ``tb/sv``, *not* the derived view, so nothing
committed proved the rewrites value-preserving:
``tests/test_synth_feasibility_evidence.py`` checks that each rewrite matches
exactly once and that the regenerated view reproduces the recorded hashes,
which is a *freshness* argument, not an identity one.

This suite supplies the cheapest falsifiable check named for each class in
the plan's sample-identity table. **No simulator, no ``klt``, no Yosys, no
PDK, and no evidence-record write**: everything here is Python over the
committed sources plus a view regenerated into a temporary directory.

What each class establishes, stated no more strongly than it is:

* **Class 1** (``TestClass1MacroValuesMatchThePackage``) -- every
  `` `define `` the view emits carries the same integer as the matching
  ``localparam int`` in the package, and every package constant the view
  leaves *undefined* (``C2_UNITS_PER_TURN``, plus the 15 ``localparam
  string`` constants) is used by none of the 12 derived sources. The two
  sides are parsed independently: this file writes its own patterns and
  never calls ``synth_view.package_ints`` / ``_package_to_defines``, because
  those two *derive* one side from the other and comparing them would be a
  function checked against itself.
* **Class 2** (``TestClass2RomPayloadMatchesTheLaneProducer``) -- the ROM
  payload bytes ``synth_view.build`` writes are byte-identical to what the
  whole-voice simulation lane writes. This is a **regression lock against
  future divergence, not evidence of two independent producers**: both sides
  reach the tables through the same ``run_tb.mix_load_receipt()`` /
  ``FixedControlPath(formats.control_spec)`` expressions and both serialize
  through the same ``run_tb.write_lut_memh``. It bites when one of the two
  call sites drifts; it cannot detect a wrong table that both share.
* **Class 3** (``TestClass3LaneNeverPassesMaxJ``) -- the whole-voice lane
  ``tb/run_voice.py`` never passes ``+max_j`` at all (the plan's wording is
  weaker: "not for non-mutation cases"), the only producer of that plusarg
  anywhere under ``tb/`` is ``tb/run_tb.py``'s mutation path, and the rewrite
  removes exactly the consumer that reads it.
* **Class 4** (``TestClass4DivisionLoopGuardIsIdentity``) -- a software model
  of both division-loop forms agrees over the whole denominator range. Two
  honesty notes: the SystemVerilog functions return the **quotient only**
  (``rem`` is function-local), so comparing remainders is a free
  strengthening of the *model*, not a check of an RTL-observable output; and
  the identity is **one-sided** -- a constant start larger than needed is
  still identity, so the negative control has to *shrink* the constant and
  must drive numerators that set bit ``den_msb + 60`` at the maximal
  ``den_msb``, or it would pass and make the class vacuous.

Every class carries a negative control that mutates a *copy* of an input (a
macro value, payload bytes, the lane's text, the loop constant) and asserts
the check refuses it. No committed file is ever written.
"""

import hashlib
import random
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tb"))

import synth_view  # noqa: E402

PKG_SV = synth_view.SV / synth_view.PKG
PLAN = ROOT / "asic" / "whole-voice-feasibility-plan.md"

#: Fixed seed: every randomized operand draw below is reproducible.
SEED = 465

#: The test classes the plan's sample-identity table must name, in row order.
CLASS_TESTS = (
    "TestClass1MacroValuesMatchThePackage",
    "TestClass2RomPayloadMatchesTheLaneProducer",
    "TestClass3LaneNeverPassesMaxJ",
    "TestClass4DivisionLoopGuardIsIdentity",
)


def build_view(outdir: Path) -> dict:
    """Regenerate the synthesis view into ``outdir``; return its hash ledger."""
    return synth_view.build(outdir)


# ---------------------------------------------------------------------------
# Class 1 -- macro values versus the package's localparams
#
# Patterns written here on purpose: ``synth_view._package_to_defines``
# derives its output from ``synth_view.package_ints`` through one shared
# regex, so reusing either would compare that regex against itself.
# ---------------------------------------------------------------------------
_LOCALPARAM_INT = re.compile(r"^\s*localparam\s+int\s+(\w+)\s*=\s*(-?\d+)\s*;", re.M)
_LOCALPARAM_STRING = re.compile(r"^\s*localparam\s+string\s+(\w+)\s*=", re.M)
_DEFINE = re.compile(r"^`define\s+(\w+)\s+(-?\d+)\s*$", re.M)
_MACRO_USE = re.compile(r"`(\w+)")

#: Range of a 32-bit SystemVerilog ``int`` -- the reason a constant is dropped.
INT32_MIN, INT32_MAX = -2 ** 31, 2 ** 31 - 1


def package_int_localparams(pkg_text: str) -> dict:
    """``{name: value}`` for every ``localparam int`` of the package."""
    return {name: int(value) for name, value in _LOCALPARAM_INT.findall(pkg_text)}


def package_string_localparams(pkg_text: str) -> set:
    """Names of every ``localparam string`` of the package."""
    return set(_LOCALPARAM_STRING.findall(pkg_text))


def view_defines(view_pkg_text: str) -> dict:
    """``{name: value}`` for every `` `define `` the view's package file emits."""
    return {name: int(value) for name, value in _DEFINE.findall(view_pkg_text)}


def check_macro_values(pkg_text: str, view_pkg_text: str) -> list:
    """Problems where a macro value disagrees with its ``localparam``.

    Also refuses a *missing* or *extra* macro: the only integer constants
    allowed to be undefined are the ones outside the 32-bit ``int`` range
    (``synth_view._package_to_defines``'s documented reason for dropping
    ``C2_UNITS_PER_TURN``).
    """
    problems = []
    declared = package_int_localparams(pkg_text)
    defined = view_defines(view_pkg_text)
    if not declared:
        problems.append("no localparam int found in the package")
    if not defined:
        problems.append("no `define found in the view's package file")
    for name, value in sorted(defined.items()):
        if name not in declared:
            problems.append("%s: defined by the view, not declared by the package" % name)
        elif declared[name] != value:
            problems.append("%s: view `define %d != package localparam %d"
                            % (name, value, declared[name]))
    for name, value in sorted(declared.items()):
        fits = INT32_MIN <= value <= INT32_MAX
        if fits and name not in defined:
            problems.append("%s: fits a 32-bit int but the view left it undefined" % name)
        if not fits and name in defined:
            problems.append("%s: outside the 32-bit int range but the view defined it" % name)
    return problems


def check_undefined_constants_unused(pkg_text: str, view_pkg_text: str,
                                     derived_texts: dict) -> list:
    """Problems where a view source uses a package constant left undefined.

    ``derived_texts`` maps file name to text for the **derived sources** --
    the 12 files in ``synth_view.SOURCE_ORDER``. The 19 testbench/DUT files
    in ``tb/sv`` are not part of the view and are deliberately not counted.

    Scanning the generated view (rather than grepping ``tb/sv``) is what
    makes this falsifiable: ``synth_view._macro_ize`` builds its substitution
    pattern from *all* package integers, including the dropped one, so a use
    of a dropped constant surfaces in the view as a backtick-prefixed macro
    with no `` `define `` behind it.
    """
    problems = []
    defined = set(view_defines(view_pkg_text))
    constants = set(package_int_localparams(pkg_text)) | package_string_localparams(pkg_text)
    undefined = constants - defined
    if not undefined:
        problems.append("no package constant is left undefined; the check is vacuous")
    for name, text in sorted(derived_texts.items()):
        for used in sorted(set(_MACRO_USE.findall(text)) & constants):
            if used not in defined:
                problems.append("%s: uses `%s, which the view leaves undefined"
                                % (name, used))
        for plain in sorted(undefined):
            if re.search(r"(?<![\w`])%s\b" % re.escape(plain), text):
                problems.append("%s: names %s, which the view leaves undefined"
                                % (name, plain))
    return problems


class TestClass1MacroValuesMatchThePackage(unittest.TestCase):
    """Rewrite class 1: constants package -> `` `define `` macros."""

    @classmethod
    def setUpClass(cls):
        cls.pkg = PKG_SV.read_text(encoding="utf-8")
        cls.derived = {}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "src"
            build_view(out)
            for name in synth_view.SOURCE_ORDER:
                cls.derived[name] = (out / name).read_text(encoding="utf-8")
        cls.view_pkg = cls.derived[synth_view.PKG]

    def test_every_macro_carries_its_localparam_value(self):
        self.assertEqual(check_macro_values(self.pkg, self.view_pkg), [])

    def test_only_the_out_of_range_constant_is_left_undefined(self):
        declared = package_int_localparams(self.pkg)
        defined = view_defines(self.view_pkg)
        self.assertEqual(sorted(set(declared) - set(defined)), ["C2_UNITS_PER_TURN"])
        self.assertFalse(INT32_MIN <= declared["C2_UNITS_PER_TURN"] <= INT32_MAX)

    def test_undefined_constants_are_used_by_no_derived_source(self):
        derived = {n: t for n, t in self.derived.items() if n != synth_view.PKG}
        self.assertEqual(len(derived), len(synth_view.SOURCE_ORDER) - 1)
        self.assertEqual(
            check_undefined_constants_unused(self.pkg, self.view_pkg, derived), [])

    def test_the_check_sees_the_constants_it_claims_to(self):
        """Guard against a silently-empty comparison."""
        self.assertEqual(len(package_int_localparams(self.pkg)), 33)
        self.assertEqual(len(package_string_localparams(self.pkg)), 15)
        self.assertEqual(len(view_defines(self.view_pkg)), 32)

    # ---- negative controls -------------------------------------------
    def test_a_wrong_macro_value_is_refused(self):
        bad = self.view_pkg.replace("`define C1_WIDTH 24", "`define C1_WIDTH 25")
        self.assertNotEqual(bad, self.view_pkg)
        self.assertTrue(any("C1_WIDTH" in p for p in check_macro_values(self.pkg, bad)))

    def test_a_dropped_macro_is_refused(self):
        bad = re.sub(r"^`define C1_WIDTH 24\n", "", self.view_pkg, flags=re.M)
        self.assertNotEqual(bad, self.view_pkg)
        self.assertTrue(any("undefined" in p for p in check_macro_values(self.pkg, bad)))

    def test_defining_the_out_of_range_constant_is_refused(self):
        value = package_int_localparams(self.pkg)["C2_UNITS_PER_TURN"]
        bad = self.view_pkg + "`define C2_UNITS_PER_TURN %d\n" % value
        self.assertTrue(any("C2_UNITS_PER_TURN" in p
                            for p in check_macro_values(self.pkg, bad)))

    def test_a_use_of_an_undefined_constant_is_refused(self):
        for injected in ("    wire [`C2_UNITS_PER_TURN-1:0] oops;\n",
                         "    // C5_METHOD\n"):
            with self.subTest(injected=injected.strip()):
                bad = {"adsr_engine.sv": self.derived["adsr_engine.sv"] + injected}
                self.assertTrue(
                    check_undefined_constants_unused(self.pkg, self.view_pkg, bad))


# ---------------------------------------------------------------------------
# Class 2 -- ROM payload bytes versus the simulation lane's producer
# ---------------------------------------------------------------------------
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_payload_hashes(ledger_hashes: dict, lane_hashes: dict) -> list:
    """Problems where the view's payload hash differs from the lane's."""
    problems = []
    if set(ledger_hashes) != set(lane_hashes):
        problems.append("payload sets differ: %s vs %s"
                        % (sorted(ledger_hashes), sorted(lane_hashes)))
    for name in sorted(set(ledger_hashes) & set(lane_hashes)):
        if ledger_hashes[name] != lane_hashes[name]:
            problems.append("%s: view %s != lane %s"
                            % (name, ledger_hashes[name], lane_hashes[name]))
    return problems


class TestClass2RomPayloadMatchesTheLaneProducer(unittest.TestCase):
    """Rewrite class 2: plusarg ``$readmemh`` -> a fixed ``$readmemh``.

    **This is a regression lock, not an independent derivation.** The view
    (``synth_view.build``) and the whole-voice lane (``tb/run_voice.py``)
    reach the two quarter-wave tables through the *same* expressions --
    ``run_tb.mix_load_receipt()`` and
    ``FixedControlPath(formats.control_spec)`` (``tb/run_voice.py:1152``,
    ``:1157-1159``) -- and both serialize through the *same*
    ``run_tb.write_lut_memh`` (``tb/tb_harness.py:60-68``; the lane calls it
    at ``tb/run_voice.py:829-830``). So agreement here proves the two call
    sites have not drifted apart; it does **not** prove the payload correct,
    and it cannot detect an error the shared producer makes. The tie from
    the *record* to the ledger already exists elsewhere
    (``tests/test_synth_feasibility_evidence.py:253`` asserts
    ``ledger["memh_sha256"] == identity["rom_payload_sha256"]``); the gap
    this closes is the tie to the lane's producer.

    No simulation is started: ``run_voice.voice()`` is never called, only its
    table-loading expressions and its ``write_lut_memh`` re-export.
    """

    @classmethod
    def setUpClass(cls):
        import run_voice
        from torchsynth_voice.format_sweep import FixedControlPath

        _receipt, formats, _cases = run_voice.mix_load_receipt()
        # Exactly what ``run_voice.voice`` does before it simulates
        # (tb/run_voice.py:1158-1159), so ``_table`` resolves the way the
        # lane's own ``write_lut_memh`` calls at :829-830 resolve it.
        run_voice._LUT_TABLES["audio"] = formats.table
        run_voice._LUT_TABLES["control"] = FixedControlPath(formats.control_spec).table

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "src"
            cls.ledger = build_view(out)
            cls.lane_text = {}
            for which, memh in (("audio", synth_view.AUDIO_MEMH),
                                ("control", synth_view.CONTROL_MEMH)):
                path = Path(tmp) / ("lane_" + memh)
                run_voice.write_lut_memh(run_voice._table(which), path)
                cls.lane_text[memh] = path.read_text(encoding="utf-8")
            cls.view_text = {memh: (out / memh).read_text(encoding="utf-8")
                             for memh in cls.lane_text}
        cls.lane_hashes = {memh: sha256_bytes(text.encode("utf-8"))
                           for memh, text in cls.lane_text.items()}
        cls.entry_count = package_int_localparams(
            PKG_SV.read_text(encoding="utf-8"))["C5_N_ENTRIES"]

    def test_the_view_writes_the_lane_payload_byte_for_byte(self):
        self.assertEqual(check_payload_hashes(self.ledger["memh_sha256"],
                                              self.lane_hashes), [])
        for memh, text in sorted(self.lane_text.items()):
            self.assertEqual(self.view_text[memh], text, memh)

    def test_both_payloads_are_covered_and_distinct(self):
        self.assertEqual(sorted(self.lane_hashes),
                         sorted([synth_view.AUDIO_MEMH, synth_view.CONTROL_MEMH]))
        self.assertNotEqual(self.lane_hashes[synth_view.AUDIO_MEMH],
                            self.lane_hashes[synth_view.CONTROL_MEMH])

    def test_the_payload_carries_the_endpoint_word_after_the_entries(self):
        for memh, text in sorted(self.lane_text.items()):
            lines = text.splitlines()
            self.assertEqual(len(lines), self.entry_count + 1, memh)

    # ---- negative controls -------------------------------------------
    def test_a_flipped_payload_nibble_is_refused(self):
        for memh in sorted(self.lane_text):
            with self.subTest(memh=memh):
                lines = self.lane_text[memh].splitlines()
                word = lines[7]
                lines[7] = word[:-1] + ("0" if word[-1] != "0" else "1")
                mutated = "\n".join(lines) + "\n"
                self.assertNotEqual(mutated, self.lane_text[memh])
                bad = dict(self.lane_hashes)
                bad[memh] = sha256_bytes(mutated.encode("utf-8"))
                self.assertTrue(any(memh in p for p in check_payload_hashes(
                    self.ledger["memh_sha256"], bad)))

    def test_dropping_the_endpoint_word_is_refused(self):
        memh = synth_view.AUDIO_MEMH
        lines = self.lane_text[memh].splitlines()[:-1]
        bad = dict(self.lane_hashes)
        bad[memh] = sha256_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        self.assertTrue(check_payload_hashes(self.ledger["memh_sha256"], bad))

    def test_a_missing_payload_is_refused(self):
        bad = dict(self.lane_hashes)
        del bad[synth_view.CONTROL_MEMH]
        self.assertTrue(check_payload_hashes(self.ledger["memh_sha256"], bad))


# ---------------------------------------------------------------------------
# Class 3 -- the whole-voice lane never passes +max_j
# ---------------------------------------------------------------------------
LANE_PY = synth_view.TB / "run_voice.py"
MUTATION_LANE_PY = synth_view.TB / "run_tb.py"
UPSAMPLE_SV = synth_view.SV / "upsample_engine.sv"

#: The plusarg as it reaches a simulator argv, and the bare identifier.
PLUSARG = "+max_j"
IDENTIFIER = "max_j"


def check_lane_never_names_max_j(lane_text: str) -> list:
    """Problems where the whole-voice lane mentions the walk cap at all.

    Stronger than the plan's "none should set it for non-mutation cases":
    ``tb/run_voice.py`` names ``max_j`` nowhere, so there is no case -- not
    even a conditional one -- in which the lane caps the walk.
    """
    problems = []
    for lineno, line in enumerate(lane_text.splitlines(), 1):
        if IDENTIFIER in line:
            problems.append("line %d names %s: %s" % (lineno, IDENTIFIER, line.strip()))
    return problems


def files_naming_the_plusarg(root: Path) -> set:
    """Names of the ``tb/*.py`` files that put ``+max_j`` on an argv."""
    return {path.name for path in sorted(root.glob("*.py"))
            if PLUSARG in path.read_text(encoding="utf-8")}


class TestClass3LaneNeverPassesMaxJ(unittest.TestCase):
    """Rewrite class 3: the ``+max_j`` plusarg -> a constant ``32'd0``."""

    @classmethod
    def setUpClass(cls):
        cls.lane = LANE_PY.read_text(encoding="utf-8")
        cls.mutation_lane = MUTATION_LANE_PY.read_text(encoding="utf-8")
        cls.upsample = UPSAMPLE_SV.read_text(encoding="utf-8")
        names = [n for n in package_int_localparams(
            PKG_SV.read_text(encoding="utf-8"))]
        cls.derived_upsample = synth_view.transform(
            "upsample_engine.sv", cls.upsample, "asic/build/src", names)

    def test_the_whole_voice_lane_never_names_the_walk_cap(self):
        self.assertEqual(check_lane_never_names_max_j(self.lane), [])

    def test_the_only_producer_under_tb_is_the_mutation_lane(self):
        self.assertEqual(files_naming_the_plusarg(synth_view.TB),
                         {MUTATION_LANE_PY.name})
        self.assertIn('"+max_j=%d" % max_j', self.mutation_lane)
        self.assertIn("MM_MUTATION_WALK_CAP", self.mutation_lane)

    def test_the_rewrite_removes_exactly_the_plusarg_consumer(self):
        """Ties the lane's silence to the rewrite rather than to a grep."""
        self.assertIn('$value$plusargs("max_j=%d", max_j)', self.upsample)
        self.assertIn("max_j != 32'd0", self.upsample)
        self.assertNotIn("$value$plusargs", self.derived_upsample)
        self.assertIn("wire [31:0] max_j = 32'd0;", self.derived_upsample)
        # The cap term survives textually but is now constant-false, so the
        # synthesized walk is the full walk the committed top runs.
        self.assertIn("max_j != 32'd0", self.derived_upsample)
        self.assertIn("upsample_engine.sv", synth_view.SOURCE_ORDER)

    # ---- negative controls -------------------------------------------
    def test_a_spliced_in_plusarg_is_refused(self):
        bad = self.lane.replace(
            '    vvp = workdir / "voice.vvp"',
            '    vvp_cmd.append("+max_j=%d" % 64)\n    vvp = workdir / "voice.vvp"')
        self.assertNotEqual(bad, self.lane)
        self.assertTrue(check_lane_never_names_max_j(bad))

    def test_even_a_commented_out_cap_is_refused(self):
        self.assertTrue(check_lane_never_names_max_j(
            self.lane + "\n# max_j = 64  # debug cap\n"))

    def test_the_producer_scan_notices_a_new_producer(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "run_other.py"
            fake.write_text('cmd.append("+max_j=8")\n', encoding="utf-8")
            (Path(tmp) / "quiet.py").write_text("pass\n", encoding="utf-8")
            self.assertEqual(files_naming_the_plusarg(Path(tmp)), {"run_other.py"})


# ---------------------------------------------------------------------------
# Class 4 -- both division-loop forms are arithmetically identical
# ---------------------------------------------------------------------------
_FUNC_WIDTH = re.compile(r"function\s+\[(\d+):0\]\s+div_half_even\s*;")
_DEN_WIDTH = re.compile(r"input\s+\[(\d+):0\]\s+denominator\s*;")
_NUM_WIDTH = re.compile(r"input\s+\[(\d+):0\]\s+numerator\s*;")
_GUARDED_LOOP = re.compile(
    r"for \(i = (\d+); i >= 0; i = i - 1\) if \(i <= den_msb \+ 60\) begin")
ORIGINAL_LOOP = "for (i = den_msb + 60; i >= 0; i = i - 1) begin"

#: ``den_msb + 60``: the caller clamps the quotient to 2^60 (see the
#: ``div_half_even`` comments in both engines), so the scan starts 60 bits
#: above the denominator's MSB.
QUOTIENT_HEADROOM = 60


def one(pattern, text, *, what):
    """The single capture of ``pattern`` in ``text``; refuse anything else."""
    found = pattern.findall(text)
    if len(found) != 1:
        raise AssertionError("%s matched %d times (expected exactly 1)"
                             % (what, len(found)))
    return found[0]


def engine_loop_parameters(engine: str) -> dict:
    """Read one engine's divider geometry from the committed RTL and the view.

    Nothing here is hardcoded: the register width, the denominator width and
    the rewritten loop's constant start all come out of the sources, so a
    widened denominator or a retuned constant changes what is asserted.
    """
    original = (synth_view.SV / engine).read_text(encoding="utf-8")
    names = list(package_int_localparams(PKG_SV.read_text(encoding="utf-8")))
    derived = synth_view.transform(engine, original, "asic/build/src", names)
    width = int(one(_FUNC_WIDTH, original, what="%s div_half_even width" % engine)) + 1
    num_bits = int(one(_NUM_WIDTH, original, what="%s numerator width" % engine)) + 1
    den_bits = int(one(_DEN_WIDTH, original, what="%s denominator width" % engine)) + 1
    start = int(one(_GUARDED_LOOP, derived, what="%s guarded loop start" % engine))
    if original.count(ORIGINAL_LOOP) != 1:
        raise AssertionError("%s: the original data-dependent loop moved" % engine)
    return {"engine": engine, "width": width, "numerator_bits": num_bits,
            "denominator_bits": den_bits, "start": start}


def div_half_even_loop(numerator: int, denominator: int, *, width: int,
                       denominator_bits: int, start=None) -> tuple:
    """Model one division-loop form; return the pre-tie ``(quo, rem)``.

    ``start=None`` models the committed RTL
    (``for (i = den_msb + 60; i >= 0; i = i - 1)``); an integer models the
    synthesis view (``for (i = start; i >= 0; i = i - 1) if (i <= den_msb +
    60)``). Every assignment is masked to ``width`` bits, matching the
    SystemVerilog registers (``rem``, ``quo``, ``den_z`` are all ``[width-1:0]``
    and the ``rem << 1`` shift truncates there).

    The remainder is returned because comparing it strengthens the *model*;
    the SystemVerilog functions expose only the quotient (``rem`` is a
    function-local reg), so an equal remainder is not an RTL-observable
    property. See :func:`div_half_even_tie` for the quotient the RTL returns.
    """
    mask = (1 << width) - 1
    num = numerator & mask
    den_z = denominator & ((1 << denominator_bits) - 1)
    quo = 0
    rem = 0
    den_msb = 0
    for i in range(denominator_bits):
        if (den_z >> i) & 1:
            den_msb = i
    top = den_msb + QUOTIENT_HEADROOM
    scan = range(top, -1, -1) if start is None else range(start, -1, -1)
    for i in scan:
        if start is not None and i > top:
            continue  # the ``if (i <= den_msb + 60)`` guard
        rem = ((rem << 1) | ((num >> i) & 1)) & mask
        if rem >= den_z:
            rem = (rem - den_z) & mask
            quo = (quo | (1 << i)) & mask
    return quo, rem


def div_half_even_tie(quo: int, rem: int, denominator: int, *, width: int) -> int:
    """The post-loop half-even correction -- the value the RTL returns.

    Runs on the same ``rem`` for both forms, so equal pre-tie pairs already
    imply equal returned quotients; this is asserted directly anyway because
    it is the only RTL-observable output.
    """
    mask = (1 << width) - 1
    doubled = (rem << 1) & mask
    if doubled > denominator:
        quo = (quo + 1) & mask
    elif doubled == denominator:
        quo = (quo + (quo & 1)) & mask
    return quo & mask


def operands(den_msb: int, *, numerator_bits: int, rng, extra: int = 6) -> tuple:
    """Boundary-plus-randomized denominators and numerators for one ``den_msb``.

    The numerators deliberately include ``1 << top`` and ``(1 << (top+1)) -
    1`` where ``top = den_msb + 60``: those are the only operands that reach
    the highest scanned bit, and without them a *shrunk* loop constant can
    agree with the original by never being asked about the bit it dropped --
    which would make the whole class vacuous.
    """
    top = den_msb + QUOTIENT_HEADROOM
    denominators = [1 << den_msb, (1 << (den_msb + 1)) - 1, (1 << den_msb) | 1]
    numerators = [0, 1, 1 << top, (1 << top) - 1, (1 << (top + 1)) - 1,
                  (1 << top) | 1]
    numerators += [rng.getrandbits(min(top + 1, numerator_bits))
                   for _ in range(extra)]
    return denominators, numerators


def compare_loop_forms(params: dict, *, start=None, den_msb_range=None,
                       seed: int = SEED) -> list:
    """Mismatches between the original loop and a constant-start guarded loop.

    Returns ``[(den_msb, denominator, numerator, original, guarded)]`` --
    empty means the two forms are identical over the operands tried.
    """
    width = params["width"]
    den_bits = params["denominator_bits"]
    num_bits = params["numerator_bits"]
    if start is None:
        start = params["start"]
    rng = random.Random(seed)
    mismatches = []
    for den_msb in (den_msb_range if den_msb_range is not None else range(den_bits)):
        denominators, numerators = operands(den_msb, numerator_bits=num_bits, rng=rng)
        for den in denominators:
            for num in numerators:
                base = div_half_even_loop(num, den, width=width,
                                          denominator_bits=den_bits)
                guarded = div_half_even_loop(num, den, width=width,
                                             denominator_bits=den_bits, start=start)
                base_q = div_half_even_tie(*base, den, width=width)
                guarded_q = div_half_even_tie(*guarded, den, width=width)
                if base != guarded or base_q != guarded_q:
                    mismatches.append((den_msb, den, num, base, guarded))
    return mismatches


class TestClass4DivisionLoopGuardIsIdentity(unittest.TestCase):
    """Rewrite class 4: a data-dependent loop start -> a constant start + guard."""

    @classmethod
    def setUpClass(cls):
        cls.lfo = engine_loop_parameters("lfo_vca_engine.sv")
        cls.adsr = engine_loop_parameters("adsr_engine.sv")

    def test_the_geometry_read_from_the_sources_is_the_documented_one(self):
        self.assertEqual(
            (self.lfo["width"], self.lfo["numerator_bits"],
             self.lfo["denominator_bits"], self.lfo["start"]),
            (96, 96, 32, 91))
        self.assertEqual(
            (self.adsr["width"], self.adsr["numerator_bits"],
             self.adsr["denominator_bits"], self.adsr["start"]),
            (142, 142, 47, 106))

    def test_the_constant_start_covers_the_whole_denominator_range(self):
        """The static bound argument: identity needs start >= den_msb_max + 60.

        This bites regardless of operand coverage, which the sweep below does
        not (a too-small constant only shows up on numerators that reach the
        dropped bit).
        """
        for params in (self.lfo, self.adsr):
            with self.subTest(engine=params["engine"]):
                den_msb_max = params["denominator_bits"] - 1
                self.assertGreaterEqual(params["start"],
                                        den_msb_max + QUOTIENT_HEADROOM)

    def test_both_forms_agree_over_the_lfo_denominator_range(self):
        self.assertEqual(compare_loop_forms(self.lfo), [])

    def test_both_forms_agree_over_the_adsr_denominator_range(self):
        self.assertEqual(compare_loop_forms(self.adsr), [])

    def test_the_model_divides_correctly_where_the_answer_is_known(self):
        """Keep the two forms from agreeing because the model is a no-op."""
        for params in (self.lfo, self.adsr):
            with self.subTest(engine=params["engine"]):
                width, den_bits = params["width"], params["denominator_bits"]
                for num, den in ((0, 441), (441, 441), (1000, 441), (2 ** 40, 441),
                                 (12345678901, 999983)):
                    quo, rem = div_half_even_loop(num, den, width=width,
                                                  denominator_bits=den_bits)
                    self.assertEqual((quo, rem), (num // den, num % den),
                                     "%d / %d" % (num, den))
                # Half-even ties both ways on the returned quotient.
                self.assertEqual(div_half_even_tie(
                    *div_half_even_loop(3, 2, width=width, denominator_bits=den_bits),
                    2, width=width), 2)   # 1.5 -> 2 (even)
                self.assertEqual(div_half_even_tie(
                    *div_half_even_loop(5, 2, width=width, denominator_bits=den_bits),
                    2, width=width), 2)   # 2.5 -> 2 (even)

    def test_a_zero_denominator_is_outside_the_contract_but_still_agrees(self):
        """``div_half_even`` documents "a positive denominator"; note the behaviour."""
        for params in (self.lfo, self.adsr):
            with self.subTest(engine=params["engine"]):
                self.assertEqual(
                    compare_loop_forms(params, den_msb_range=[0],
                                       seed=SEED + 1),
                    [])
                width, den_bits = params["width"], params["denominator_bits"]
                self.assertEqual(
                    div_half_even_loop(7, 0, width=width, denominator_bits=den_bits),
                    div_half_even_loop(7, 0, width=width, denominator_bits=den_bits,
                                       start=params["start"]))

    # ---- negative controls -------------------------------------------
    def test_a_shrunk_loop_constant_is_refused(self):
        """The negative control must *shrink*: see the one-sidedness test below."""
        for params in (self.lfo, self.adsr):
            with self.subTest(engine=params["engine"]):
                den_msb_max = params["denominator_bits"] - 1
                mismatches = compare_loop_forms(
                    params, start=params["start"] - 1,
                    den_msb_range=[den_msb_max])
                self.assertTrue(mismatches,
                                "start=%d must not be identity" % (params["start"] - 1))
                # It is the top-of-window numerators that expose it.
                top = den_msb_max + QUOTIENT_HEADROOM
                self.assertTrue(any(num >> top for _m, _d, num, _a, _b in mismatches))

    def test_the_identity_is_one_sided_so_a_grown_constant_cannot_be_the_control(self):
        for params in (self.lfo, self.adsr):
            with self.subTest(engine=params["engine"]):
                self.assertEqual(
                    compare_loop_forms(params, start=params["start"] + 29), [])

    def test_dropping_the_guard_entirely_is_refused(self):
        """A constant start with no guard scans bits the original never did."""
        for params in (self.lfo, self.adsr):
            with self.subTest(engine=params["engine"]):
                width = params["width"]
                den_bits = params["denominator_bits"]
                start = params["start"]
                unguarded = []
                for i in range(start, -1, -1):
                    unguarded.append(i)
                # den_msb = 0 -> the original scans only bits 60..0.
                num = (1 << start) | 1
                den = 1
                base = div_half_even_loop(num, den, width=width,
                                          denominator_bits=den_bits)
                rem = 0
                quo = 0
                mask = (1 << width) - 1
                for i in unguarded:
                    rem = ((rem << 1) | ((num >> i) & 1)) & mask
                    if rem >= den:
                        rem = (rem - den) & mask
                        quo = (quo | (1 << i)) & mask
                self.assertNotEqual(base, (quo, rem))


# ---------------------------------------------------------------------------
# The plan's table must name these tests, and must keep its open caveat
# ---------------------------------------------------------------------------
def sample_identity_rows(plan_text: str) -> list:
    """The four data rows of the plan's sample-identity table, as cell lists."""
    lines = plan_text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("| Rewrite class |"):
            break
    else:
        return []
    rows = []
    for line in lines[index + 2:]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


class TestPlanNamesTheNewChecks(unittest.TestCase):
    """The plan's "Cheapest falsifiable check" column must point here."""

    @classmethod
    def setUpClass(cls):
        cls.plan = PLAN.read_text(encoding="utf-8")
        cls.rows = sample_identity_rows(cls.plan)

    def test_every_rewrite_class_names_its_test(self):
        self.assertEqual(len(self.rows), len(CLASS_TESTS))
        for row, test_class in zip(self.rows, CLASS_TESTS):
            with self.subTest(row=row[0][:28]):
                check = row[-1]
                self.assertIn("tests/test_synth_view_identity.py", check)
                self.assertIn(test_class, check)

    def test_every_named_test_exists_in_this_module(self):
        """So a rename cannot leave the plan pointing at nothing."""
        for test_class in CLASS_TESTS:
            self.assertTrue(issubclass(globals()[test_class], unittest.TestCase),
                            test_class)

    def test_the_plan_still_states_the_yosys_versus_icarus_gap(self):
        lowered = self.plan.lower()
        self.assertIn("yosys", lowered)
        self.assertIn("read_verilog", lowered)
        self.assertIn("**unchecked**", self.plan)
        self.assertIn("the only complete tie is dynamic", lowered)
        # The four static checks must not be dressed up as the dynamic tie.
        for phrase in ("sample-exact on the synthesis view",
                       "sample identity established"):
            self.assertNotIn(phrase, lowered)


if __name__ == "__main__":
    unittest.main()
