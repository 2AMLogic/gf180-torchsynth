"""Tests for tools/listening_session.py (the guided listening-session runner).

Stdlib-only; mocked playback (the player is a no-op callable) and no real
audio anywhere. The guided flow is exercised with dependency-injected
input/output functions against a synthetic session package built on the REAL
ratified protocol config; compatibility with tools/analyze_listening_responses.py
is asserted by running that analyzer on the runner's output.

No listening has occurred; no perceptual claim is made or tested.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import listening_protocol_common as common  # noqa: E402
import listening_session as runner  # noqa: E402
import analyze_listening_responses as analyzer  # noqa: E402

TRUE_PLAYER = shutil.which("true")
RATIFIED_CONFIG_PATH = ROOT / "spec" / "reference" / "listening-protocol-config-v1.json"
REAL_SESSION_DIR = Path(
    "/Users/joseph/dev/gf180-listening-sessions/session-01"
)


def ratified_config() -> dict:
    return json.loads(RATIFIED_CONFIG_PATH.read_bytes().decode("utf-8"))


def stimulus_row(cfg_id: str, case_id: str, operator: str, step, c1: bool = True) -> dict:
    code = common.stimulus_code(cfg_id, case_id, operator, step)
    magnitude = None
    if step is not None:
        magnitude = ratified_config()["ladders"][operator]["points"][step - 1]
    return {
        "stimulus_code": code,
        "role": "degraded",
        "case_id": case_id,
        "operator": operator,
        "ladder_step": step,
        "magnitude": magnitude,
        "audio": {"sha256": "a" * 64, "sample_count": 176400},
        "playback": {
            "level_condition": "C1" if c1 else "C2",
            "playback_gain_linear": 1.0 if c1 else 1.0,
        },
    }


def reference_row(cfg_id: str, case_id: str) -> dict:
    return {
        "stimulus_code": common.stimulus_code(cfg_id, case_id, "", None),
        "role": "reference",
        "case_id": case_id,
        "operator": None,
        "ladder_step": None,
        "corpus": {"audio_sha256": "b" * 64},
    }


def make_session(tmp: Path, c2_operator: str = "gain.db") -> tuple:
    """A synthetic session package bound to the real ratified config.

    Two ladder operators on distinct cases, each with a rendered gain.polarity
    binary (the same-case known bad version -> MUSHRA anchor substitution),
    plus a clip.round_step L5 row for the calibration anchor.
    """
    cfg = ratified_config()
    cfg_id = common.config_identity(cfg)
    rows = []
    ladders = [
        ("clip.saturation_ceiling", "global-48"),
        ("gain.dc_offset", "global-88"),
    ]
    for operator, case_id in ladders:
        for step in (1, 2, 3, 4, 5):
            rows.append(stimulus_row(cfg_id, case_id, operator, step))
        rows.append(stimulus_row(cfg_id, case_id, "gain.polarity", None))
    rows.append(stimulus_row(cfg_id, "global-12", "clip.round_step", 5))
    rows.append(stimulus_row(cfg_id, "global-12", c2_operator, 1, c1=False))
    references = [reference_row(cfg_id, case[1]) for case in ladders]
    references.append(reference_row(cfg_id, "global-12"))
    manifest = {
        "schema": "torchsynth-listening-stimulus-manifest",
        "schema_version": 1,
        "status": "RATIFIED-PROTOCOL-STIMULI",
        "protocol_config": {"config_identity_sha256": cfg_id},
        "stimuli": rows,
        "references": references,
    }
    session_dir = tmp / "session-01"
    (session_dir / "wav").mkdir(parents=True)
    (session_dir / "manifest.json").write_bytes(common.canonical_bytes(manifest) + b"\n")
    wav_dir = session_dir / "wav"
    for row in rows:
        code = row["stimulus_code"]
        if row["playback"]["level_condition"] == "C1":
            (wav_dir / ("%s.C1.wav" % code)).write_bytes(b"")
        (wav_dir / ("%s.wav" % code)).write_bytes(b"")
    for row in references:
        (wav_dir / ("REF-%s.wav" % row["case_id"])).write_bytes(b"")
    return cfg, cfg_id, manifest, session_dir


def build_session_dict(cfg: dict, cfg_id: str, manifest: dict, session_dir: Path) -> dict:
    return runner.load_session(session_dir, ROOT)


class Driver:
    """Scripted stdin for the guided flow; records everything printed."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.printed = []

    def input_fn(self, prompt):
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def output_fn(self, text):
        self.printed.append(text)


