#!/usr/bin/env bash
# Reuse the landed runtime, with each pinned source in its own offline container.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
output=${1:-"$repo/out/source-equivalence"}
mkdir -p "$output"
output=$(cd "$output" && pwd)
rm -f "$output/source-equivalence.json"

echo "Building pinned release-era image; logs: $output/build.log" >&2
if ! docker build --platform linux/amd64 --progress plain \
    --iidfile "$output/image.id" -f "$repo/env/release-era/Dockerfile" "$repo" \
    >"$output/build.log" 2>&1; then
    tail -60 "$output/build.log" >&2
    exit 1
fi
image=$(cat "$output/image.id")
docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image" >"$output/image-platform.txt"
docker version --format '{{.Server.Os}}/{{.Server.Arch}} {{.Server.Version}}' >"$output/docker-server.txt"
scratch=$(mktemp -d "$output/source.XXXXXX")
release=$(python3 "$repo/env/release-era/compare_sources.py" prepare --output "$scratch")
python3 "$repo/env/release-era/compare_sources.py" negative --source-root "$release" >"$output/negative.json"
for side in selected release; do
    mkdir -p "$output/$side"
    source=/opt/torchsynth
    if [[ "$side" == release ]]; then source=/release; fi
    echo "Rendering four $side probes in a fresh offline container" >&2
    if ! docker run --rm --platform linux/amd64 --network none \
        --mount "type=bind,src=$release,dst=/release,readonly" \
        --mount "type=bind,src=$output/$side,dst=/output" \
        --entrypoint python "$image" /opt/runner/compare_sources.py render \
        --side "$side" --source-root "$source" \
        >"$output/$side.json" 2>"$output/$side.stderr"; then
        cat "$output/$side.json" "$output/$side.stderr" >&2
        exit 1
    fi
done
python3 "$repo/env/release-era/compare_sources.py" compare --output "$output"
echo "Comparison passed; evidence: $output/source-equivalence.json"
