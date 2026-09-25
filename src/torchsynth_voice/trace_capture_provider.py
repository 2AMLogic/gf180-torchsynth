"""Shared container-driver capture provider for the release-era image.

The single home for the ``ProviderSession``/``ProviderFactory`` pair that the
container-side drivers in ``tools/qualify_trace_artifacts.py`` and
``tools/capture_float_sources.py`` attach around their one Voice call. Both
drivers are written to ``/output/driver.py`` and executed by the release image's
Python 3.9 against a read-only ``/repo`` mount, so -- exactly as for
``trace_capture`` -- no module-level Torch import and no syntax newer than 3.9
may appear here. The Torch and ``torchsynth.util.normalize_if_clipping``
imports stay deferred inside ``ProviderFactory.__call__``, so importing this
module never imports Torch.

This lives beside ``trace_capture`` rather than inside it on purpose.
``src/torchsynth_voice/trace_capture.py`` is a digest-pinned input of three
committed evidence publications -- ``sim/reference/mutation-framework-v1.json``,
``mutation-runtime-v1.json`` and ``mutation-timing-v1.json`` (see
``INPUT_PATHS``/``STALENESS_INPUTS`` in the matching ``tools/qualify_mutations*``
tools). Those publications are regenerated only by the DR-0006 host-gated run,
and nothing they execute reaches this provider: it is used by the two container
drivers alone, never by ``mutation_runtime`` or the release-era workers. Folding
driver-only code into ``trace_capture`` would therefore both invalidate the
three publications and permanently bind their input digests to code they never
run.
"""

from __future__ import annotations

import hashlib

try:
    from . import trace_capture
except ImportError:
    # The container drivers import this file directly on Python 3.9, with
    # /repo/src/torchsynth_voice on sys.path and no package context.
    import trace_capture


class ProviderSession:
    """One ``TraceCapture`` session serialized into container-side payloads.

    Wraps the session so the driver receives, on clean exit, the original
    binary32 bytes of every retained trace keyed by trace name, each
    serialization re-hashed against the digest the capture recorded.
    """

    def __init__(self, factory, session, torch):
        self.factory = factory
        self.session = session
        self.torch = torch

    def __enter__(self):
        self.session.__enter__()
        return self.factory.payloads

    def __exit__(self, exc_type, exc, tb):
        result = self.session.__exit__(exc_type, exc, tb)
        if exc_type is None:
            for item in self.session.inventory:
                name = item["name"]
                data = trace_capture.tensor_bytes(
                    self.session.values[name], self.torch
                )
                if hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise ValueError(
                        "serialized bytes disagree with capture: " + name
                    )
                self.factory.descriptors[name] = item
                self.factory.payloads[name] = data
        return result


class ProviderFactory:
    """Worker capture provider binding ``TraceCapture`` to one render call.

    The container drivers pass an instance as the worker's
    ``capture_provider``; the Torch and ``normalize_if_clipping`` imports stay
    inside ``__call__`` so importing this module never imports Torch.
    """

    def __init__(self, document, requested, batch_size):
        self.document = document
        self.requested = requested
        self.batch_size = batch_size
        self.payloads = {}
        self.descriptors = {}

    def __call__(self, request, voice, slot):
        import torch
        from torchsynth.util import normalize_if_clipping

        session = trace_capture.TraceCapture(
            voice,
            self.document,
            torch,
            normalize_if_clipping,
            names=self.requested,
            slot=slot,
            batch_size=self.batch_size,
        )
        return ProviderSession(self, session, torch)
