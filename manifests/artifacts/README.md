# `manifests/artifacts/` — stored-evidence manifests

One file per stored run: `<bench>-<run-id>.manifest.json`, written by
`tools/store_evidence.py` (issue #409). Each entry names one object in the fleet
artifact store by sha256, with `size`, `kind` (`evidence`), `producer`, an
`artifact:<key>` `uri`, and `name`, a relative path with no `..` (by convention
the scratch-relative path of the file). The format is klayout-tools'
`docs/cli/artifact.md`.

This directory is unrelated to `manifests/gf180-torchsynth.signoff.json`, the
`klt signoff` manifest.

Evidence bytes are private; manifests are public. Never write a bucket, account
id, storage-bucket URI or broker URL here. Manifests are append-only: do not edit or
delete one. New runs only; evidence and golden vectors already committed under
`sim/` stay where they are.

`python tools/check_artifact_manifests.py check` (also run by
`tests/test_evidence_manifests.py` in CI) checks schema, `artifact:` URIs, and
that no stored object's bytes are also committed. It never contacts the broker:
schema-valid is not hash-verified. To verify bytes run
`klt artifact verify <manifest>` with `KLT_ARTIFACT_BROKER` set.
