#!/usr/bin/env bash
# Two independent canonical/scalar process pairs, then three start-red controls.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
sentinel=""
mkl_compatible=""
# DR-0009 amendment A1 (issue 151): the dispatch environment the workflow job
# declares must reach the render container, not stop at the runner shell, and
# it must be bound into the receipt as actually set: forward each pin that is
# set; leave a declared runtime that does not set a pin (for example the
# native profile) byte-for-byte as declared, with the unset values recorded
# as null in runtime/math_environment.
while [[ ${1:-} == --* ]]; do
    case $1 in
        --sentinel) sentinel=--sentinel ;;
        --mkl-compatible) mkl_compatible=1 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done
math_options=()
for name in ATEN_CPU_CAPABILITY MKL_ENABLE_INSTRUCTIONS ONEDNN_MAX_CPU_ISA MKL_CBWR; do
    value="${!name-}"
    if [[ -n "${value}" ]]; then
        math_options+=(-e "${name}=${value}")
    fi
done
if [[ -n "${mkl_compatible:-}" && " ${math_options[*]-} " != *"-e MKL_CBWR="* ]]; then
    # The diagnostic profile (SCALAR-EXECUTION.md) always bounds MKL.
    math_options+=(-e MKL_CBWR=COMPATIBLE)
fi
output=${1:-"$repo/out/scalar-$(date -u +%Y%m%dT%H%M%SZ)"}
if [[ -e "$output" ]]; then
    echo "Refusing existing output directory (preserve each actual run): $output" >&2
    exit 1
fi
mkdir -p "$output"
output=$(cd "$output" && pwd)
python3 -S -c 'import uuid; print(uuid.uuid4())' >"$output/campaign.id"
campaign=$(cat "$output/campaign.id")

echo "Building pinned linux/amd64 runtime; output: $output" >&2
# --progress is deliberately omitted: the CI pool's docker (BuildKit) and a
# stock Ubuntu docker.io legacy builder must both run this unmodified, and
# the progress format is log cosmetics only.
if ! docker build --platform linux/amd64 \
    --iidfile "$output/image.id" -f "$repo/env/release-era/Dockerfile" "$repo" \
    >"$output/build.log" 2>&1; then
    tail -60 "$output/build.log" >&2
    exit 1
fi
image=$(cat "$output/image.id")
docker version --format '{{.Server.Os}}/{{.Server.Arch}} {{.Server.Version}}' >"$output/docker-server.txt"

render() {
    docker run --rm --platform linux/amd64 --network none --cpus 1 --memory 3g \
        -e OMP_NUM_THREADS=1 -e MKL_NUM_THREADS=1 -e OPENBLAS_NUM_THREADS=1 \
        -e VECLIB_MAXIMUM_THREADS=1 -e PYTHONDONTWRITEBYTECODE=1 \
        ${math_options[@]+"${math_options[@]}"} \
        --mount "type=bind,src=$repo,dst=/workspace,readonly" \
        --mount "type=bind,src=$output,dst=/output" \
        --entrypoint python "$image" /workspace/env/release-era/qualify_scalar.py \
        render ${sentinel:+"$sentinel"} --campaign-id "$campaign" "$@"
}

for run in 1 2; do
    echo "Fresh process pair $run/2" >&2
    render --side canonical --repeat "$run" --output "/output/run-$run/canonical" \
        >"$output/canonical-$run.stdout" 2>"$output/canonical-$run.stderr"
    render --side scalar --repeat "$run" --canonical "/output/run-$run/canonical" \
        --output "/output/run-$run/scalar" \
        >"$output/scalar-$run.stdout" 2>"$output/scalar-$run.stderr"
done

for mutation in wrong-parameter wrong-noise fresh-randomization; do
    echo "Start-red control: $mutation (must exit 1)" >&2
    rc=0
    render --side scalar --repeat 1 --canonical /output/run-1/canonical \
        --output "/output/controls/$mutation" --mutation "$mutation" \
        >"$output/$mutation.stdout" 2>"$output/$mutation.stderr" || rc=$?
    if [[ $rc != 1 ]]; then
        echo "Control returned $rc, expected 1: $mutation" >&2
        exit 1
    fi
done

python3 -S "$repo/env/release-era/qualify_scalar.py" aggregate \
    --output "$output" ${sentinel:+"$sentinel"}
echo "Evidence: $output/scalar-execution.json"