class TestOrderSheets(unittest.TestCase):
    def test_parse_and_validation(self):
        text = "index,catch,stimulus_code\n1,False,ls1-a\n2,True,CATCH(A=B=R)\n"
        rows = runner.parse_order_csv(text, Path("memory"))
        self.assertEqual(
            [(r["index"], r["catch"], r["code"]) for r in rows],
            [(1, False, "ls1-a"), (2, True, "CATCH(A=B=R)")],
        )
        with self.assertRaises(runner.SessionRefusal):
            runner.parse_order_csv("bad,header,here\n1,False,x\n", Path("memory"))
        with self.assertRaises(runner.SessionRefusal):
            runner.parse_order_csv(
                "index,catch,stimulus_code\n2,False,x\n", Path("memory")
            )

    def test_generated_order_is_deterministic_and_matches_helper_format(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg, cfg_id, manifest, session_dir = make_session(tmp)
            session = build_session_dict(cfg, cfg_id, manifest, session_dir)
            rows_a, created_a = runner.ensure_order(session, "L1")
            rows_b, created_b = runner.ensure_order(session, "L1")
            self.assertTrue(created_a)
            self.assertFalse(created_b)
            self.assertEqual(rows_a, rows_b)
            n = len(manifest["stimuli"])
            n_catch = int(round(n * cfg["method"]["abx"]["catch_trial_rate"]))
            self.assertEqual(len(rows_a), n + n_catch)
            self.assertEqual(
                sum(1 for r in rows_a if r["catch"]),
                n_catch,
            )
            # Same seed derivation as the session helper: same listener and
            # config identity must reproduce the real committed order sheet.
            real_order = REAL_SESSION_DIR / "orders" / "order-L1.csv"
            if real_order.is_file():
                real_manifest = json.loads(
                    (REAL_SESSION_DIR / "manifest.json").read_bytes().decode("utf-8")
                )
                real_session = {
                    "cfg_id": real_manifest["protocol_config"]["config_identity_sha256"],
                    "cfg": ratified_config(),
                    "manifest": real_manifest,
                }
                trials = runner.build_trial_list(real_session, "L1")
                self.assertEqual(
                    runner.order_csv_text(trials), real_order.read_text()
                )

    def test_catch_case_resolution(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg, cfg_id, manifest, session_dir = make_session(tmp)
            session = build_session_dict(cfg, cfg_id, manifest, session_dir)
            rows, _ = runner.ensure_order(session, "L1")
            for position, row in enumerate(rows, 1):
                if row["catch"]:
                    case_id = runner.catch_case_id(session, rows, position)
                    self.assertIsNotNone(case_id)
                    self.assertTrue(case_id.startswith("global-"))


class TestPositionsAndWavs(unittest.TestCase):
    def test_degraded_position_is_deterministic(self):
        a = runner.degraded_position("cfg", "L1", 7)
        b = runner.degraded_position("cfg", "L1", 7)
        other = runner.degraded_position("cfg", "L1", 8)
        self.assertEqual(a, b)
        self.assertIn(a, (0, 1))
        self.assertIn(other, (0, 1))

    def test_level_condition_exactness(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg, cfg_id, manifest, session_dir = make_session(tmp)
            session = build_session_dict(cfg, cfg_id, manifest, session_dir)
            by_code = session["by_code"]
            c1_row = next(
                r for r in manifest["stimuli"] if r["playback"]["level_condition"] == "C1"
            )
            self.assertEqual(
                runner.wav_for_stimulus(session, c1_row).name,
                "%s.C1.wav" % c1_row["stimulus_code"],
            )
            c2_row = next(
                r for r in manifest["stimuli"] if r["playback"]["level_condition"] == "C2"
            )
            self.assertEqual(
                runner.wav_for_stimulus(session, c2_row).name,
                "%s.wav" % c2_row["stimulus_code"],
            )
            # A declared C1 variant that is missing is a broken package.
            (session["wav_dir"] / ("%s.C1.wav" % c1_row["stimulus_code"])).unlink()
            with self.assertRaises(runner.SessionRefusal):
                runner.wav_for_stimulus(session, c1_row)
            del by_code


class TestMushraBlocks(unittest.TestCase):
    def test_two_blocks_with_known_bad_version_and_substitution_flag(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg, cfg_id, manifest, session_dir = make_session(tmp)
            session = build_session_dict(cfg, cfg_id, manifest, session_dir)
            blocks = runner.mushra_block_specs(session)
            self.assertEqual(len(blocks), 2)
            n_items = cfg["method"]["mushra"]["items_per_block"]
            seen_cases = set()
            for block in blocks:
                self.assertEqual(len(block["items"]), n_items)
                self.assertNotIn(block["case_id"], seen_cases)
                seen_cases.add(block["case_id"])
                roles = [item.get("role") for item in block["items"]]
                self.assertEqual(roles.count("reference"), 1)
                self.assertEqual(roles.count("degraded"), n_items - 1)
                # The ratified clip.round_step anchor is not rendered on these
                # cases; the same-case binary stands in and is flagged.
                self.assertTrue(block["anchor_substituted"])
            self.assertEqual(blocks[0]["operator"], "clip.saturation_ceiling")

    def test_item_order_is_deterministic_permutation(self):
        order_a = runner.mushra_item_order("cfg", "L1", "M1", 7)
        order_b = runner.mushra_item_order("cfg", "L1", "M1", 7)
        self.assertEqual(sorted(order_a), list(range(7)))
        self.assertEqual(order_a, order_b)


class TestResponseContract(unittest.TestCase):
    def test_doc_shape_matches_analyzer_required_keys(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg, cfg_id, manifest, session_dir = make_session(tmp)
            session = build_session_dict(cfg, cfg_id, manifest, session_dir)
            doc = runner.build_responses_doc(session, "L1")
            self.assertEqual(
                set(doc),
                {
                    "schema",
                    "schema_version",
                    "protocol_config_sha256",
                    "unblinded",
                    "condition_map",
                    "listeners",
                },
            )
            self.assertEqual(doc["schema"], runner.RESPONSES_SCHEMA)
            self.assertFalse(doc["unblinded"])
            self.assertEqual(doc["protocol_config_sha256"], cfg_id)
            # Analyzer-side: codes must re-derive from the config.
            index = analyzer._condition_index(cfg, cfg_id, doc["condition_map"])
            self.assertTrue(index)

    def test_atomic_write_is_crash_safe(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg, cfg_id, manifest, session_dir = make_session(tmp)
            session = build_session_dict(cfg, cfg_id, manifest, session_dir)
            doc = runner.build_responses_doc(session, "L1")
            path = runner.responses_path(session_dir, "L1")
            runner.atomic_write_json(path, doc)
            self.assertEqual(json.loads(path.read_bytes().decode("utf-8")), doc)
            # A crash mid-write leaves at most a tmp file next to a valid one.
            tmp_file = path.with_name(path.name + ".tmp")
            tmp_file.write_bytes(b'{"half": ')
            runner.atomic_write_json(path, doc)
            self.assertFalse(tmp_file.exists())
            self.assertEqual(json.loads(path.read_bytes().decode("utf-8")), doc)


def full_run_answers(session: dict, listener: str, include_anchor: bool = True):
    """Scripted answers for one complete guided session on the synthetic package.

    include_anchor=False for a resumed run whose calibration was already
    recorded (the runner then skips the calibration question)."""
    answers = ["y", ""]  # playback check, hardware note (skipped)
    if include_anchor:
        # The seeded calibration position is knowable; answer it correctly so
        # the "continue anyway?" branch (fail path) never fires mid-script.
        first_is_degraded = runner.degraded_position(session["cfg_id"], listener, 0) == 0
        answers.append("1" if first_is_degraded else "2")
    order, _ = runner.ensure_order(session, listener)
    for row in order:
        if row["catch"]:
            answers.append("n")
        else:
            answers.extend(["1", "y"])
    for _ in range(14):
        answers.append("50")  # 2 blocks x 7 ratings
    return answers


class TestGuidedFlow(unittest.TestCase):
    def test_complete_session_writes_analyzer_compatible_responses(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg, cfg_id, manifest, session_dir = make_session(tmp)
            session = build_session_dict(cfg, cfg_id, manifest, session_dir)
            driver = Driver(full_run_answers(session, "LT"))
            code = runner.run_guided_session(
                session,
                "LT",
                [TRUE_PLAYER],
                input_fn=driver.input_fn,
                output_fn=driver.output_fn,
            )
            self.assertEqual(code, 0)
            doc = runner.load_responses_doc(session, "LT")
            entry = doc["listeners"][0]
            n = len(manifest["stimuli"])
            n_catch = int(round(n * cfg["method"]["abx"]["catch_trial_rate"]))
            self.assertEqual(len(entry["abx_trials"]), n)
            self.assertEqual(len(entry["catch_trials"]), n_catch)
            answered = runner.answered_order_indices(doc)
            self.assertEqual(len(answered), n + n_catch)
            self.assertEqual(
                entry["session_anchor_passed"] in (True, False), True
            )
            ratings = [
                r
                for block in entry["mushra_blocks"]
                for r in block["ratings"]
            ]
            self.assertEqual(len(ratings), 14)
            # Analyzer compatibility: unblind (the post-collection step) and run it.
            doc["unblinded"] = True
            analysis = analyzer.analyze(cfg, cfg_id, doc)
            operators = {row["operator"] for row in analysis["detection_rows"]}
            self.assertIn("clip.saturation_ceiling", operators)
            self.assertIn("gain.dc_offset", operators)
            block_ids = {
                row["block_id"] for row in analysis["mushra_rows"] if "block_id" in row
            }
            self.assertIn("M1", block_ids)
            self.assertIn("M2", block_ids)
            # File-level acceptance too (exact top-level key set enforcement).
            path = runner.responses_path(session_dir, "LT")
            doc_on_disk = json.loads(path.read_bytes().decode("utf-8"))
            doc_on_disk["unblinded"] = True
            runner.atomic_write_json(path, doc_on_disk)
            loaded = analyzer._load_responses(path, cfg_id)
            self.assertEqual(loaded["listeners"][0]["listener_code"], "LT")

    def test_resume_skips_answered_trials(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg, cfg_id, manifest, session_dir = make_session(tmp)
            session = build_session_dict(cfg, cfg_id, manifest, session_dir)
            answers = ["y", ""]
            first_is_degraded = runner.degraded_position(session["cfg_id"], "LR", 0) == 0
            answers.append("1" if first_is_degraded else "2")  # calibration, correct
            order, _ = runner.ensure_order(session, "LR")
            first_two = order[:2]
            for row in first_two:
                if row["catch"]:
                    answers.append("n")
                else:
                    answers.extend(["2", "n"])
            driver = Driver(answers)
            code = runner.run_guided_session(
                session,
                "LR",
                [TRUE_PLAYER],
                input_fn=driver.input_fn,
                output_fn=driver.output_fn,
            )
            self.assertEqual(code, 0)  # paused, saved
            doc = runner.load_responses_doc(session, "LR")
            answered_first = runner.answered_order_indices(doc)
            self.assertEqual(answered_first, {1, 2})
            # Restart: same command, remaining answers (calibration already recorded).
            driver2 = Driver(full_run_answers(session, "LR", include_anchor=False))
            code = runner.run_guided_session(
                session,
                "LR",
                [TRUE_PLAYER],
                input_fn=driver2.input_fn,
                output_fn=driver2.output_fn,
            )
            self.assertEqual(code, 0)
            doc = runner.load_responses_doc(session, "LR")
            answered = sorted(runner.answered_order_indices(doc))
            self.assertEqual(answered, list(range(1, len(order) + 1)))
            # No duplicated rows for the resumed trials.
            abx_indices = [r["order_index"] for r in doc["listeners"][0]["abx_trials"]]
            self.assertEqual(len(abx_indices), len(set(abx_indices)))

    def test_skip_defers_trial_to_the_end(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg, cfg_id, manifest, session_dir = make_session(tmp)
            session = build_session_dict(cfg, cfg_id, manifest, session_dir)
            order, _ = runner.ensure_order(session, "LS")
            answers = ["y", ""]
            first_is_degraded = runner.degraded_position(session["cfg_id"], "LS", 0) == 0
            answers.append("1" if first_is_degraded else "2")  # calibration, correct
            deferred_seen = False
            for position, row in enumerate(order):
                if position == 0:
                    answers.append("s")  # skip the first trial
                    deferred_seen = True
                elif row["catch"]:
                    answers.append("n")
                else:
                    answers.extend(["1", "y"])
            self.assertTrue(deferred_seen)
            # The revisited trial comes back at the end: a catch needs one
            # answer, a real trial needs Q1+Q2.
            if order[0]["catch"]:
                answers.append("n")
            else:
                answers.extend(["1", "y"])
            answers.extend(["50"] * 20)  # 14 ratings + slack
            driver = Driver(answers)
            code = runner.run_guided_session(
                session,
                "LS",
                [TRUE_PLAYER],
                input_fn=driver.input_fn,
                output_fn=driver.output_fn,
            )
            self.assertEqual(code, 0)
            doc = runner.load_responses_doc(session, "LS")
            self.assertEqual(
                len(runner.answered_order_indices(doc)), len(order)
            )

    def test_binding_refusal_on_wrong_config(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg, cfg_id, manifest, session_dir = make_session(tmp)
            manifest["protocol_config"]["config_identity_sha256"] = "f" * 64
            (session_dir / "manifest.json").write_bytes(
                common.canonical_bytes(manifest) + b"\n"
            )
            with self.assertRaises(runner.SessionRefusal):
                build_session_dict(cfg, cfg_id, manifest, session_dir)

    def test_responses_file_refusal_on_listener_mismatch(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg, cfg_id, manifest, session_dir = make_session(tmp)
            session = build_session_dict(cfg, cfg_id, manifest, session_dir)
            doc = runner.build_responses_doc(session, "LX")
            doc["listeners"][0]["listener_code"] = "LZ"  # hand-mangled file
            runner.atomic_write_json(runner.responses_path(session_dir, "LX"), doc)
            with self.assertRaises(runner.SessionRefusal):
                runner.load_responses_doc(session, "LX")


if __name__ == "__main__":
    unittest.main()
