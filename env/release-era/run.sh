#!/usr/bin/env bash
# Build the pinned amd64 image and qualify it in two fresh, offline containers.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
output=${1:-"$repo/out/release-era"}
mkdir -p "$output"
output=$(cd "$output" && pwd)
# A failed re-run must not leave an earlier aggregate success report in place.
rm -f "$output/environment.json"

echo "Building linux/amd64 release-era environment; logs: $output/build.log" >&2
if ! docker build --platform linux/amd64 --progress plain \
    --iidfile "$output/image.id" -f "$repo/env/release-era/Dockerfile" "$repo" \
    >"$output/build.log" 2>&1; then
    tail -60 "$output/build.log" >&2
    exit 1
fi
image=$(cat "$output/image.id")
docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image" >"$output/image-platform.txt"
docker version --format '{{.Server.Os}}/{{.Server.Arch}} {{.Server.Version}}' >"$output/docker-server.txt"

for run in 1 2; do
    mkdir -p "$output/run-$run"
    echo "Rendering indices 0 and 39942 in fresh container $run/2" >&2
    if ! docker run --rm --platform linux/amd64 --network none \
        --mount "type=bind,src=$output/run-$run,dst=/output" "$image" \
        >"$output/run-$run.json" 2>"$output/run-$run.stderr"; then
        cat "$output/run-$run.json" "$output/run-$run.stderr" >&2
        exit 1
    fi
done

python3 - "$repo" "$output" <<'PY'
import datetime
import hashlib
import json
from pathlib import Path
import platform
import sys

repo, output = map(Path, sys.argv[1:])
runs = [json.loads((output / ("run-%d.json" % n)).read_text()) for n in (1, 2)]
for n, run in enumerate(runs, 1):
    run["container_stderr"] = (output / ("run-%d.stderr" % n)).read_text()
if not all(run["status"] == "passed" for run in runs):
    raise SystemExit("A container did not pass; inspect run reports")
same = runs[0]["sounds"] == runs[1]["sounds"]
definitions = {}
for path in sorted((repo / "env/release-era").iterdir()):
    if path.is_file():
        definitions[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
evidence = {
    "schema_version": 1,
    "recorded_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "purpose": "release-era CPU environment smoke qualification; no canonical-runtime ratification",
    "command": "./env/release-era/run.sh",
    "host": {"system": platform.system(), "machine": platform.machine()},
    "docker_server": (output / "docker-server.txt").read_text().strip(),
    "image": {"id": (output / "image.id").read_text().strip(),
              "platform": (output / "image-platform.txt").read_text().strip(),
              "base": (repo / "env/release-era/Dockerfile").read_text().splitlines()[0][5:]},
    "definition_sha256": definitions,
    "build_log_sha256": hashlib.sha256((output / "build.log").read_bytes()).hexdigest(),
    "build_warnings": [line for line in (output / "build.log").read_text().splitlines()
                       if "warning" in line.lower()],
    "fresh_container_count": 2,
    "two_run_sound_records_equal": same,
    "limitations": [
        "Only linux/amd64 is supported by this definition; ARM hosts require working x86 emulation.",
        "Python/PyTorch/NumPy/SciPy are release-era; Lightning 1.8.6 (December 2022) supplies the selected source import.",
        "The 32-row reproducible batch is a qualification fixture protocol, not a hardware batching requirement.",
        "No cross-environment, broad repeat/batch, scalar-mode, hardware, or fidelity conclusion.",
    ],
    "runs": runs,
}
(output / "environment.json").write_text(json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) + "\n")
print("Two fresh containers passed; sound records equal: %s" % same)
print("Evidence: " + str(output / "environment.json"))
if not same:
    raise SystemExit("Repeat differed; retained both actual outcomes")
PY
