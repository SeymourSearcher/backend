#!/usr/bin/env bash
# Runs scripts/bench_compute.py in the test image: pinned numpy/scipy and the
# real app/static/*.npy, rather than whatever the host happens to have.
set -euo pipefail

cd "$(dirname "$0")/.."

docker build --target test -t seymour-backend-test . >/dev/null

MSYS_NO_PATHCONV=1 exec docker run --rm \
    -v "//$(pwd | sed 's#^/\([a-zA-Z]\)/#\1/#'):/bench" -w /bench \
    seymour-backend-test python scripts/bench_compute.py "$@"
