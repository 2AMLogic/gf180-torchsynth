#!/usr/bin/env python3
"""GUIDED listening-session runner for issue 44 session 01 (zero-command operator).

A single-file, stdlib-only, interactive terminal guide that walks a human
listener through one listening session of the ratified blind listening
protocol (``spec/reference/listening-protocol-config-v1.json``) over the
landed session-01 stimulus package. It is a PRESENTATION LAYER ONLY: every
presentation rule (blinded order = the per-listener order CSV, level
conditions, hidden reference placement, catch trials, MUSHRA blocks, the
independent identity question) comes from the landed protocol artifacts; this
tool redesigns nothing and renders no audio.

Usage::

    python3 tools/listening_session.py [--session-dir DIR] [--listener CODE]

``--session-dir`` defaults to the standard session-01 location
(``/Users/joseph/dev/gf180-listening-sessions/session-01``). If the
per-listener order sheet (``orders/order-<CODE>.csv``) is missing it is
generated with the exact algorithm of the session package's documented helper
(same seed derivation, byte-identical format), so the operator never runs a
second command.

What the listener does (all plain-language, one screen at a time):

1. Playback check: one reference clip plays; a "no" aborts with fix-up
   guidance (headphones, volume, output device) and records nothing.
2. Protocol session anchor: one obvious-difference calibration trial; a fail
   is recorded honestly (``session_anchor_passed: false``) and the protocol
   treats that session as invalidated (replacement rules apply).
3. 3AFC AX trials in order-sheet order: R (the original), then A, then B.
   Q1 "which of A/B was the original?" ([1/2], [n]=no difference, r=replay,
   s=skip-until-end). Q2 is the independent forced identity question "does
   the differing clip still sound like the same patch/voice?" ([y/n]) — AC3;
   recorded separately, never merged with Q1. Catch trials (A=B=R) are
   interleaved per the private order sheet and are NEVER announced,
   summarized, or hinted at.
4. Two MUSHRA-style rating blocks: rate 7 shuffled versions of one clip,
   0-100 (100 = imperceptible), replay always available. Bracket validation
   per the ratified config is what the analysis enforces.
5. Summary: counts (catches stay hidden), where responses live, what happens
   next.

Persistence and resume: every response is written INTO the response-set JSON
(``responses/responses-<CODE>.json`` — the exact contract of
``tools/analyze_listening_responses.py``, schema
``torchsynth-listening-responses``) the moment it is given, via a temp file +
fsync + atomic rename, so a crash can never corrupt it. Restarting skips
every already-answered trial (per-trial bookkeeping rides in an
``order_index``/``item_index`` field the analyzer tolerates). During
collection the file is written with ``unblinded: false`` per the operator
guide; it is flipped only after ALL listener sessions complete.

Audio playback: ``afplay`` on macOS. Fallbacks, in order: ``--player``,
the ``LISTENING_SESSION_PLAYER`` environment variable, then
``afplay`` / ``play`` (SoX) / ``paplay`` / ``aplay``. The player is one
executable invoked with the wav path as its only argument.

Honest anchor note: the ratified MUSHRA anchor is ``clip.round_step`` at
2^-5 on the SAME case. The landed session-01 package rendered that anchor
only on the ``clip.round_step`` cases, so for rating blocks on other
operators no such clip exists; this runner then uses another clearly
degraded rendered clip from the same case (the single-point ``gain.polarity``
binary) as the known bad version and says so in the end-of-session summary.
Bracket validation (the config-enforced check) is unaffected.

Custody: this tool writes ONLY inside the session directory (orders and
responses). Raw responses are private per the ratified ethics default; never
commit or share them. No perceptual claim is made by this tool.

Exit codes: 0 normal (completed, paused, or declined), 2 refusal (missing or
inconsistent session package).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

DEFAULT_SESSION_DIR = Path("/Users/joseph/dev/gf180-listening-sessions/session-01")
FALLBACK_REPO_DIR = Path("/Users/joseph/dev/gf180-torchsynth")
CONFIG_RELATIVE = Path("spec/reference/listening-protocol-config-v1.json")
MANIFEST_NAME = "manifest.json"
RESPONSES_SCHEMA = "torchsynth-listening-responses"
CATCH_CODE = "CATCH(A=B=R)"
SECONDS_PER_TRIAL = 30
SECONDS_PER_MUSHRA_ITEM = 60
ANCHOR_LINK = "https://soundcloud.com/user-357924775/synth1k1"
PLAYER_CANDIDATES = ("afplay", "play", "paplay", "aplay")
PLAY_TIMEOUT_S = 120
TRIALS_PER_BREAK_REMINDER = 20


class SessionRefusal(RuntimeError):
    """Refusal: the session package or a response set is inconsistent."""


class SessionPaused(Exception):
    """Listener quit (Ctrl-C / EOF); everything answered is already saved."""


class PlaybackError(RuntimeError):
    """The configured audio player failed."""


# ---------------------------------------------------------------------------
# Identities and small parsing helpers (mirror tools/listening_protocol_common.py)
# ---------------------------------------------------------------------------


def canonical_bytes(document: Any) -> bytes:
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def config_identity(cfg: Any) -> str:
    return sha256_hex(canonical_bytes(cfg))


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SessionRefusal("cannot read %s: %s" % (path, error)) from error


def locate_repo(explicit: Optional[str]) -> Path:
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if not (candidate / CONFIG_RELATIVE).is_file():
            raise SessionRefusal("--repo %s has no %s" % (candidate, CONFIG_RELATIVE))
        return candidate
    here = Path(__file__).resolve()
    candidates: List[Path] = []
    if here.parent.name == "tools":
        candidates.append(here.parent.parent.resolve())
    candidates.append(FALLBACK_REPO_DIR)
    candidates.append(FALLBACK_REPO_DIR / ".loom" / "worktrees" / "issue-44")
    for candidate in candidates:
        if (candidate / CONFIG_RELATIVE).is_file():
            return candidate.resolve()
    raise SessionRefusal(
        "cannot locate the repo checkout holding %s; pass --repo /path/to/gf180-torchsynth"
        % CONFIG_RELATIVE
    )


# ---------------------------------------------------------------------------
# Session package loading and binding checks
# ---------------------------------------------------------------------------


def load_session(session_dir: Path, repo_dir: Path) -> Dict[str, Any]:
    cfg = read_json(repo_dir / CONFIG_RELATIVE)
    cfg_id = config_identity(cfg)
    manifest = read_json(session_dir / MANIFEST_NAME)
    if manifest.get("schema") != "torchsynth-listening-stimulus-manifest":
        raise SessionRefusal(
            "%s is not a torchsynth-listening-stimulus-manifest"
            % (session_dir / MANIFEST_NAME)
        )
    if manifest.get("status") != "RATIFIED-PROTOCOL-STIMULI":
        raise SessionRefusal(
            "manifest status %r is not RATIFIED-PROTOCOL-STIMULI; this runner only "
            "presents ratified-protocol stimuli" % manifest.get("status")
        )
    bound = (manifest.get("protocol_config") or {}).get("config_identity_sha256")
    if bound != cfg_id:
        raise SessionRefusal(
            "session manifest binds config identity %r but the repo's ratified config "
            "is %r; this session package belongs to a different protocol version"
            % (bound, cfg_id)
        )
    wav_dir = session_dir / "wav"
    if not wav_dir.is_dir():
        raise SessionRefusal("no wav/ directory under %s" % session_dir)
    stimuli = manifest.get("stimuli") or []
    references = manifest.get("references") or []
    if not stimuli or not references:
        raise SessionRefusal("manifest has no stimuli or no references")
    return {
        "session_dir": session_dir,
        "repo_dir": repo_dir,
        "cfg": cfg,
        "cfg_id": cfg_id,
        "manifest": manifest,
        "wav_dir": wav_dir,
        "by_code": {row["stimulus_code"]: row for row in stimuli},
        "ref_by_case": {row["case_id"]: row for row in references},
        "ref_code_by_case": {row["case_id"]: row["stimulus_code"] for row in references},
    }


# ---------------------------------------------------------------------------
# Order sheet (blinded presentation order)
# ---------------------------------------------------------------------------


def parse_order_csv(text: str, source: Path) -> List[Dict[str, Any]]:
    """Parse the documented order-sheet format: index,catch,stimulus_code."""
    rows: List[Dict[str, Any]] = []
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines or lines[0].strip() != "index,catch,stimulus_code":
        raise SessionRefusal("%s: expected header 'index,catch,stimulus_code'" % source)
    for line_no, line in enumerate(lines[1:], 2):
        parts = line.split(",")
        if len(parts) != 3:
            raise SessionRefusal("%s:%d: expected 3 columns" % (source, line_no))
        try:
            index = int(parts[0])
        except ValueError:
            raise SessionRefusal("%s:%d: index is not an integer" % (source, line_no))
        catch = parts[1].strip().lower() == "true"
        rows.append({"index": index, "catch": catch, "code": parts[2].strip()})
    if not rows:
        raise SessionRefusal("%s: no trials" % source)
    if [row["index"] for row in rows] != list(range(1, len(rows) + 1)):
        raise SessionRefusal("%s: trial indices are not contiguous 1..N" % source)
    return rows


def build_trial_list(
    session: Dict[str, Any], listener: str
) -> List[Dict[str, Any]]:
    """The documented order algorithm of the session package's helper: all
    manifest stimuli in manifest order, then int(round(N * catch_rate))
    catch trials, then one seeded shuffle with seed
    "listening-order-v1|<config identity>|<listener>"."""
    catch_rate = session["cfg"]["method"]["abx"]["catch_trial_rate"]
    rng = random.Random("listening-order-v1|%s|%s" % (session["cfg_id"], listener))
    trials: List[Dict[str, Any]] = [
        {"stimulus_code": row["stimulus_code"], "catch": False}
        for row in session["manifest"]["stimuli"]
    ]
    for _ in range(int(round(len(trials) * catch_rate))):
        trials.append({"stimulus_code": CATCH_CODE, "catch": True})
    rng.shuffle(trials)
    return trials


def order_csv_text(trials: Sequence[Dict[str, Any]]) -> str:
    lines = ["index,catch,stimulus_code"]
    lines += [
        "%d,%s,%s" % (i, t["catch"], t["stimulus_code"]) for i, t in enumerate(trials, 1)
    ]
    return "\n".join(lines) + "\n"


def ensure_order(session: Dict[str, Any], listener: str) -> Tuple[List[Dict[str, Any]], bool]:
    """Load orders/order-<listener>.csv, generating it (documented algorithm,
    written for the operator's private records) when absent."""
    orders_dir = session["session_dir"] / "orders"
    order_path = orders_dir / ("order-%s.csv" % listener)
    if order_path.is_file():
        return parse_order_csv(order_path.read_bytes().decode("utf-8"), order_path), False
    trials = build_trial_list(session, listener)
    orders_dir.mkdir(parents=True, exist_ok=True)
    order_path.write_text(order_csv_text(trials))
    rows = parse_order_csv(order_path.read_bytes().decode("utf-8"), order_path)
    return rows, True


# ---------------------------------------------------------------------------
# Presentation helpers (wav resolution, positions, MUSHRA blocks)
# ---------------------------------------------------------------------------


def wav_for_stimulus(session: Dict[str, Any], row: Dict[str, Any]) -> Path:
    """Level-condition-exact wav: the C1 RMS-matched variant when the manifest
    declares C1 for this row, else the native render (C2). Reference rows
    resolve to REF-<case>.wav (references are played native). A declared C1
    file that is missing is a broken package: refuse rather than silently
    change the presentation condition."""
    if row.get("role") == "reference":
        return reference_wav(session, row["case_id"])
    code = row["stimulus_code"]
    wav_dir: Path = session["wav_dir"]
    playback = row.get("playback") or {}
    if (
        playback.get("level_condition") == "C1"
        and playback.get("playback_gain_linear") is not None
    ):
        c1 = wav_dir / ("%s.C1.wav" % code)
        if not c1.is_file():
            raise SessionRefusal(
                "manifest declares level condition C1 for %s but %s is missing; "
                "re-run the session package's render step" % (code, c1.name)
            )
        return c1
    native = wav_dir / ("%s.wav" % code)
    if not native.is_file():
        raise SessionRefusal("stimulus wav missing: %s" % native.name)
    return native


def reference_wav(session: Dict[str, Any], case_id: str) -> Path:
    path = session["wav_dir"] / ("REF-%s.wav" % case_id)
    if not path.is_file():
        raise SessionRefusal("reference wav missing: %s" % path.name)
    return path


def case_of_code(session: Dict[str, Any], code: str) -> Optional[str]:
    row = session["by_code"].get(code)
    if row is not None:
        return row["case_id"]
    for case_id, ref_code in session["ref_code_by_case"].items():
        if ref_code == code:
            return case_id
    return None


def catch_case_id(
    session: Dict[str, Any], rows: Sequence[Dict[str, Any]], position: int
) -> str:
    """Deterministic reference case for a catch trial: the case of the nearest
    preceding real trial, else the nearest following one. Presentation-only."""
    for scan in range(position - 2, -1, -1):
        if not rows[scan]["catch"]:
            case_id = case_of_code(session, rows[scan]["code"])
            if case_id is not None:
                return case_id
    for scan in range(position, len(rows)):
        if not rows[scan]["catch"]:
            case_id = case_of_code(session, rows[scan]["code"])
            if case_id is not None:
                return case_id
    raise SessionRefusal("order sheet has no real trial to anchor a catch trial")


def degraded_position(cfg_id: str, listener: str, index: int) -> int:
    """Which of A/B holds the degraded clip: 0 = A, 1 = B. Deterministic per
    (config identity, listener, trial index), so replays and resumes agree."""
    rng = random.Random("abx-position-v1|%s|%s|%d" % (cfg_id, listener, index))
    return rng.randrange(2)


def mushra_anchor_row(
    session: Dict[str, Any], case_id: str, block_operator: str
) -> Optional[Dict[str, Any]]:
    """The known bad version for a rating block on (block_operator, case).

    1. The ratified anchor (method.mushra.anchor_operator at anchor_magnitude)
       when it is rendered for this case and is not the block operator itself
       (that would duplicate a ladder item).
    2. Otherwise a rendered single-point (binary) degraded clip on the same
       case - clearly objectionable by construction (e.g. gain.polarity).
    Returns None when neither exists (blocks on such cases are refused).
    """
    mushra = session["cfg"]["method"]["mushra"]
    anchor_op = mushra["anchor_operator"]
    anchor_mag = mushra["anchor_magnitude"]
    if anchor_op != block_operator:
        for row in session["manifest"]["stimuli"]:
            if (
                row["operator"] == anchor_op
                and row["case_id"] == case_id
                and row.get("magnitude") is not None
                and float(row["magnitude"]) == float(anchor_mag)
            ):
                return row
    for row in session["manifest"]["stimuli"]:
        if (
            row["operator"] != block_operator
            and row["case_id"] == case_id
            and row.get("ladder_step") is None
            and row.get("magnitude") is None
        ):
            return row
    return None


def mushra_block_specs(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Two rating blocks (method.mushra.items_per_block items each),
    deterministically chosen from the rendered set.

    Preference: clip.saturation_ceiling blocks first (the documented
    suggestion), then other ladder operators alphabetically; distinct cases;
    only cases whose known bad version exists (see mushra_anchor_row).
    """
    n_items = session["cfg"]["method"]["mushra"]["items_per_block"]
    ladders: Dict[str, Dict[str, Dict[int, Dict[str, Any]]]] = {}
    for row in session["manifest"]["stimuli"]:
        step = row.get("ladder_step")
        if step is None:
            continue
        ladders.setdefault(row["operator"], {}).setdefault(row["case_id"], {})[step] = row
    candidates: List[Tuple[int, str, str]] = []
    for op in sorted(ladders):
        for case_id in sorted(ladders[op]):
            if sorted(ladders[op][case_id]) != [1, 2, 3, 4, 5]:
                continue
            if mushra_anchor_row(session, case_id, op) is None:
                continue
            preference = 0 if op == "clip.saturation_ceiling" else 1
            candidates.append((preference, op, case_id))
    candidates.sort()
    blocks: List[Dict[str, Any]] = []
    used_cases: Set[str] = set()
    for _preference, op, case_id in candidates:
        if case_id in used_cases or len(blocks) == 2:
            continue
        used_cases.add(case_id)
        anchor = mushra_anchor_row(session, case_id, op)
        assert anchor is not None
        items = [session["ref_by_case"][case_id]] + [
            ladders[op][case_id][step] for step in (1, 2, 3, 4, 5)
        ] + [anchor]
        if len(items) != n_items:
            raise SessionRefusal(
                "rating block on (%s, %s) has %d items, the config declares %d"
                % (op, case_id, len(items), n_items)
            )
        blocks.append(
            {
                "block_id": "M%d" % (len(blocks) + 1),
                "case_id": case_id,
                "operator": op,
                "items": items,
                "anchor_substituted": anchor["operator"]
                != session["cfg"]["method"]["mushra"]["anchor_operator"],
            }
        )
    if len(blocks) < 2:
        raise SessionRefusal(
            "the landed session package supports only %d rating block(s) with a known "
            "bad version; the protocol declares 2. No ratings are invented - extend "
            "the rendered stimulus set first." % len(blocks)
        )
    return blocks


def mushra_item_order(cfg_id: str, listener: str, block_id: str, n: int) -> List[int]:
    rng = random.Random("mushra-order-v1|%s|%s|%s" % (cfg_id, listener, block_id))
    order = list(range(n))
    rng.shuffle(order)
    return order


# ---------------------------------------------------------------------------
# Response-set document (exact analyzer contract)
# ---------------------------------------------------------------------------


def build_responses_doc(session: Dict[str, Any], listener: str) -> Dict[str, Any]:
    condition_map: List[Dict[str, Any]] = []
    for row in session["manifest"]["stimuli"]:
        condition_map.append(
            {
                "stimulus_code": row["stimulus_code"],
                "case_id": row["case_id"],
                "operator": row["operator"],
                "ladder_step": row["ladder_step"],
                "audio_sha256": row["audio"]["sha256"],
            }
        )
    for row in session["manifest"]["references"]:
        condition_map.append(
            {
                "stimulus_code": row["stimulus_code"],
                "case_id": row["case_id"],
                "operator": None,
                "ladder_step": None,
                "audio_sha256": row["corpus"]["audio_sha256"],
            }
        )
    return {
        "schema": RESPONSES_SCHEMA,
        "schema_version": 1,
        "protocol_config_sha256": session["cfg_id"],
        "unblinded": False,
        "condition_map": condition_map,
        "listeners": [
            {
                "listener_code": listener,
                "session_anchor_passed": None,
                "abx_trials": [],
                "catch_trials": [],
                "mushra_blocks": [],
            }
        ],
    }


def responses_path(session_dir: Path, listener: str) -> Path:
    return session_dir / "responses" / ("responses-%s.json" % listener)


def load_responses_doc(session: Dict[str, Any], listener: str) -> Optional[Dict[str, Any]]:
    path = responses_path(session["session_dir"], listener)
    if not path.is_file():
        return None
    doc = read_json(path)
    if doc.get("schema") != RESPONSES_SCHEMA or doc.get("schema_version") != 1:
        raise SessionRefusal("%s is not a %s v1 response set" % (path, RESPONSES_SCHEMA))
    if doc.get("protocol_config_sha256") != session["cfg_id"]:
        raise SessionRefusal(
            "%s binds a different protocol config; refusing to mix sessions" % path
        )
    listeners = doc.get("listeners") or []
    if len(listeners) != 1 or listeners[0].get("listener_code") != listener:
        raise SessionRefusal("%s does not hold listener %r alone" % (path, listener))
    return doc


def atomic_write_json(path: Path, doc: Dict[str, Any]) -> None:
    """Crash-safe persist: write tmp, flush + fsync, atomic rename, fsync dir."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(canonical_bytes(doc) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def answered_order_indices(doc: Dict[str, Any]) -> Set[int]:
    answered: Set[int] = set()
    listener = doc["listeners"][0]
    for key in ("abx_trials", "catch_trials"):
        for row in listener.get(key) or []:
            if "order_index" in row:
                answered.add(row["order_index"])
    return answered


def rated_mushra_items(doc: Dict[str, Any]) -> Set[Tuple[str, str]]:
    rated: Set[Tuple[str, str]] = set()
    for block in doc["listeners"][0].get("mushra_blocks") or []:
        for rating in block.get("ratings") or []:
            rated.add((block.get("block_id"), rating.get("stimulus_code")))
    return rated


# ---------------------------------------------------------------------------
# Audio playback
# ---------------------------------------------------------------------------


def resolve_player(explicit: Optional[str]) -> List[str]:
    candidates: List[str] = []
    if explicit:
        candidates.append(explicit)
    env_player = os.environ.get("LISTENING_SESSION_PLAYER")
    if env_player:
        candidates.append(env_player)
    candidates.extend(PLAYER_CANDIDATES)
    for name in candidates:
        found = shutil.which(name)
        if found:
            return [found]
    raise SessionRefusal(
        "no audio player found (tried: %s). Install one, or pass --player /path/to/player "
        "(or set LISTENING_SESSION_PLAYER). The player is invoked with the wav path as "
        "its only argument." % ", ".join(candidates)
    )


def play_clip(player: Sequence[str], path: Path) -> None:
    try:
        result = subprocess.run(
            list(player) + [str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PLAY_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as error:
        raise PlaybackError(
            "player %s timed out playing %s" % (player[0], path.name)
        ) from error
    except OSError as error:
        raise PlaybackError(
            "player %s failed to start: %s" % (player[0], error)
        ) from error
    if result.returncode != 0:
        raise PlaybackError(
            "player %s exited %d playing %s"
            % (player[0], result.returncode, path.name)
        )


# ---------------------------------------------------------------------------
# The guided session
# ---------------------------------------------------------------------------


class GuidedSession:
    def __init__(
        self,
        session: Dict[str, Any],
        listener: str,
        order: Sequence[Dict[str, Any]],
        player: Sequence[str],
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self.session = session
        self.listener = listener
        self.order = list(order)
        self.player = list(player)
        self.input_fn = input_fn
        self.out = output_fn
        self.doc: Optional[Dict[str, Any]] = None
        self.skipped: List[int] = []
        self.trials_done = 0
        self.mushra_specs = mushra_block_specs(session)
        self.anchor_case_id = self._pick_anchor_case()
        self.anchor_row = self._pick_anchor_stimulus()
        self.anchor_substituted = any(
            block["anchor_substituted"] for block in self.mushra_specs
        )

    # -- basic io -----------------------------------------------------------

    def ask(self, prompt: str) -> str:
        try:
            answer = self.input_fn(prompt)
        except EOFError:
            raise SessionPaused()
        except KeyboardInterrupt:
            raise SessionPaused()
        return answer.strip()

    def ask_choice(self, prompt: str, valid: Sequence[str]) -> str:
        while True:
            answer = self.ask(prompt).lower()
            if answer in valid:
                return answer
            self.out("  Please answer one of: %s" % ", ".join(valid))

    def play(self, path: Path) -> None:
        try:
            play_clip(self.player, path)
        except PlaybackError as error:
            self.out("")
            self.out("AUDIO PROBLEM: %s" % error)
            self.out("Test the file manually:  %s '%s'" % (self.player[0], path))
            raise SessionPaused()

    # -- persistence --------------------------------------------------------

    def save(self) -> None:
        assert self.doc is not None
        atomic_write_json(
            responses_path(self.session["session_dir"], self.listener), self.doc
        )

    def listener_entry(self) -> Dict[str, Any]:
        return self.doc["listeners"][0]

    # -- plan ---------------------------------------------------------------

    def _pick_anchor_case(self) -> str:
        anchor_op = self.session["cfg"]["method"]["mushra"]["anchor_operator"]
        for row in sorted(
            self.session["manifest"]["stimuli"], key=lambda r: r["case_id"]
        ):
            if row["operator"] == anchor_op:
                return row["case_id"]
        raise SessionRefusal("no rendered clip of the anchor operator; cannot calibrate")

    def _pick_anchor_stimulus(self) -> Dict[str, Any]:
        mushra = self.session["cfg"]["method"]["mushra"]
        for row in self.session["manifest"]["stimuli"]:
            if (
                row["operator"] == mushra["anchor_operator"]
                and row["case_id"] == self.anchor_case_id
                and row.get("magnitude") is not None
                and float(row["magnitude"]) == float(mushra["anchor_magnitude"])
            ):
                return row
        raise SessionRefusal(
            "anchor-magnitude stimulus missing for case %s" % self.anchor_case_id
        )

    # -- flow ---------------------------------------------------------------

    def run(self, order_created: bool) -> int:
        total = len(self.order)
        minutes = (
            total * SECONDS_PER_TRIAL
            + len(self.mushra_specs)
            * len(self.mushra_specs[0]["items"])
            * SECONDS_PER_MUSHRA_ITEM
        ) // 60
        self.out("")
        self.out(
            "This session compares recordings of your synthesizer against slightly-broken copies of them."
        )
        self.out(
            "Your job: listen, and tell us what you hear - no expertise needed, just your ears."
        )
        self.out(
            "Plan on about %d minutes: %d quick listen-and-answer trials (~30 s each) plus %d short rating rounds."
            % (minutes, total, len(self.mushra_specs))
        )
        self.out(
            "You can quit any time (Ctrl-C) and resume later - every answer is saved the moment you make it."
        )
        self.out(
            "First, calibrate your ears with the author's reference recording: %s" % ANCHOR_LINK
        )
        if self.listener.upper().startswith("PILOT"):
            self.out("Pilot session: results are harness validation only (not evidence).")
        if order_created:
            self.out(
                "(A fresh randomized play order was generated for listener %s.)" % self.listener
            )

        self.doc = load_responses_doc(self.session, self.listener)
        if self.doc is None:
            self.doc = build_responses_doc(self.session, self.listener)

        self.out("")
        self.out("Headphones on, in a quiet room, at a comfortable fixed volume.")
        if not self._playback_check():
            return 0
        self._hardware_note()
        if not self._session_anchor():
            return 0
        answered_before = answered_order_indices(self.doc)
        pending = [row for row in self.order if row["index"] not in answered_before]
        self._run_trials(pending, total)
        if self.skipped:
            self._revisit_skipped(total)
        unanswered = total - len(answered_order_indices(self.doc))
        self._run_mushra_blocks()
        self._summary(total, unanswered)
        return 0

    def _playback_check(self) -> bool:
        ref_path = reference_wav(self.session, self.anchor_case_id)
        self.out("")
        self.out("Let's check your sound setup with one short clip of the untouched original.")
        self.play(ref_path)
        if self.ask_choice("Did that sound play OK? [y/n]: ", ("y", "n")) == "y":
            return True
        self.out("")
        self.out("No problem - let's fix the sound first; nothing was recorded.")
        self.out("  1. Check the volume and the selected output device.")
        self.out("  2. Test the clip manually:  %s '%s'" % (self.player[0], ref_path))
        self.out("  3. Start this session again when you can hear the clip.")
        return False

    def _hardware_note(self) -> None:
        note = self.ask(
            "Optional - describe your setup for the session notes "
            "(headphones/interface/room; Enter to skip): "
        )
        if note:
            notes_path = self.session["session_dir"] / "responses" / (
                "hardware-%s.txt" % self.listener
            )
            notes_path.parent.mkdir(parents=True, exist_ok=True)
            notes_path.write_text(note + "\n")
            self.out("(Saved to %s - kept private with your responses.)" % notes_path.name)

    def _session_anchor(self) -> bool:
        entry = self.listener_entry()
        if entry.get("session_anchor_passed") is not None:
            return True
        ref_path = reference_wav(self.session, self.anchor_case_id)
        degraded_path = wav_for_stimulus(self.session, self.anchor_row)
        first_is_degraded = degraded_position(self.session["cfg_id"], self.listener, 0) == 0
        self.out("")
        self.out("Calibration check (not part of the real trials).")
        self.out(
            "You'll hear R (the original), then A, then B. One of A/B has an obvious distortion."
        )
        answer = self._play_abc_and_ask(
            ref_path,
            degraded_path if first_is_degraded else ref_path,
            ref_path if first_is_degraded else degraded_path,
            "Which clip was modified - [1]=A, [2]=B?",
            allow_no_difference=False,
        )
        passed = (answer == "1") == first_is_degraded
        entry["session_anchor_passed"] = passed
        self.save()
        if passed:
            self.out("Calibration recorded. Starting the real trials.")
            return True
        self.out("")
        self.out("The calibration answer was off. Under the protocol this session would be")
        self.out("invalidated and replaced. You can continue anyway (the failed calibration")
        self.out("is recorded) or stop here - nothing else was recorded.")
        return self.ask_choice("Continue anyway? [y/n]: ", ("y", "n")) == "y"

    def _play_abc_and_ask(
        self,
        ref_path: Path,
        a_path: Path,
        b_path: Path,
        prompt: str,
        allow_no_difference: bool,
        allow_skip: bool = False,
    ) -> str:
        valid = ["1", "2"] + (["n"] if allow_no_difference else [])
        valid += ["s"] if allow_skip else []
        valid += ["r", "q"]
        while True:
            self.out("")
            self.out("  Playing R (original), then A, then B ...")
            self.play(ref_path)
            self.play(a_path)
            self.play(b_path)
            answer = self.ask_choice("  %s [r]=replay all three, [q]=quit: " % prompt, tuple(valid))
            if answer != "r":
                return answer

    def _run_trials(self, pending: Sequence[Dict[str, Any]], total: int) -> None:
        for row in pending:
            if row["index"] in answered_order_indices(self.doc):
                continue
            self._one_trial(row, total)

    def _one_trial(self, row: Dict[str, Any], total: int) -> None:
        remaining = total - len(answered_order_indices(self.doc)) + len(self.skipped)
        minutes = max(0, remaining * SECONDS_PER_TRIAL // 60)
        self.out("")
        self.out("Trial %d of %d - about %d min left" % (row["index"], total, minutes))
        degraded_position_value: Optional[int] = None
        if row["catch"]:
            case_id = catch_case_id(self.session, self.order, row["index"])
            ref_path = reference_wav(self.session, case_id)
            a_path = b_path = ref_path
        else:
            stim = self.session["by_code"].get(row["code"])
            if stim is None:
                raise SessionRefusal(
                    "order sheet references unknown stimulus code %r" % row["code"]
                )
            ref_path = reference_wav(self.session, stim["case_id"])
            degraded_path = wav_for_stimulus(self.session, stim)
            degraded_position_value = degraded_position(
                self.session["cfg_id"], self.listener, row["index"]
            )
            a_path = degraded_path if degraded_position_value == 0 else ref_path
            b_path = ref_path if degraded_position_value == 0 else degraded_path
        # The screen is identical for real trials and catch trials: the
        # listener is never told (nor hinted) which kind this is.
        answer = self._play_abc_and_ask(
            ref_path,
            a_path,
            b_path,
            "Q1: which of A/B was the ORIGINAL - [1]=A, [2]=B, or [n]=no difference?",
            allow_no_difference=True,
            allow_skip=True,
        )
        if answer == "q":
            raise SessionPaused()
        if answer == "s":
            self.out("  (We'll come back to this one at the end.)")
            if row["index"] not in self.skipped:
                self.skipped.append(row["index"])
            return
        entry = self.listener_entry()
        if row["catch"]:
            entry["catch_trials"].append(
                {
                    "stimulus_code": CATCH_CODE,
                    "false_positive": answer in ("1", "2"),
                    "order_index": row["index"],
                }
            )
        else:
            assert degraded_position_value is not None
            # Q1 asked which of A/B was the original; a correct pick locates
            # the degraded interval too (the other slot).
            detected = answer != "n" and (answer == "1") == (degraded_position_value == 1)
            same = self.ask_choice(
                "  Q2: Does the differing clip still sound like the same patch/voice? [y/n]: ",
                ("y", "n"),
            )
            entry["abx_trials"].append(
                {
                    "stimulus_code": row["code"],
                    "detected": detected,
                    "identity_same_patch": same == "y",
                    "order_index": row["index"],
                }
            )
        self.save()
        if row["index"] in self.skipped:
            self.skipped.remove(row["index"])
        self.trials_done += 1
        if self.trials_done % TRIALS_PER_BREAK_REMINDER == 0:
            self.out(
                "(%d trials done - consider a short break; the session resumes when you press Enter.)"
                % self.trials_done
            )
            self.ask("")

    def _revisit_skipped(self, total: int) -> None:
        self.out("")
        self.out("Now the %d trial(s) you set aside earlier." % len(self.skipped))
        still: List[int] = []
        for index in list(self.skipped):
            self._one_trial(self.order[index - 1], total)
            if index in self.skipped:
                still.append(index)
        self.skipped = still

    # -- MUSHRA-style rating blocks ------------------------------------------

    def _run_mushra_blocks(self) -> None:
        rated = rated_mushra_items(self.doc)
        for block in self.mushra_specs:
            order = mushra_item_order(
                self.session["cfg_id"],
                self.listener,
                block["block_id"],
                len(block["items"]),
            )
            pending = [
                pos
                for pos in order
                if (block["block_id"], block["items"][pos]["stimulus_code"]) not in rated
            ]
            if pending:
                self._one_mushra_block(block, pending)

    def _one_mushra_block(self, block: Dict[str, Any], pending: Sequence[int]) -> None:
        self.out("")
        self.out(
            "Rating round %s of %d - a different kind of task."
            % (block["block_id"], len(self.mushra_specs))
        )
        self.out(
            "You'll rate %d shuffled versions of the same clip: how GOOD does each one sound?"
            % len(block["items"])
        )
        self.out(
            "100 = perfect/imperceptible, 0 = terrible. Replay any version as often as you like."
        )
        low, high = self.session["cfg"]["method"]["mushra"]["scale"]
        to_rate = list(pending)
        passes = 0
        while to_rate and passes < 6:
            passes += 1
            if passes > 1:
                self.out("")
                self.out("Still to rate: %d version(s) you set aside." % len(to_rate))
            deferred: List[int] = []
            for pos in to_rate:
                item = block["items"][pos]
                path = wav_for_stimulus(self.session, item)
                self.out("")
                self.out("Version %d of %d:" % (pos + 1, len(block["items"])))
                self.play(path)
                answer = self._mushra_rating_prompt(pos, len(block["items"]), low, high, path)
                if answer == "s":
                    deferred.append(pos)
                    continue
                try:
                    value = int(answer)
                except ValueError:
                    value = -1
                if not (low <= value <= high):
                    self.out("  Please enter a whole number from %d to %d." % (low, high))
                    deferred.append(pos)
                    continue
                self._record_mushra_rating(block, item, value, pos + 1)
            to_rate = deferred
        if to_rate:
            self.out("Some versions are still unrated; they are left unanswered.")

    def _mushra_rating_prompt(
        self, pos: int, n_items: int, low: int, high: int, path: Path
    ) -> str:
        while True:
            answer = self.ask(
                "  Rating for version %d (%d-%d, r = replay, s = rate it later): "
                % (pos + 1, low, high)
            ).lower()
            if answer != "r":
                return answer
            self.play(path)

    def _record_mushra_rating(
        self, block: Dict[str, Any], item: Dict[str, Any], value: int, item_index: int
    ) -> None:
        entry = self.listener_entry()
        holder = None
        for candidate in entry["mushra_blocks"]:
            if candidate["block_id"] == block["block_id"]:
                holder = candidate
                break
        if holder is None:
            holder = {"block_id": block["block_id"], "ratings": []}
            entry["mushra_blocks"].append(holder)
        holder["ratings"].append(
            {
                "stimulus_code": item["stimulus_code"],
                "rating": value,
                "item_index": item_index,
            }
        )
        self.save()

    # -- summary ------------------------------------------------------------

    def _summary(self, total: int, unanswered_trials: int) -> None:
        entry = self.listener_entry()
        n_abx = len(entry["abx_trials"])
        n_catch = len(entry["catch_trials"])
        n_ratings = sum(len(block.get("ratings") or []) for block in entry["mushra_blocks"])
        path = responses_path(self.session["session_dir"], self.listener)
        self.out("")
        self.out("Session complete - thank you!")
        self.out(
            "Answers recorded: %d trials (%d comparison answers) + %d rating round(s) (%d ratings)."
            % (n_abx + n_catch, n_abx, len(entry["mushra_blocks"]), n_ratings)
        )
        if unanswered_trials > 0:
            self.out(
                "%d trial(s) were left unanswered; the analysis simply sees no response for them."
                % unanswered_trials
            )
        self.out("Your responses: %s" % path)
        self.out("They stay private on this machine (never commit or share raw responses).")
        self.out(
            "The file is marked 'not unblinded'; it is flipped only after every listener has finished."
        )
        if self.anchor_substituted:
            self.out(
                "Note: the standardized low-quality anchor clip was not rendered for some rating"
            )
            self.out(
                "blocks in this session package; another clearly-degraded clip from the same case"
            )
            self.out("was used as the known bad version (the bracket check is unaffected).")
        self.out(
            "What happens next: responses stay private; analysis turns them into ladder validation."
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_guided_session(
    session: Dict[str, Any],
    listener: str,
    player: Sequence[str],
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    order, order_created = ensure_order(session, listener)
    guided = GuidedSession(
        session, listener, order, player, input_fn=input_fn, output_fn=output_fn
    )
    try:
        return guided.run(order_created)
    except SessionPaused:
        output_fn("")
        output_fn("Paused - everything answered so far is already saved.")
        output_fn(
            "Start the same command again with listener %r to pick up where you left off."
            % listener
        )
        return 0


def sanitize_listener(code: str) -> str:
    if not code or not all(c.isalnum() or c in "-_" for c in code):
        raise SessionRefusal(
            "listener code %r must use only letters, digits, '-' and '_' (it names files)"
            % code
        )
    return code


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Guided listening-session runner (issue 44, session 01)"
    )
    parser.add_argument(
        "--session-dir",
        default=str(DEFAULT_SESSION_DIR),
        help="session package directory (default: the standard session-01 location)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="repo checkout holding the ratified protocol config (auto-detected)",
    )
    parser.add_argument(
        "--listener", default=None, help="listener code, e.g. L1 (else prompted)"
    )
    parser.add_argument(
        "--player",
        default=None,
        help="audio player executable (default: afplay, then play/paplay/aplay; "
        "env LISTENING_SESSION_PLAYER also works)",
    )
    args = parser.parse_args(argv)
    try:
        session_dir = Path(args.session_dir).expanduser().resolve()
        if not (session_dir / MANIFEST_NAME).is_file():
            raise SessionRefusal(
                "%s does not look like a session package (no %s)"
                % (session_dir, MANIFEST_NAME)
            )
        repo_dir = locate_repo(args.repo)
        session = load_session(session_dir, repo_dir)
        listener = args.listener
        if listener is None:
            existing = sorted(
                p.name[len("order-"):-len(".csv")]
                for p in (session_dir / "orders").glob("order-*.csv")
            )
            hint = ", ".join(existing) if existing else "none yet"
            listener = input(
                "Listener code for this session (existing sheets: %s) [L1]: " % hint
            )
            listener = listener.strip() or "L1"
        listener = sanitize_listener(listener)
        player = resolve_player(args.player)
        return run_guided_session(session, listener, player)
    except SessionRefusal as refusal:
        print("REFUSED: %s" % refusal, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("")
        print("Interrupted - everything answered so far is already saved.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
