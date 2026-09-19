#!/usr/bin/env bash
# Locked release runtime, offline workers; never mutates the source or current venv.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
mode=${1:-sentinel}
if [[ $# -gt 0 ]]; then shift; fi
case "$mode" in matrix|sentinel) ;; *) echo 'Expected matrix or sentinel' >&2; exit 2 ;; esac
mkdir -p "$repo/out"
build_dir=$(mktemp -d "$repo/out/repeatability-build.XXXXXX")
echo "Pinned image build logs: $build_dir/build.log" >&2
docker build --platform linux/amd64 --progress plain --iidfile "$build_dir/image.id" \
    -f "$repo/env/release-era/Dockerfile" "$repo" >"$build_dir/build.log" 2>&1 || {
    cat "$build_dir/build.log" >&2; exit 1;
}
image=$(cat "$build_dir/image.id")
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
python3 "$repo/env/release-era/qualify_repeatability.py" "$mode" --image "$image" "$@"
