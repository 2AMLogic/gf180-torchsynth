"""Shared scaffolding for the signal and timing family workers (#429).

Family-owned adjacent module of ``env/release-era/mutation_worker.py``
(Python 3.9, release image ``release-mkl-compatible-v1``). The bridge worker
stays byte-identical; ``mutation_worker_signal.py`` and
``mutation_worker_timing.py`` previously each carried a line-for-line copy of
the harness, attempt and case-process glue. It lives here once, parameterized
by the family's session class, schedule validator, schedule error class and
file-label rule. Stdlib plus ``mutation_worker`` only; no import from ``src/``.
"""

from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutation_worker as base  # noqa: E402


def file_label(name, replace_spaces=False):
    label = name.replace(":", "__")
    return label.replace(" ", "_") if replace_spaces else label


class FamilyHarness(base.Harness):
    """The shared deterministic harness, bound to one resolved development case.

    Subclasses set ``schedule_error`` to the family's refusal exception.
    """

    schedule_error = ValueError

    def __init__(self, capture, document, plan, torch, Voice, SynthConfig, normalize, case):
        base.Harness.__init__(self, capture, document, plan, torch, Voice, SynthConfig, normalize)
        coordinates = base.qualification.coordinates(case["global_sound_index"], base.BATCH_SIZE)
        if not (
            case["batch_size"] == base.BATCH_SIZE
            and coordinates["batch"] == case["batch_block"]
            and coordinates["slot"] == case["batch_slot"]
        ):
            raise self.schedule_error("case coordinates differ from the reproducible batch coordinates")
        self.coordinates = coordinates
        self.case = dict(case)


class FamilyAttempt(base.Attempt):
    """One bounded attempt bound to its case; retains traces and consumed replacements.

    Subclasses set ``session_class`` and ``label_spaces``.
    """

    session_class = None
    label_spaces = False

    def run(self, output):
        harness = self.harness
        torch = harness.torch
        coordinates = harness.coordinates
        slot = coordinates["slot"]
        if self.schedule is not None:
            if not (
                self.schedule["campaign"]["case"] == harness.case
                and self.schedule["campaign"]["attempt"] == self.name
            ):
                raise harness.schedule_error(
                    "attempt is not bound to its schedule case and label: " + self.name
                )
        voice = harness.make_voice()
        rng_before = harness.rng_state()
        parameters_before = harness.capture.named_parameters(voice, torch, slot)
        noise_before = base.tensor_bytes(voice.noise.noise[coordinates["noise_slot"]], torch)
        session = None
        capture = None
        started = time.perf_counter()
        errored = None
        try:
            with torch.inference_mode():
                capture = harness.make_capture(voice)
                if self.schedule is None:
                    with capture:
                        audio, forward, labels = voice(coordinates["batch"])
                else:
                    session = self.session_class(self.schedule, voice, capture, torch)
                    session.attach()
                    try:
                        audio, forward, labels = voice(coordinates["batch"])
                    finally:
                        if session._attached:
                            session.detach(*sys.exc_info())
        except BaseException as error:
            errored = type(error).__name__ + ": " + str(error)
        elapsed = time.perf_counter() - started
        state_restored = self._state_restored(voice, parameters_before, noise_before)
        label = file_label(self.name, self.label_spaces)
        common = {
            "attempt": self.name,
            "case": harness.case["id"],
            "plan_id": None if self.schedule is None else self.schedule["plan_id"],
            "events": session.events if session is not None else [],
            "state_restored": state_restored,
            "render_seconds": elapsed,
        }
        if errored is not None:
            return dict(common, errored=errored)
        rng_after = harness.rng_state()
        audio_bytes = base.tensor_bytes(audio, torch)
        raw = output / (label + ".audio.f32le")
        raw.write_bytes(base.tensor_bytes(audio[slot], torch))
        retained = {}
        for name, value in capture.values.items():
            path = output / (label + ".trace." + name + ".f32le")
            path.write_bytes(base.tensor_bytes(value, torch))
            retained[name] = path.name
        consumed = []
        for instance_id, trace, data in (session.consumed if session is not None else []):
            path = output / (label + ".consumed." + instance_id + "." + trace + ".f32le")
            path.write_bytes(data)
            consumed.append(
                {"instance_id": instance_id, "trace": trace, "slot": slot,
                 "sha256": base.sha256(data), "file": path.name}
            )
        return dict(
            common,
            errored=None,
            audio_slot_sha256=base.sha256(base.tensor_bytes(audio[slot], torch)),
            batch_audio_sha256=base.sha256(audio_bytes),
            slot_sha256=base.slot_digests(audio_bytes),
            audio_slot_file=raw.name,
            labels_all=bool(labels.all()),
            shapes_ok=tuple(audio.shape) == (base.BATCH_SIZE, base.SAMPLES)
            and tuple(forward.shape) == (base.BATCH_SIZE, 78)
            and tuple(labels.shape) == (base.BATCH_SIZE,),
            capture_inventory=[dict(item) for item in capture.inventory],
            invocation_counts=dict(capture.tracker.counts),
            parameters_before_sha256=parameters_before["normalized"]["batch_sha256_by_name"],
            parameters_selected_sha256=parameters_before["normalized"]["selected_sha256_by_name"],
            noise_sha256=base.sha256(noise_before),
            rng_state=rng_after,
            render_drew_randoms=rng_before != rng_after,
            retained_traces=retained,
            consumed_replacements=consumed,
        )



def run_case_process(
    harness, schedules, output, document, parameter_names, manifest_sha256,
    validate, attempt_class, error_class,
):
    """Plain, empty, sham, faults in manifest order, clean rerun; validation first."""

    def require(condition, reason):
        if not condition:
            raise error_class(reason)

    names = list(schedules)
    require(names[:2] == ["empty", "sham"], "controls must precede the faulted attempts")
    for name in names:
        validate(schedules[name], document, parameter_names, harness.case, manifest_sha256)
        require(schedules[name]["campaign"]["attempt"] == name, "schedule label mismatch: " + name)
    records = {}
    for name, schedule in [("plain", None)] + [(n, schedules[n]) for n in names] + [
        ("clean_rerun", None)
    ]:
        records[name] = attempt_class(name, harness, schedule).run(output)
    return records
