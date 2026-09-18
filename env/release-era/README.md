# Release-era CPU environment

From the repository root, with Docker (BuildKit) and a host `python3` available:

```sh
./env/release-era/run.sh
```

This builds the pinned **linux/amd64** image, then runs two fresh containers
with networking disabled. Each validates the selected source, checks a
deliberately changed source in a separate process, runs the upstream RNG
sentinel, and renders global sound indices **0** and **39942**. Any failed
build, import, sentinel, shape/finite/name check, or unequal repeat exits
nonzero. A failed run keeps its actual JSON error and stderr; it is not a pass.

Results go to `out/release-era/` (ignored by git). Pass a different output
directory as the sole argument to keep a separate run. `environment.json`
contains image/platform identity, definition hashes, exact installed package
versions, source hashes, named-parameter/audio hashes, warnings, and both run
outcomes. `build.log`, `run-1.json`, `run-2.json`, and their stderr files retain
local diagnostics. Each `run-N/` contains two raw little-endian float32 mono
audio files and two JSON files of normalized/physical parameters keyed by
canonical `module.parameter` names. Nothing clips or normalizes the output
after upstream `Voice` returns. No generated audio is committed.

The checked-in bounded execution record is
[`sim/reference/release-era-environment.json`](../../sim/reference/release-era-environment.json).
It qualifies this environment only; **canonical-runtime ratification is
downstream**. The 32-row reproducible batch selects a global fixture identity
(39942 is batch 1248, slot 6). It does not impose hardware batching: the
hardware product remains one four-second sound per trigger. Batch-1,
`reproducible=False`, broad repeat/batch qualification, source A/B, and
cross-environment comparisons belong to their separate issues.

## Version selection and immutable inputs

The [v1.0.2 setup.py](https://github.com/torchsynth/torchsynth/blob/115af79de4021408e05cc0785b9710e0982d0ce6/setup.py)
requires Python >=3.7, NumPy, SciPy, PyTorch >=1.8, and
`pytorch-lightning>=1.4`; it does **not** provide an exact historical lock.
This is a practical reconstruction, not a claim to recover the authors'
original installed environment.

| Component | Choice | Reason |
| --- | --- | --- |
| Python | 3.9.13, Debian bullseye slim | Available at the August 2022 release; digest pins the amd64 base and all inherited OS packages. No apt install/upgrade occurs. |
| PyTorch | 1.12.1+cpu, CPython 3.9 Linux x86_64 wheel | August 2022 CPU numerical stack; direct wheel URL and SHA-256 pin. |
| NumPy / SciPy | 1.23.2 / 1.9.0 | August 2022 numerical dependencies. |
| Lightning | 1.8.6 (December 2022) | The selected source imports `LightningModule` from `lightning`; the release's `pytorch-lightning` package alone cannot satisfy that import. The late-2022 unified package works without a source patch or module alias. |
| setuptools | 65.5.1 | Meets the selected source's explicit minimum and provides its `pkg_resources` import. |
| Other Python packages | Fully resolved in `requirements.lock` | Resolution cutoff 2023-01-01; all versions and distribution hashes retained, including extras and pip/wheel. |

There are 72 locked Python distributions. The first import probe exposed an
undeclared `websockets` dependency in Lightning 1.8.6; `websockets==10.4` is
therefore an explicit input. The binary-only historical resolution selects
FastAPI 0.58.1 because later releases' `all` extras require a
`python-multipart` release without a wheel at the cutoff. That older API
successfully imports the selected Voice. These app dependencies do not run
an app/server during this probe.

The normative source is still
[`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`](https://github.com/torchsynth/torchsynth/tree/2b0964d4c6c3d472a2a0d54d91b408caaeffca6d).
Its archive is SHA-256 pinned separately from the base image and packages.
`probe.py` checks every file in both `files` and `source_checkout_only_files`
from `spec/reference/upstream.json` **before importing TorchSynth**. The
archive is used directly as a source tree: no installation hooks, source
patches, nebula changes, or root project install are needed. The root Python
requirement and modern `uv.lock` are independent and unchanged.

The upstream `check_for_reproducibility()` function runs unmodified. Each
render checks 176400 finite samples, the complete set of 78 parameter names
against the default nebula, finite normalized/physical values, correspondence
between named parameters and the returned positional tensor, and train/test
identity. Hashes of parameter maps use UTF-8 JSON with sorted keys, compact
separators, and `allow_nan=False`; parameter file hashes additionally include
the trailing newline. Audio hashes cover the raw little-endian float32 bytes.

## Platform boundaries

Only **linux/amd64** is supported by this image/wheel definition. On an ARM
Docker server, Docker must provide x86 emulation; the script always requests
`--platform linux/amd64` for both build and run. It never falls back to a
native ARM wheel or a newer PyTorch. Native ARM execution, GPU execution,
other CPU instruction sets, and identical output across platforms are
unqualified. The evidence distinguishes the host, Docker server, and observed
container architecture. Emulation can make installation/rendering slower.

Python warnings (including old dependency deprecations), captured library
stdout/stderr, Docker stderr, and build warning lines are retained. The image
contains historical dependencies for this offline probe; it starts no
service. Network access is needed to fetch the pinned base, wheels, and
source archive at build time. Package registries must continue serving those
exact bytes or the build fails. Only the base image has a registry digest;
the resulting local image ID is recorded per build, not promised identical
across builders.

## Focused checks and lock maintenance

Normal project checks remain free of Torch/Docker dependencies. Additional
preflight tests also use only the standard library:

```sh
python3 -m unittest discover -s env/release-era -v
python3 -m unittest discover -s tests -v
python3 tools/check_contract.py
```

The lock was resolved with `uv 0.6.11` using the following command (lock
maintenance only; **not** part of reproduction). Inspect and requalify any
lock change. Preserving extras avoids historical pip treating an extras
dependency as a new unpinned requirement during hash enforcement.

```sh
uv pip compile env/release-era/requirements.in \
  --python-version 3.9 --python-platform x86_64-manylinux_2_31 \
  --exclude-newer 2023-01-01 --only-binary :all: --generate-hashes \
  --no-strip-extras --no-annotate --no-header \
  --output-file env/release-era/requirements.lock
```

For a build with no reusable Docker layers, first run
`docker build --no-cache --platform linux/amd64 -f env/release-era/Dockerfile .`,
then run the reproduction command. Do not regenerate the lock simply to run
the probe.
